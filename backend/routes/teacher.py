from datetime import datetime, timedelta, timezone
import hashlib
import json
import random

from flask import Blueprint, jsonify, request, g, current_app
from sqlalchemy import and_, func, or_

from ..models import (
    Test,
    TestResult,
    User,
    Question,
    TestQuestion,
    AnswerLog,
    Classroom,
    ClassroomStudent,
    TestAssignment,
    School,
    db,
)
from ..security import require_auth, role_required
from ..validation import validate_required_fields, sanitize_string
from ..ai_topic_service import (
    generate_topic_mcqs,
    get_topic_ai_service_url,
)
from ..at_risk_predictor import get_at_risk_predictions_for_students

teacher_bp = Blueprint("teacher", __name__)

VALID_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_GRADES = {"elementary", "middle", "high", "college"}


def _utcnow():
    # Keep UTC semantics while storing naive datetimes used by existing models.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clamp_int(raw, default_value, min_value, max_value):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default_value
    return max(min_value, min(max_value, value))


def _parse_bool_flag(raw_value, default=False):
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return raw_value != 0
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            return True
        if value in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _parse_days_arg(default_value=30):
    return _clamp_int(request.args.get("days", default_value), default_value, 1, 3650)


def _parse_iso_datetime(raw_value):
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(str(raw_value))
    except (TypeError, ValueError):
        return None


def _teacher_student_filter(teacher, grade=None):
    # Only apply grade filtering when explicitly requested by caller.
    # Default behavior should expose all students in the teacher's school scope.
    effective_grade = sanitize_string(grade, max_length=32) if grade else None
    query = User.query.filter(
        User.role == "student",
        User.school_id == teacher.school_id,
    )
    if effective_grade:
        query = query.filter(User.grade == effective_grade)
    return query, effective_grade


def _ensure_teacher_school(teacher):
    """Guarantee teacher has a school_id for classroom-scoped flows."""
    bound_teacher = User.query.filter_by(id=teacher.id).first()
    if not bound_teacher:
        return None

    if bound_teacher.school_id:
        if teacher.school_id != bound_teacher.school_id:
            teacher.school_id = bound_teacher.school_id
        return bound_teacher.school_id

    slug = f"teacher-{teacher.id}-school"
    school = School.query.filter_by(slug=slug).first()
    if not school:
        school = School(
            name=f"Teacher {teacher.id} Workspace",
            slug=slug,
        )
        db.session.add(school)
        db.session.flush()

    bound_teacher.school_id = school.id
    teacher.school_id = school.id
    db.session.flush()
    return school.id


def _get_teacher_classroom_or_404(teacher_id, classroom_id):
    classroom = Classroom.query.filter_by(id=classroom_id, teacher_id=teacher_id).first()
    if not classroom:
        return None, (jsonify({"error": "Classroom not found"}), 404)
    return classroom, None


def _normalize_question_payload(item, subject, grade, difficulty, topic):
    text = sanitize_string(item.get("text"), max_length=4000)
    options = item.get("options") if isinstance(item.get("options"), list) else []
    options = [sanitize_string(str(opt), max_length=300) for opt in options][:6]
    options = [opt for opt in options if opt]

    if len(options) < 2:
        return None

    try:
        correct_index = int(item.get("correct_index", 0))
    except (TypeError, ValueError):
        correct_index = 0
    if correct_index < 0 or correct_index >= len(options):
        correct_index = 0

    return {
        "subject": subject,
        "grade": grade,
        "difficulty": difficulty,
        "text": text,
        "options": options,
        "correct_index": correct_index,
        "hint": sanitize_string(item.get("hint") or "", max_length=500) or None,
        "explanation": sanitize_string(item.get("explanation") or "", max_length=1500) or None,
        "syllabus_topic": sanitize_string(topic or item.get("topic") or "", max_length=128) or None,
        "is_generated": True,
    }


def _question_identity_key(text, options):
    normalized_text = " ".join(str(text or "").strip().lower().split())
    normalized_options = "|".join(
        sorted(" ".join(str(opt or "").strip().lower().split()) for opt in (options or []))
    )
    return f"{normalized_text}|{normalized_options}"


def _normalize_preview_reuse_payload(
    preview_questions,
    subject,
    grade,
    difficulty,
    topic,
    max_count,
):
    if not isinstance(preview_questions, list) or max_count <= 0:
        return []

    rows = []
    seen = set()
    for item in preview_questions:
        if not isinstance(item, dict):
            continue

        normalized = _normalize_question_payload(item, subject, grade, difficulty, topic)
        if not normalized:
            continue

        payload_row = {
            "text": normalized["text"],
            "options": normalized["options"],
            "correct_index": normalized["correct_index"],
            "hint": normalized["hint"],
            "explanation": normalized["explanation"],
            "topic": normalized["syllabus_topic"],
            "source": sanitize_string(item.get("source") or "preview_reuse", max_length=64) or "preview_reuse",
        }

        key = _question_identity_key(payload_row["text"], payload_row["options"])
        if not key or key in seen:
            continue

        seen.add(key)
        rows.append(payload_row)

        if len(rows) >= max_count:
            break

    return rows


def _build_preview_signature(*, subject, grade, difficulty, topic, count, seed, questions):
    canonical_payload = {
        "subject": str(subject or "").strip().lower(),
        "grade": str(grade or "").strip().lower(),
        "difficulty": str(difficulty or "").strip().lower(),
        "topic": str(topic or "").strip().lower(),
        "count": int(max(0, count or 0)),
        "seed": seed,
        "questions": [
            {
                "text": str(item.get("text") or "").strip(),
                "options": [str(opt or "").strip() for opt in (item.get("options") or [])],
                "correct_index": int(item.get("correct_index") or 0),
                "topic": str(item.get("topic") or "").strip(),
                "source": str(item.get("source") or "").strip().lower(),
            }
            for item in (questions or [])
            if isinstance(item, dict)
        ],
    }
    raw = json.dumps(canonical_payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _build_generated_question_records(
    generated_questions,
    subject,
    grade,
    difficulty,
    topic,
    generated_by,
    seed,
    default_source="teacher_dashboard",
    generation_meta_extras=None,
):
    records = []
    generated_at = _utcnow().isoformat()
    for item in generated_questions:
        normalized = _normalize_question_payload(item, subject, grade, difficulty, topic)
        if not normalized:
            continue

        source = sanitize_string(item.get("source") or default_source, max_length=64) or default_source
        generation_meta = {
            "generated_at": generated_at,
            "generator_version": "teacher-v3",
            "seed": seed,
            "source": source,
        }
        if generation_meta_extras:
            generation_meta.update(generation_meta_extras)

        record = Question(
            **normalized,
            generated_by=generated_by,
            generation_meta=generation_meta,
        )
        records.append(record)
    return records


def _generate_technical_sample_payload(
    *,
    subject,
    grade,
    difficulty,
    topic,
    count,
    seed,
):
    focus = sanitize_string(topic or subject or "the requested topic", max_length=128) or "the requested topic"
    subject_label = (sanitize_string(subject, max_length=64) or "STEM").strip().title()
    grade_label = (sanitize_string(grade, max_length=32) or "school").strip().lower()
    difficulty_label = (sanitize_string(difficulty, max_length=16) or "medium").strip().lower()

    rng = random.Random(seed)
    templates = [
        (
            "Which statement best defines {focus} in {subject_label}?",
            "It describes the core principle used to explain {focus}.",
            [
                "It ignores evidence and relies only on guesswork.",
                "It means every outcome is equally correct regardless of facts.",
                "It is unrelated to the requested subject area.",
            ],
            "This option states the core idea accurately.",
        ),
        (
            "In a {grade_label} {subject_label} assessment, what is the best way to apply {focus}?",
            "Use the concept in a factual scenario and justify the answer with evidence.",
            [
                "Memorize random terms without checking their meaning.",
                "Choose the longest option because it looks detailed.",
                "Ignore units, assumptions, and known constraints.",
            ],
            "Applying evidence-based reasoning is the correct method.",
        ),
        (
            "Which option is the most accurate conclusion about {focus} at {difficulty_label} difficulty?",
            "The conclusion should be specific, testable, and consistent with known facts.",
            [
                "The conclusion should avoid measurable evidence.",
                "The conclusion should contradict all known definitions.",
                "The conclusion should be based only on personal opinion.",
            ],
            "A valid conclusion must align with verifiable facts.",
        ),
    ]

    rows = []
    for idx in range(max(0, int(count))):
        question_tpl, correct_tpl, distractors_tpl, explanation_tpl = templates[idx % len(templates)]
        question_text = question_tpl.format(
            focus=focus,
            subject_label=subject_label,
            grade_label=grade_label,
            difficulty_label=difficulty_label,
        )
        correct = correct_tpl.format(
            focus=focus,
            subject_label=subject_label,
            grade_label=grade_label,
            difficulty_label=difficulty_label,
        )
        distractors = [
            value.format(
                focus=focus,
                subject_label=subject_label,
                grade_label=grade_label,
                difficulty_label=difficulty_label,
            )
            for value in distractors_tpl
        ]

        options = [correct] + distractors
        rng.shuffle(options)
        correct_index = options.index(correct)

        rows.append({
            "text": question_text,
            "options": options,
            "correct_index": correct_index,
            "hint": f"Focus on the core principle of {focus}.",
            "explanation": explanation_tpl,
            "topic": focus,
            "source": "technical_sample_fallback",
        })

    return rows


def _pick_or_generate_questions(
    teacher_id,
    subject,
    grade,
    difficulty,
    topic,
    count,
    seed=None,
    test_title=None,
    test_description=None,
):
    pool = []
    generation_status = {
        "requested_count": count,
        "generated_count": 0,
        "service_generated_count": 0,
        "service_llm_count": 0,
        "service_template_count": 0,
        "service_cache_hit": False,
        "technical_sample_count": 0,
        "service_error": None,
        "service_status_code": None,
        "service_endpoint": None,
        "service_latency_ms": None,
        "source_mode": "strict_llm",
        "technical_fallback_used": False,
    }

    service_result = generate_topic_mcqs(
        subject=subject,
        grade=grade,
        difficulty=difficulty,
        topic=topic,
        count=count,
        seed=seed,
        test_title=test_title,
        test_description=test_description,
    )

    service_meta = service_result.get("meta") if isinstance(service_result.get("meta"), dict) else {}
    generation_status["service_status_code"] = service_result.get("status_code")
    generation_status["service_error"] = service_result.get("error")
    generation_status["service_endpoint"] = service_result.get("service_endpoint")
    generation_status["service_latency_ms"] = service_result.get("service_latency_ms")
    generation_status["service_llm_count"] = int(service_meta.get("llm_count") or 0)
    generation_status["service_template_count"] = int(service_meta.get("template_count") or 0)
    generation_status["service_cache_hit"] = bool(service_meta.get("cache_hit"))

    service_payload = []
    if service_result.get("ok"):
        for row in service_result.get("questions") or []:
            if not isinstance(row, dict):
                continue
            service_payload.append({
                **row,
                "source": "topic_ai_service",
            })
    elif service_result.get("error"):
        current_app.logger.warning(
            "Topic AI service call failed during test generation: %s",
            service_result.get("error"),
        )

    generated_records = _build_generated_question_records(
        service_payload,
        subject,
        grade,
        difficulty,
        topic,
        teacher_id,
        seed,
        default_source="topic_ai_service",
        generation_meta_extras={
            "service_meta": service_meta,
            "test_title": sanitize_string(test_title or "", max_length=255) or None,
            "test_description": sanitize_string(test_description or "", max_length=1000) or None,
        },
    )

    for record in generated_records:
        db.session.add(record)
    if generated_records:
        db.session.flush()
        pool.extend(generated_records)
    generation_status["service_generated_count"] = len(generated_records)

    if not service_result.get("ok") and len(pool) < count:
        sample_payload = _generate_technical_sample_payload(
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            topic=topic,
            count=count - len(pool),
            seed=seed,
        )
        sample_records = _build_generated_question_records(
            sample_payload,
            subject,
            grade,
            difficulty,
            topic,
            teacher_id,
            seed,
            default_source="technical_sample_fallback",
            generation_meta_extras={
                "fallback_reason": "topic_ai_service_technical_failure",
                "service_error": service_result.get("error"),
                "service_status_code": service_result.get("status_code"),
            },
        )
        for record in sample_records:
            db.session.add(record)
        if sample_records:
            db.session.flush()
            pool.extend(sample_records)
            generation_status["technical_sample_count"] = len(sample_records)
            generation_status["technical_fallback_used"] = True

    final_pool = pool[:count]
    generation_status["generated_count"] = len(final_pool)
    return final_pool, generation_status


@teacher_bp.get("/dashboard")
@require_auth
@role_required("teacher")
def teacher_dashboard():
    """Get teacher dashboard data with scoped school/grade metrics."""
    teacher = g.current_user
    days = _parse_days_arg(30)
    cutoff = _utcnow() - timedelta(days=days)

    students_query, effective_grade = _teacher_student_filter(teacher)
    students = students_query.order_by(User.name.asc()).all()
    student_ids = [student.id for student in students]

    my_tests_query = Test.query.filter_by(created_by=teacher.id)
    my_tests = my_tests_query.order_by(Test.created_at.desc()).all()

    recent_results = []
    total_attempts = 0
    average_score = 0.0

    if student_ids:
        recent_results = TestResult.query.filter(
            TestResult.user_id.in_(student_ids),
            TestResult.started_at >= cutoff,
        ).order_by(TestResult.started_at.desc()).limit(12).all()

        all_period_results = TestResult.query.filter(
            TestResult.user_id.in_(student_ids),
            TestResult.started_at >= cutoff,
        ).all()

        total_attempts = len(all_period_results)
        if total_attempts > 0:
            pct_values = [
                (r.correct_answers / r.total_questions * 100)
                for r in all_period_results
                if r.total_questions and r.total_questions > 0
            ]
            average_score = round(sum(pct_values) / len(pct_values), 2) if pct_values else 0.0

    published_tests = sum(1 for test in my_tests if test.is_published)
    active_assignments = TestAssignment.query.filter(
        TestAssignment.assigned_by == teacher.id,
        TestAssignment.status.in_(["assigned", "started"]),
    ).count()
    classrooms_count = Classroom.query.filter_by(teacher_id=teacher.id).count()

    at_risk_payload = {}
    try:
        # Compute predictions only for the small sample shown on the landing dashboard.
        sample_student_ids = [s.id for s in students[:8]]
        at_risk_payload = get_at_risk_predictions_for_students(
            sample_student_ids,
            cutoff=cutoff,
            top_k_shap=3,
        )
    except Exception:
        at_risk_payload = {"at_risk_students": [], "meta": {"reason": "at_risk_failed"}}

    at_risk_by_id = {int(x.get("student_id")): x for x in at_risk_payload.get("at_risk_students", []) if x.get("student_id") is not None}
    return jsonify({
        "period_days": days,
        "teacher": {
            "id": teacher.id,
            "name": teacher.name,
            "grade": effective_grade,
            "school_id": teacher.school_id,
        },
        "students": {
            "total": len(students),
            "sample": [
                {
                    **student.as_dict(),
                    "at_risk": at_risk_by_id.get(int(student.id), {}).get("at_risk_probability") if at_risk_by_id.get(int(student.id)) else None,
                    "at_risk_explanation": at_risk_by_id.get(int(student.id), {}).get("explanation") if at_risk_by_id.get(int(student.id)) else None,
                }
                for student in students[:8]
            ],
        },
        "tests": {
            "total": len(my_tests),
            "published": published_tests,
            "drafts": max(0, len(my_tests) - published_tests),
            "recent": [test.as_dict() for test in my_tests[:6]],
        },
        "assignments": {
            "active": active_assignments,
            "classrooms": classrooms_count,
        },
        "performance": {
            "total_attempts": total_attempts,
            "average_score": average_score,
            "recent_results": [result.as_dict() for result in recent_results],
        },
        "at_risk_meta": at_risk_payload.get("meta") or {},
    }), 200


@teacher_bp.post("/question-bank/generate")
@require_auth
@role_required("teacher")
def teacher_generate_question_bank():
    """Generate question payload with optional persistence for teacher workflows."""
    teacher = g.current_user
    data = request.get_json(silent=True) or {}

    error = validate_required_fields(data, ["subject", "grade", "difficulty", "count"])
    if error:
        return jsonify({"error": error}), 400

    subject = sanitize_string(data.get("subject"), max_length=64)
    grade = sanitize_string(data.get("grade"), max_length=32).lower()
    difficulty = sanitize_string(data.get("difficulty"), max_length=16).lower()
    topic = sanitize_string(data.get("topic") or "", max_length=128) or None
    test_title = sanitize_string(data.get("title") or "", max_length=255) or None
    test_description = sanitize_string(data.get("description") or "", max_length=1000) or None
    count = _clamp_int(data.get("count", 10), 10, 1, 50)
    persist = _parse_bool_flag(data.get("persist"), False)

    seed_raw = data.get("seed")
    seed = None
    if seed_raw is not None and str(seed_raw).strip() != "":
        try:
            seed = int(seed_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "seed must be an integer"}), 400

    if difficulty not in VALID_DIFFICULTIES:
        return jsonify({"error": "Difficulty must be one of: easy, medium, hard"}), 400
    if grade not in VALID_GRADES:
        return jsonify({"error": "Grade must be one of: elementary, middle, high, college"}), 400

    service_result = generate_topic_mcqs(
        subject=subject,
        grade=grade,
        difficulty=difficulty,
        topic=topic,
        count=count,
        seed=seed,
        test_title=test_title,
        test_description=test_description,
    )

    service_meta = service_result.get("meta") if isinstance(service_result.get("meta"), dict) else {}
    generation_status = {
        "requested_count": count,
        "generated_count": 0,
        "service_generated_count": 0,
        "service_llm_count": int(service_meta.get("llm_count") or 0),
        "service_template_count": int(service_meta.get("template_count") or 0),
        "service_cache_hit": bool(service_meta.get("cache_hit")),
        "technical_sample_count": 0,
        "technical_fallback_used": False,
        "service_error": service_result.get("error"),
        "service_status_code": service_result.get("status_code"),
        "service_endpoint": service_result.get("service_endpoint"),
        "service_latency_ms": service_result.get("service_latency_ms"),
        "source_mode": "strict_llm",
    }

    generated_payload = []
    if service_result.get("ok"):
        generated_payload = [
            {
                **row,
                "source": "topic_ai_service",
            }
            for row in (service_result.get("questions") or [])
            if isinstance(row, dict)
        ]
        generation_status["service_generated_count"] = len(generated_payload)
    elif service_result.get("error"):
        current_app.logger.warning(
            "Topic AI service call failed for question bank generation: %s",
            service_result.get("error"),
        )

    if not service_result.get("ok") and len(generated_payload) < count:
        sample_payload = _generate_technical_sample_payload(
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            topic=topic,
            count=count - len(generated_payload),
            seed=seed,
        )
        generated_payload.extend(sample_payload)
        generation_status["technical_sample_count"] = len(sample_payload)
        generation_status["technical_fallback_used"] = len(sample_payload) > 0

    normalized_preview = []
    normalized_payload = []
    for item in generated_payload:
        normalized = _normalize_question_payload(item, subject, grade, difficulty, topic)
        if not normalized:
            continue

        normalized_preview.append({
            "subject": normalized["subject"],
            "grade": normalized["grade"],
            "difficulty": normalized["difficulty"],
            "text": normalized["text"],
            "options": normalized["options"],
            "correct_index": normalized["correct_index"],
            "hint": normalized["hint"],
            "explanation": normalized["explanation"],
            "topic": normalized["syllabus_topic"],
            "source": item.get("source") or "topic_ai_service",
        })
        normalized_payload.append({
            "text": normalized["text"],
            "options": normalized["options"],
            "correct_index": normalized["correct_index"],
            "hint": normalized["hint"],
            "explanation": normalized["explanation"],
            "topic": normalized["syllabus_topic"],
            "source": item.get("source") or "topic_ai_service",
        })

        if len(normalized_payload) >= count:
            break

    generation_status["generated_count"] = len(normalized_payload)
    preview_signature = _build_preview_signature(
        subject=subject,
        grade=grade,
        difficulty=difficulty,
        topic=topic,
        count=count,
        seed=seed,
        questions=normalized_preview,
    )

    if not persist:
        warning = None
        if generation_status["generated_count"] == 0:
            warning = generation_status["service_error"] or "No valid questions were generated for this request."
        elif generation_status.get("technical_fallback_used"):
            warning = (
                "Topic AI service was temporarily unavailable. "
                "Sample questions were generated for continuity."
            )

        return jsonify({
            "persisted": False,
            "requested_count": count,
            "generated_count": len(normalized_preview),
            "seed": seed,
            "preview_signature": preview_signature,
            "warning": warning,
            "generation_status": generation_status,
            "questions": normalized_preview,
        }), 200

    warning = None
    if len(normalized_payload) == 0:
        status_code = 503 if generation_status.get("service_status_code") in {503, 504} else 502
        return jsonify({
            "error": "Question generation is temporarily unavailable. Please retry shortly.",
            "generated_count": len(normalized_payload),
            "requested_count": count,
            "service_url": get_topic_ai_service_url(),
            "generation_status": generation_status,
        }), status_code

    if len(normalized_payload) < count:
        warning = (
            f"Requested {count} questions, but only {len(normalized_payload)} were generated and saved."
        )

    generated_records = _build_generated_question_records(
        normalized_payload,
        subject,
        grade,
        difficulty,
        topic,
        teacher.id,
        seed,
        default_source="topic_ai_service",
        generation_meta_extras={
            "service_meta": service_meta,
            "service_url": service_result.get("service_url"),
            "test_title": test_title,
            "test_description": test_description,
            "technical_fallback_used": bool(generation_status.get("technical_fallback_used")),
        },
    )

    for record in generated_records:
        db.session.add(record)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to save generated questions: {str(exc)}"}), 500

    return jsonify({
        "persisted": True,
        "requested_count": count,
        "generated_count": len(generated_records),
        "seed": seed,
        "warning": warning,
        "generation_status": generation_status,
        "questions": [
            {
                "id": q.id,
                "text": q.text,
                "topic": q.syllabus_topic,
                "difficulty": q.difficulty,
                "subject": q.subject,
                "grade": q.grade,
            }
            for q in generated_records
        ],
    }), 201


@teacher_bp.post("/tests")
@require_auth
@role_required("teacher")
def create_test():
    """Create a new test and attach selected/generated questions."""
    teacher = g.current_user
    data = request.get_json(silent=True) or {}

    required_fields = ["title", "subject", "grade", "difficulty", "question_count"]
    error = validate_required_fields(data, required_fields)
    if error:
        return jsonify({"error": error}), 400

    title = sanitize_string(data.get("title"), max_length=255)
    subject = sanitize_string(data.get("subject"), max_length=64)
    grade = sanitize_string(data.get("grade"), max_length=32).lower()
    topic = sanitize_string(data.get("topic") or "", max_length=128) or None
    difficulty = sanitize_string(data.get("difficulty"), max_length=16).lower()
    question_count = _clamp_int(data.get("question_count", 10), 10, 1, 50)
    time_limit = _clamp_int(data.get("time_limit", 30), 30, 5, 240)
    description = sanitize_string(data.get("description") or "", max_length=1000) or None

    seed_raw = data.get("seed")
    seed = None
    if seed_raw is not None and str(seed_raw).strip() != "":
        try:
            seed = int(seed_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "seed must be an integer"}), 400

    if difficulty not in VALID_DIFFICULTIES:
        return jsonify({"error": "Difficulty must be one of: easy, medium, hard"}), 400
    if grade not in VALID_GRADES:
        return jsonify({"error": "Grade must be one of: elementary, middle, high, college"}), 400

    school_id = _ensure_teacher_school(teacher)
    if not school_id:
        return jsonify({"error": "Unable to resolve teacher school context"}), 500

    try:
        test = Test(
            title=title,
            description=description,
            subject=subject,
            grade=grade,
            topic=topic,
            difficulty=difficulty,
            question_count=question_count,
            time_limit=time_limit,
            created_by=teacher.id,
            school_id=school_id,
            total_points=question_count,
        )
        db.session.add(test)
        db.session.flush()

        questions, generation_status = _pick_or_generate_questions(
            teacher_id=teacher.id,
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            topic=topic,
            count=question_count,
            seed=seed,
            test_title=title,
            test_description=description,
        )

        warning = None
        generated_count = len(questions)

        if generation_status.get("service_error"):
            if generation_status.get("technical_fallback_used"):
                warning = (
                    "Topic AI service was temporarily unavailable. "
                    "Sample questions were used for continuity."
                )
            else:
                warning = generation_status.get("service_error")

        if generated_count == 0:
            db.session.rollback()
            status_code = 503 if generation_status.get("service_status_code") in {503, 504} else 502
            return jsonify({
                "error": "Question generation is temporarily unavailable. Please retry shortly.",
                "generated_count": generated_count,
                "requested_count": question_count,
                "service_url": get_topic_ai_service_url(),
                "generation_status": generation_status,
            }), status_code

        if generated_count < question_count:
            partial_warning = (
                f"Requested {question_count} questions, but only {generated_count} could be generated. "
                "Test was created with available questions."
            )
            warning = f"{warning} {partial_warning}".strip() if warning else partial_warning
            test.question_count = generated_count
            test.total_points = generated_count

        for idx, question in enumerate(questions, start=1):
            db.session.add(TestQuestion(
                test_id=test.id,
                question_id=question.id,
                order=idx,
                points=1,
            ))

        db.session.commit()

        current_app.logger.info(
            "Teacher test generation status test_id=%s requested=%s generated=%s service_status=%s latency_ms=%s endpoint=%s error=%s",
            test.id,
            question_count,
            generated_count,
            generation_status.get("service_status_code"),
            generation_status.get("service_latency_ms"),
            generation_status.get("service_endpoint"),
            generation_status.get("service_error"),
        )

        return jsonify({
            "message": "Test created successfully",
            "test": test.as_dict(),
            "question_ids": [q.id for q in questions],
            "requested_count": question_count,
            "generated_count": generated_count,
            "warning": warning,
            "generation_status": generation_status,
        }), 201
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to create test: {str(exc)}"}), 500


@teacher_bp.get("/tests")
@require_auth
@role_required("teacher")
def get_tests():
    """Get all tests created by the teacher with performance metadata."""
    teacher = g.current_user

    tests = Test.query.filter_by(created_by=teacher.id).order_by(Test.created_at.desc()).all()

    test_ids = [t.id for t in tests]
    result_map = {}
    if test_ids:
        aggregates = db.session.query(
            TestResult.test_id,
            func.count(TestResult.id).label("attempts"),
            func.avg(
                func.coalesce((TestResult.correct_answers * 100.0) / func.nullif(TestResult.total_questions, 0), 0)
            ).label("avg_score"),
        ).filter(
            TestResult.test_id.in_(test_ids),
        ).group_by(TestResult.test_id).all()
        for row in aggregates:
            result_map[row.test_id] = {
                "attempts": int(row.attempts or 0),
                "average_score": round(float(row.avg_score or 0), 2),
            }

    payload = []
    for test in tests:
        item = test.as_dict()
        item.update(result_map.get(test.id, {"attempts": 0, "average_score": 0.0}))
        payload.append(item)

    return jsonify({"tests": payload}), 200


@teacher_bp.get("/tests/<int:test_id>")
@require_auth
@role_required("teacher")
def get_test(test_id):
    """Get a specific test with attached questions."""
    teacher = g.current_user

    test = Test.query.filter_by(id=test_id, created_by=teacher.id).first()
    if not test:
        return jsonify({"error": "Test not found"}), 404

    test_questions = TestQuestion.query.filter_by(test_id=test_id).order_by(TestQuestion.order.asc()).all()
    questions_data = []
    for tq in test_questions:
        question = db.session.get(Question, tq.question_id)
        if not question:
            continue
        questions_data.append({
            "id": question.id,
            "text": question.text,
            "options": question.options,
            "correct_index": question.correct_index,
            "order": tq.order,
            "points": tq.points,
            "difficulty": question.difficulty,
            "topic": question.syllabus_topic,
            "hint": question.hint,
            "explanation": question.explanation,
        })

    test_data = test.as_dict()
    test_data["questions"] = questions_data
    return jsonify(test_data), 200


@teacher_bp.put("/tests/<int:test_id>")
@require_auth
@role_required("teacher")
def update_test(test_id):
    """Update an existing teacher-owned test."""
    teacher = g.current_user

    test = Test.query.filter_by(id=test_id, created_by=teacher.id).first()
    if not test:
        return jsonify({"error": "Test not found"}), 404

    data = request.get_json(silent=True) or {}

    if "title" in data:
        test.title = sanitize_string(data.get("title"), max_length=255)
    if "description" in data:
        test.description = sanitize_string(data.get("description") or "", max_length=1000) or None
    if "time_limit" in data:
        test.time_limit = _clamp_int(data.get("time_limit"), test.time_limit, 5, 240)
    if "is_published" in data:
        test.is_published = bool(data.get("is_published"))
    if "is_active" in data:
        test.is_active = bool(data.get("is_active"))

    if "scheduled_at" in data:
        parsed = _parse_iso_datetime(data.get("scheduled_at"))
        if data.get("scheduled_at") and parsed is None:
            return jsonify({"error": "scheduled_at must be ISO datetime"}), 400
        test.scheduled_at = parsed

    if "expires_at" in data:
        parsed = _parse_iso_datetime(data.get("expires_at"))
        if data.get("expires_at") and parsed is None:
            return jsonify({"error": "expires_at must be ISO datetime"}), 400
        test.expires_at = parsed

    if "questions" in data:
        questions_payload = data.get("questions")
        if not isinstance(questions_payload, list) or len(questions_payload) == 0:
            return jsonify({"error": "questions must be a non-empty array"}), 400

        tqs = TestQuestion.query.filter_by(test_id=test.id).order_by(TestQuestion.order.asc()).all()
        tq_map = {tq.question_id: tq for tq in tqs}
        question_ids = [tq.question_id for tq in tqs]
        question_map = {q.id: q for q in Question.query.filter(Question.id.in_(question_ids)).all()} if question_ids else {}

        seen_orders = set()
        for idx, item in enumerate(questions_payload, start=1):
            if not isinstance(item, dict):
                return jsonify({"error": f"questions[{idx - 1}] must be an object"}), 400

            question_id = item.get("id")
            if not question_id or int(question_id) not in question_map:
                return jsonify({"error": f"questions[{idx - 1}] references unknown question id"}), 400
            question_id = int(question_id)

            text = sanitize_string(item.get("text") or "", max_length=2000)
            if not text:
                return jsonify({"error": f"questions[{idx - 1}].text is required"}), 400

            options = item.get("options")
            if not isinstance(options, list) or len(options) < 2:
                return jsonify({"error": f"questions[{idx - 1}].options must have at least 2 choices"}), 400
            normalized_options = [sanitize_string(opt or "", max_length=300) for opt in options]
            if any(not opt for opt in normalized_options):
                return jsonify({"error": f"questions[{idx - 1}].options cannot be empty"}), 400

            try:
                correct_index = int(item.get("correct_index"))
            except (TypeError, ValueError):
                return jsonify({"error": f"questions[{idx - 1}].correct_index must be an integer"}), 400

            if correct_index < 0 or correct_index >= len(normalized_options):
                return jsonify({"error": f"questions[{idx - 1}].correct_index out of range"}), 400

            order = item.get("order", idx)
            try:
                order = int(order)
            except (TypeError, ValueError):
                return jsonify({"error": f"questions[{idx - 1}].order must be an integer"}), 400

            if order < 1:
                return jsonify({"error": f"questions[{idx - 1}].order must be >= 1"}), 400
            if order in seen_orders:
                return jsonify({"error": "Question order values must be unique"}), 400
            seen_orders.add(order)

            try:
                points = int(item.get("points", 1))
            except (TypeError, ValueError):
                return jsonify({"error": f"questions[{idx - 1}].points must be an integer"}), 400
            points = max(1, points)

            question = question_map[question_id]
            question.text = text
            question.options = normalized_options
            question.correct_index = correct_index

            tq = tq_map.get(question_id)
            if tq:
                tq.order = order
                tq.points = points

        test.question_count = len(questions_payload)
        test.total_points = sum(max(1, int(item.get("points", 1))) for item in questions_payload)

    try:
        db.session.commit()
        return jsonify({"message": "Test updated successfully", "test": test.as_dict()}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to update test: {str(exc)}"}), 500


@teacher_bp.delete("/tests/<int:test_id>")
@require_auth
@role_required("teacher")
def delete_test(test_id):
    """Delete teacher-owned test."""
    teacher = g.current_user

    test = Test.query.filter_by(id=test_id, created_by=teacher.id).first()
    if not test:
        return jsonify({"error": "Test not found"}), 404

    try:
        db.session.delete(test)
        db.session.commit()
        return jsonify({"message": "Test deleted successfully"}), 200
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete test: {str(exc)}"}), 500


@teacher_bp.get("/students")
@require_auth
@role_required("teacher")
def get_students():
    """Get students in teacher scope with summary stats."""
    teacher = g.current_user
    grade = request.args.get("grade")

    students_query, effective_grade = _teacher_student_filter(teacher, grade=grade)
    students = students_query.order_by(User.name.asc()).all()
    student_ids = [s.id for s in students]

    stats_map = {sid: {"attempts": 0, "avg_score": 0.0} for sid in student_ids}
    if student_ids:
        rows = db.session.query(
            TestResult.user_id,
            func.count(TestResult.id).label("attempts"),
            func.avg(
                func.coalesce((TestResult.correct_answers * 100.0) / func.nullif(TestResult.total_questions, 0), 0)
            ).label("avg_score"),
        ).filter(
            TestResult.user_id.in_(student_ids),
        ).group_by(TestResult.user_id).all()

        for row in rows:
            stats_map[row.user_id] = {
                "attempts": int(row.attempts or 0),
                "avg_score": round(float(row.avg_score or 0), 2),
            }

    payload = []
    for student in students:
        item = student.as_dict()
        item.update(stats_map.get(student.id, {"attempts": 0, "avg_score": 0.0}))
        payload.append(item)

    return jsonify({
        "grade": effective_grade,
        "students": payload,
    }), 200


@teacher_bp.get("/classrooms")
@require_auth
@role_required("teacher")
def list_classrooms():
    teacher = g.current_user
    classrooms = Classroom.query.filter_by(teacher_id=teacher.id).order_by(Classroom.created_at.desc()).all()

    payload = []
    for classroom in classrooms:
        memberships = ClassroomStudent.query.filter_by(classroom_id=classroom.id, is_active=True).all()
        student_ids = [m.student_id for m in memberships]
        students = User.query.filter(User.id.in_(student_ids)).order_by(User.name.asc()).all() if student_ids else []

        item = classroom.as_dict()
        item["student_count"] = len(students)
        item["students"] = [student.as_dict() for student in students]
        payload.append(item)

    return jsonify({"classrooms": payload}), 200


@teacher_bp.post("/classrooms")
@require_auth
@role_required("teacher")
def create_classroom():
    teacher = g.current_user
    data = request.get_json(silent=True) or {}
    error = validate_required_fields(data, ["name"])
    if error:
        return jsonify({"error": error}), 400

    name = sanitize_string(data.get("name"), max_length=128)
    grade = sanitize_string(data.get("grade") or teacher.grade or "", max_length=32) or None
    auto_enroll_students = bool(data.get("auto_enroll_students", False))

    if grade and grade not in VALID_GRADES:
        return jsonify({"error": "Invalid grade"}), 400

    school_id = _ensure_teacher_school(teacher)
    if not school_id:
        return jsonify({"error": "Unable to resolve teacher school context"}), 500

    classroom = Classroom(
        name=name,
        grade=grade,
        school_id=school_id,
        teacher_id=teacher.id,
    )
    db.session.add(classroom)

    enrolled_count = 0
    try:
        db.session.flush()

        # Professional default workflow support:
        # when enabled, enroll all same-grade students in teacher school,
        # and claim unassigned same-grade students into teacher school for onboarding.
        if auto_enroll_students and grade:
            candidates = User.query.filter(
                User.role == "student",
                User.grade == grade,
                or_(
                    User.school_id == school_id,
                    User.school_id.is_(None),
                ),
            ).all()

            for student in candidates:
                if student.school_id is None:
                    student.school_id = school_id

                membership = ClassroomStudent.query.filter_by(classroom_id=classroom.id, student_id=student.id).first()
                if membership:
                    if not membership.is_active:
                        membership.is_active = True
                    enrolled_count += 1
                    continue

                db.session.add(ClassroomStudent(classroom_id=classroom.id, student_id=student.id, is_active=True))
                enrolled_count += 1

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to create classroom: {str(exc)}"}), 500

    return jsonify({
        "message": "Classroom created",
        "classroom": classroom.as_dict(),
        "enrolled_count": enrolled_count,
        "auto_enroll_students": auto_enroll_students,
    }), 201


@teacher_bp.post("/classrooms/<int:classroom_id>/enroll-grade")
@require_auth
@role_required("teacher")
def enroll_classroom_by_grade(classroom_id):
    teacher = g.current_user
    classroom, error_response = _get_teacher_classroom_or_404(teacher.id, classroom_id)
    if error_response:
        return error_response

    if not classroom.grade:
        return jsonify({"error": "Classroom grade is required for bulk enroll"}), 400

    school_id = _ensure_teacher_school(teacher)
    if not school_id:
        return jsonify({"error": "Unable to resolve teacher school context"}), 500

    enrolled_count = 0
    candidates = User.query.filter(
        User.role == "student",
        User.grade == classroom.grade,
        or_(
            User.school_id == school_id,
            User.school_id.is_(None),
        ),
    ).all()

    for student in candidates:
        if student.school_id is None:
            student.school_id = school_id

        membership = ClassroomStudent.query.filter_by(classroom_id=classroom.id, student_id=student.id).first()
        if membership:
            if not membership.is_active:
                membership.is_active = True
            enrolled_count += 1
            continue

        db.session.add(ClassroomStudent(classroom_id=classroom.id, student_id=student.id, is_active=True))
        enrolled_count += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to enroll students: {str(exc)}"}), 500

    return jsonify({
        "message": "Students enrolled by grade",
        "classroom_id": classroom.id,
        "grade": classroom.grade,
        "enrolled_count": enrolled_count,
    }), 200


@teacher_bp.post("/classrooms/<int:classroom_id>/students")
@require_auth
@role_required("teacher")
def add_student_to_classroom(classroom_id):
    teacher = g.current_user
    classroom, error_response = _get_teacher_classroom_or_404(teacher.id, classroom_id)
    if error_response:
        return error_response

    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")
    if not student_id:
        return jsonify({"error": "student_id is required"}), 400

    student = User.query.filter_by(id=student_id, role="student", school_id=teacher.school_id).first()
    if not student:
        return jsonify({"error": "Student not found in your school"}), 404

    if classroom.grade and student.grade and classroom.grade != student.grade:
        return jsonify({"error": "Student grade does not match classroom grade"}), 400

    membership = ClassroomStudent.query.filter_by(classroom_id=classroom_id, student_id=student.id).first()
    if membership:
        membership.is_active = True
    else:
        membership = ClassroomStudent(classroom_id=classroom_id, student_id=student.id, is_active=True)
        db.session.add(membership)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to add student: {str(exc)}"}), 500

    return jsonify({"message": "Student added", "membership": membership.as_dict()}), 200


@teacher_bp.delete("/classrooms/<int:classroom_id>/students/<int:student_id>")
@require_auth
@role_required("teacher")
def remove_student_from_classroom(classroom_id, student_id):
    teacher = g.current_user
    classroom, error_response = _get_teacher_classroom_or_404(teacher.id, classroom_id)
    if error_response:
        return error_response

    _ = classroom
    membership = ClassroomStudent.query.filter_by(classroom_id=classroom_id, student_id=student_id).first()
    if not membership:
        return jsonify({"error": "Membership not found"}), 404

    membership.is_active = False
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to remove student: {str(exc)}"}), 500

    return jsonify({"message": "Student removed"}), 200


@teacher_bp.get("/assignments")
@require_auth
@role_required("teacher")
def list_assignments():
    teacher = g.current_user
    limit = _clamp_int(request.args.get("limit", 200), 200, 1, 1000)

    rows = TestAssignment.query.filter_by(assigned_by=teacher.id).order_by(TestAssignment.created_at.desc()).limit(limit).all()

    test_ids = {row.test_id for row in rows}
    classroom_ids = {row.classroom_id for row in rows if row.classroom_id}
    student_ids = {row.student_id for row in rows if row.student_id}

    tests = {item.id: item for item in Test.query.filter(Test.id.in_(test_ids)).all()} if test_ids else {}
    classrooms = {item.id: item for item in Classroom.query.filter(Classroom.id.in_(classroom_ids)).all()} if classroom_ids else {}
    students = {item.id: item for item in User.query.filter(User.id.in_(student_ids)).all()} if student_ids else {}

    payload = []
    for row in rows:
        item = row.as_dict()
        item["test"] = tests.get(row.test_id).as_dict() if tests.get(row.test_id) else None
        item["classroom"] = classrooms.get(row.classroom_id).as_dict() if row.classroom_id and classrooms.get(row.classroom_id) else None
        item["student"] = students.get(row.student_id).as_dict() if row.student_id and students.get(row.student_id) else None
        payload.append(item)

    return jsonify({"assignments": payload}), 200


@teacher_bp.post("/assignments")
@require_auth
@role_required("teacher")
def create_assignment():
    teacher = g.current_user
    data = request.get_json(silent=True) or {}

    error = validate_required_fields(data, ["test_id"])
    if error:
        return jsonify({"error": error}), 400

    test_id = data.get("test_id")
    classroom_id = data.get("classroom_id")
    student_id = data.get("student_id")
    notes = sanitize_string(data.get("notes") or "", max_length=1000) or None
    due_at = _parse_iso_datetime(data.get("due_at"))
    is_mandatory = bool(data.get("is_mandatory", True))
    allow_late = bool(data.get("allow_late", False))

    if not classroom_id and not student_id:
        return jsonify({"error": "classroom_id or student_id is required"}), 400

    test = Test.query.filter_by(id=test_id, created_by=teacher.id).first()
    if not test:
        return jsonify({"error": "Test not found"}), 404

    if not test.is_published:
        return jsonify({"error": "Publish the test before assigning"}), 400

    created = []

    if classroom_id:
        classroom, error_response = _get_teacher_classroom_or_404(teacher.id, classroom_id)
        if error_response:
            return error_response

        assignment = TestAssignment(
            test_id=test.id,
            classroom_id=classroom.id,
            assigned_by=teacher.id,
            notes=notes,
            due_at=due_at,
            is_mandatory=is_mandatory,
            allow_late=allow_late,
            status='assigned',
        )
        db.session.add(assignment)
        created.append(assignment)

    if student_id:
        student = User.query.filter_by(id=student_id, role='student', school_id=teacher.school_id).first()
        if not student:
            return jsonify({"error": "Student not found in your school"}), 404

        assignment = TestAssignment(
            test_id=test.id,
            student_id=student.id,
            assigned_by=teacher.id,
            notes=notes,
            due_at=due_at,
            is_mandatory=is_mandatory,
            allow_late=allow_late,
            status='assigned',
        )
        db.session.add(assignment)
        created.append(assignment)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to create assignment: {str(exc)}"}), 500

    return jsonify({
        "message": "Assignment created",
        "assignments": [row.as_dict() for row in created],
    }), 201


@teacher_bp.patch('/assignments/<int:assignment_id>')
@require_auth
@role_required('teacher')
def update_assignment(assignment_id):
    teacher = g.current_user
    assignment = TestAssignment.query.filter_by(id=assignment_id, assigned_by=teacher.id).first()
    if not assignment:
        return jsonify({"error": "Assignment not found"}), 404

    data = request.get_json(silent=True) or {}
    if 'status' in data:
        status = sanitize_string(data.get('status'), max_length=32).lower()
        if status not in {'assigned', 'started', 'submitted', 'reviewed', 'expired', 'cancelled'}:
            return jsonify({"error": "Invalid status"}), 400
        assignment.status = status
        if status == 'reviewed':
            assignment.reviewed_at = _utcnow()

    if 'due_at' in data:
        parsed_due = _parse_iso_datetime(data.get('due_at'))
        if data.get('due_at') and parsed_due is None:
            return jsonify({"error": "due_at must be ISO datetime"}), 400
        assignment.due_at = parsed_due

    if 'notes' in data:
        assignment.notes = sanitize_string(data.get('notes') or '', max_length=1000) or None

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to update assignment: {str(exc)}"}), 500

    return jsonify({"message": "Assignment updated", "assignment": assignment.as_dict()}), 200


@teacher_bp.get("/reports")
@require_auth
@role_required("teacher")
def teacher_reports():
    """Detailed test reports scoped to teacher's school + grade."""
    teacher = g.current_user

    subject = sanitize_string(request.args.get("subject") or "", max_length=64) or None
    grade = request.args.get("grade", teacher.grade)
    limit = _clamp_int(request.args.get("limit", 100), 100, 1, 500)
    days = _parse_days_arg(90)
    cutoff = _utcnow() - timedelta(days=days)

    students_query, effective_grade = _teacher_student_filter(teacher, grade=grade)
    student_ids = [s.id for s in students_query.all()]
    if not student_ids:
        return jsonify({"items": [], "grade": effective_grade, "period_days": days}), 200

    q = TestResult.query.filter(
        TestResult.user_id.in_(student_ids),
        TestResult.started_at >= cutoff,
    )

    if subject:
        q = q.filter(TestResult.subject == subject)

    results = q.order_by(TestResult.started_at.desc()).limit(limit).all()

    user_map = {
        user.id: user
        for user in User.query.filter(User.id.in_({r.user_id for r in results})).all()
    }

    items = []
    for tr in results:
        user = user_map.get(tr.user_id)
        score_pct = round((tr.correct_answers / tr.total_questions * 100) if tr.total_questions > 0 else 0, 2)
        items.append({
            "id": tr.id,
            "student_name": user.name if user else "Unknown",
            "student_email": user.email if user else "Unknown",
            "subject": tr.subject,
            "score_pct": score_pct,
            "correct_answers": tr.correct_answers,
            "total_questions": tr.total_questions,
            "earned_points": tr.earned_points,
            "total_points": tr.total_points,
            "status": tr.status,
            "test_date": tr.started_at.isoformat() if tr.started_at else None,
            "finished_at": tr.finished_at.isoformat() if tr.finished_at else None,
        })

    return jsonify({
        "period_days": days,
        "grade": effective_grade,
        "items": items,
    }), 200


@teacher_bp.get("/analytics")
@require_auth
@role_required("teacher")
def teacher_analytics():
    """Get chart-ready analytics for teacher's students."""
    teacher = g.current_user
    grade = request.args.get("grade")
    days = _parse_days_arg(30)
    cutoff = _utcnow() - timedelta(days=days)

    students_query, effective_grade = _teacher_student_filter(teacher, grade=grade)
    student_ids = [s.id for s in students_query.all()]

    if not student_ids:
        return jsonify({
            "period_days": days,
            "grade": effective_grade,
            "summary": {
                "total_students": 0,
                "active_students": 0,
                "total_attempts": 0,
                "average_score": 0.0,
                "top_student": None,
            },
            "student_performance": [],
            "subject_performance": [],
            "grade_performance": [],
            "difficulty_performance": [],
            "weak_topics": [],
        }), 200

    score_expr = func.coalesce(
        (TestResult.correct_answers * 100.0) / func.nullif(TestResult.total_questions, 0),
        0,
    )

    student_performance_rows = db.session.query(
        User.id.label("student_id"),
        User.name.label("student_name"),
        User.grade.label("grade"),
        func.count(TestResult.id).label("total_attempts"),
        func.avg(score_expr).label("avg_score"),
        func.sum(func.coalesce(TestResult.correct_answers, 0)).label("correct_answers"),
        func.sum(func.coalesce(TestResult.total_questions, 0)).label("total_questions"),
    ).outerjoin(
        TestResult,
        and_(
            TestResult.user_id == User.id,
            TestResult.started_at >= cutoff,
        ),
    ).filter(
        User.id.in_(student_ids),
    ).group_by(
        User.id,
        User.name,
        User.grade,
    ).order_by(
        func.avg(score_expr).desc(),
        func.count(TestResult.id).desc(),
    ).all()

    subject_performance = db.session.query(
        TestResult.subject,
        func.count(TestResult.id).label("total_attempts"),
        func.avg(score_expr).label("avg_score"),
    ).filter(
        TestResult.user_id.in_(student_ids),
        TestResult.started_at >= cutoff,
    ).group_by(TestResult.subject).all()

    student_subject_rows = db.session.query(
        TestResult.user_id,
        TestResult.subject,
        func.count(TestResult.id).label("total_attempts"),
        func.avg(score_expr).label("avg_score"),
    ).filter(
        TestResult.user_id.in_(student_ids),
        TestResult.started_at >= cutoff,
    ).group_by(
        TestResult.user_id,
        TestResult.subject,
    ).all()

    user_name_by_id = {
        row.student_id: row.student_name
        for row in student_performance_rows
    }

    best_subject_by_student = {}
    top_student_by_subject = {}
    for row in student_subject_rows:
        student_id = int(row.user_id)
        subject = row.subject or "Unknown"
        avg_score = float(row.avg_score or 0)
        total_attempts = int(row.total_attempts or 0)

        candidate_subject = {
            "subject": subject,
            "avg_score": round(avg_score, 2),
            "total_attempts": total_attempts,
        }
        current_subject = best_subject_by_student.get(student_id)
        if not current_subject or candidate_subject["avg_score"] > current_subject["avg_score"]:
            best_subject_by_student[student_id] = candidate_subject

        candidate_student = {
            "student_id": student_id,
            "student_name": user_name_by_id.get(student_id, "Unknown"),
            "avg_score": round(avg_score, 2),
            "total_attempts": total_attempts,
        }
        current_student = top_student_by_subject.get(subject)
        if not current_student or candidate_student["avg_score"] > current_student["avg_score"]:
            top_student_by_subject[subject] = candidate_student

    difficulty_performance = db.session.query(
        Question.difficulty,
        func.count(AnswerLog.id).label("total_attempts"),
        func.avg(func.cast(AnswerLog.is_correct, db.Integer) * 100.0).label("accuracy"),
    ).join(
        Question,
        AnswerLog.question_id == Question.id,
    ).filter(
        AnswerLog.user_id.in_(student_ids),
        AnswerLog.answered_at >= cutoff,
    ).group_by(Question.difficulty).all()

    topic_performance = db.session.query(
        Question.syllabus_topic,
        func.count(AnswerLog.id).label("total_attempts"),
        func.avg(func.cast(AnswerLog.is_correct, db.Integer) * 100.0).label("accuracy"),
    ).join(
        Question,
        AnswerLog.question_id == Question.id,
    ).filter(
        AnswerLog.user_id.in_(student_ids),
        AnswerLog.answered_at >= cutoff,
        Question.syllabus_topic.isnot(None),
    ).group_by(Question.syllabus_topic).order_by(func.avg(func.cast(AnswerLog.is_correct, db.Integer) * 100.0).asc()).limit(10).all()

    grade_rollup = {}
    for row in student_performance_rows:
        grade_key = row.grade or "unknown"
        bucket = grade_rollup.setdefault(grade_key, {
            "grade": grade_key,
            "total_students": 0,
            "active_students": 0,
            "total_attempts": 0,
            "weighted_score_sum": 0.0,
        })
        attempts = int(row.total_attempts or 0)
        avg_score = float(row.avg_score or 0)

        bucket["total_students"] += 1
        bucket["total_attempts"] += attempts
        bucket["weighted_score_sum"] += avg_score * attempts
        if attempts > 0:
            bucket["active_students"] += 1

    grade_order = {"elementary": 0, "middle": 1, "high": 2, "college": 3, "unknown": 9}
    grade_performance = []
    for bucket in grade_rollup.values():
        attempts = bucket["total_attempts"]
        avg_score = (bucket["weighted_score_sum"] / attempts) if attempts > 0 else 0.0
        grade_performance.append({
            "grade": bucket["grade"],
            "total_students": int(bucket["total_students"]),
            "active_students": int(bucket["active_students"]),
            "total_attempts": int(attempts),
            "avg_score": round(float(avg_score), 2),
        })

    grade_performance.sort(key=lambda item: grade_order.get(item["grade"], 99))

    total_attempts = sum(int(row.total_attempts or 0) for row in student_performance_rows)
    active_students = sum(1 for row in student_performance_rows if int(row.total_attempts or 0) > 0)
    average_score = round(
        sum(float(row.avg_score or 0) for row in student_performance_rows if int(row.total_attempts or 0) > 0) / active_students,
        2,
    ) if active_students > 0 else 0.0
    top_student = None
    ranked_active_students = [row for row in student_performance_rows if int(row.total_attempts or 0) > 0]
    if ranked_active_students:
        top_row = ranked_active_students[0]
        top_student = {
            "student_id": int(top_row.student_id),
            "student_name": top_row.student_name,
            "avg_score": round(float(top_row.avg_score or 0), 2),
            "total_attempts": int(top_row.total_attempts or 0),
        }

    at_risk_payload = {}
    try:
        at_risk_payload = get_at_risk_predictions_for_students(
            [int(sid) for sid in student_ids],
            cutoff=cutoff,
            top_k_shap=5,
        )
    except Exception:
        at_risk_payload = {"at_risk_students": [], "meta": {"reason": "at_risk_failed"}}

    return jsonify({
        "period_days": days,
        "grade": effective_grade,
        "summary": {
            "total_students": len(student_ids),
            "active_students": active_students,
            "total_attempts": total_attempts,
            "average_score": average_score,
            "top_student": top_student,
        },
        "student_performance": [
            {
                "student_id": int(row.student_id),
                "student_name": row.student_name,
                "grade": row.grade,
                "total_attempts": int(row.total_attempts or 0),
                "avg_score": round(float(row.avg_score or 0), 2),
                "correct_answers": int(row.correct_answers or 0),
                "total_questions": int(row.total_questions or 0),
                "best_subject": best_subject_by_student.get(int(row.student_id), {}).get("subject"),
            }
            for row in student_performance_rows
        ],
        "subject_performance": [
            {
                "subject": row.subject,
                "total_attempts": int(row.total_attempts or 0),
                "avg_score": round(float(row.avg_score or 0), 2),
                "top_student": top_student_by_subject.get(row.subject or "Unknown"),
            }
            for row in subject_performance
        ],
        "grade_performance": grade_performance,
        "difficulty_performance": [
            {
                "difficulty": row.difficulty or "unknown",
                "total_attempts": int(row.total_attempts or 0),
                "accuracy": round(float(row.accuracy or 0), 2),
            }
            for row in difficulty_performance
        ],
        "weak_topics": [
            {
                "topic": row.syllabus_topic,
                "total_attempts": int(row.total_attempts or 0),
                "accuracy": round(float(row.accuracy or 0), 2),
            }
            for row in topic_performance
        ],
        "at_risk_students": [
            {
                **item,
                "student_name": user_name_by_id.get(int(item.get("student_id")), None),
            }
            for item in (at_risk_payload.get("at_risk_students") or [])
        ],
        "at_risk_meta": at_risk_payload.get("meta") or {},
    }), 200
