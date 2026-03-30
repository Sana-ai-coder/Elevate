"""Question routes for fetching and submitting answers."""
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g
from ..models import db, Question, AnswerLog, UserProgress, EmotionLog, SubjectPerformance, User
from ..security import require_auth, optional_auth, role_required
from ..validation import sanitize_string
from sqlalchemy import and_, func
import random
from ..question_generator import generate_stem_questions, save_generated_questions
from ..recommendation_service import (
    get_recommender_metadata,
    recommend_questions_for_user,
)
from ..adaptive_engine import compute_adaptive_signals, difficulty_fallback_sequence

questions_bp = Blueprint("questions", __name__)


STEM_SUBJECT_ALIASES = {
    "science": ["science", "physics", "chemistry", "biology"],
    "technology": ["technology", "computer science", "programming", "coding"],
    "engineering": ["engineering"],
    "mathematics": ["mathematics", "math"]
}


def normalize_topic_token(topic_value: str | None) -> str:
    """Normalize topic values so AI Basics/ai-basics/ai_basics match consistently."""
    if not topic_value:
        return ""
    normalized = sanitize_string(topic_value).strip().lower()
    if not normalized:
        return ""
    normalized = normalized.replace("-", " ")
    normalized = "_".join(normalized.split())
    return normalized


def _select_adaptive_questions(base_filters: dict, count: int, target_difficulty: str | None, user_id: int | None, exclude_answered: bool):
    """Select questions by target difficulty with nearest fallback ordering.

    Prefers unanswered questions for authenticated users.
    """
    picked = []
    seen_ids = set()

    answered_ids = set()
    if user_id:
        answered_ids = {
            row.question_id
            for row in db.session.query(AnswerLog.question_id).filter(AnswerLog.user_id == user_id).all()
        }

    def query_for(difficulty_level: str, include_answered: bool, limit: int):
        q = Question.query.filter(Question.grade == sanitize_string(base_filters["grade"]).strip().lower())
        if base_filters.get("topic"):
            q = apply_topic_filter(q, base_filters["topic"])
        if base_filters.get("subject"):
            q = apply_subject_filter(q, base_filters["subject"])
        q = q.filter(Question.difficulty == difficulty_level)

        if user_id and not include_answered:
            q = q.filter(~Question.id.in_(answered_ids))

        rows = q.order_by(func.random()).limit(limit).all()
        return rows

    for difficulty in difficulty_fallback_sequence(target_difficulty):
        if len(picked) >= count:
            break
        needed = count - len(picked)
        rows = query_for(difficulty, include_answered=not exclude_answered, limit=needed)
        for row in rows:
            if row.id in seen_ids:
                continue
            seen_ids.add(row.id)
            picked.append(row)

    # If strict exclude_answered is enabled and still short, relax as a fallback.
    if exclude_answered and len(picked) < count:
        for difficulty in difficulty_fallback_sequence(target_difficulty):
            if len(picked) >= count:
                break
            needed = count - len(picked)
            rows = query_for(difficulty, include_answered=True, limit=needed)
            for row in rows:
                if row.id in seen_ids:
                    continue
                seen_ids.add(row.id)
                picked.append(row)

    return picked


def apply_subject_filter(query, subject_value: str):
    """Apply case-insensitive subject filtering with STEM alias support."""
    normalized = sanitize_string(subject_value).strip().lower()
    if not normalized:
        return query

    accepted = STEM_SUBJECT_ALIASES.get(normalized, [normalized])
    accepted = [v.lower() for v in accepted]
    return query.filter(func.lower(Question.subject).in_(accepted))


def apply_topic_filter(query, topic_value: str):
    """Apply topic filtering using normalized slugs across whitespace/hyphen variants."""
    normalized_topic = normalize_topic_token(topic_value)
    if not normalized_topic:
        return query

    db_normalized_topic = func.replace(
        func.replace(func.lower(Question.syllabus_topic), '-', '_'),
        ' ',
        '_',
    )
    return query.filter(db_normalized_topic == normalized_topic)


@questions_bp.get("/")
@optional_auth
def list_questions():
    """
    List and filter questions by subject, grade, and difficulty.
    
    Query Parameters:
    - subject: Filter by subject (e.g., "mathematics", "science")
    - grade: Filter by grade level (e.g., "elementary", "middle", "high", "college")
    - difficulty: Filter by difficulty (e.g., "easy", "medium", "hard")
    - exclude_answered: If true and user authenticated, exclude answered questions
    - limit: Max results (default 10, max 50)
    - offset: Pagination offset (default 0)
    
    Returns:
        JSON with questions list and metadata
    """
    from flask import g
    current_user = g.current_user
    try:
        # Get query parameters
        subject = request.args.get("subject")
        grade = request.args.get("grade")
        difficulty = request.args.get("difficulty")
        topic = request.args.get("topic") or request.args.get("syllabus_topic")
        exclude_answered = request.args.get("exclude_answered", "false").lower() == "true"
        limit = min(int(request.args.get("limit", 10)), 50)
        offset = int(request.args.get("offset", 0))
        
        # Build query
        query = Question.query
        
        # Apply filters
        if subject:
            query = apply_subject_filter(query, subject)
        if grade:
            query = query.filter(Question.grade == sanitize_string(grade).strip().lower())
        if difficulty:
            query = query.filter(Question.difficulty == sanitize_string(difficulty).strip().lower())
        if topic:
            query = apply_topic_filter(query, topic)
        
        # Exclude answered questions if requested and user is authenticated
        if exclude_answered and current_user:
            answered_question_ids = db.session.query(AnswerLog.question_id).filter(
                AnswerLog.user_id == current_user.id
            ).distinct().subquery()
            query = query.filter(~Question.id.in_(answered_question_ids))
        
        # Get total count before pagination
        total = query.count()
        
        # Apply pagination
        questions = query.offset(offset).limit(limit).all()
        
        # Format response (exclude correct answer for security)
        questions_list = [
            {
                "id": q.id,
                "subject": q.subject,
                "grade": q.grade,
                "difficulty": q.difficulty,
                "text": q.text,
                "options": q.options,
                "hint": q.hint,
                "syllabus_topic": q.syllabus_topic,
                "readability_level": q.readability_level,
                "tags": q.tags or []
            }
            for q in questions
        ]
        
        return jsonify({
            "questions": questions_list,
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters": {
                "subject": subject,
                "grade": grade,
                "difficulty": difficulty,
                "exclude_answered": exclude_answered
            }
        }), 200
    except ValueError as e:
        return jsonify({"error": "Invalid pagination parameters"}), 400
    except Exception as e:
        return jsonify({"error": "Failed to fetch questions", "details": str(e)}), 500


@questions_bp.get('/generate')
@optional_auth
def generate_questions():
    """Generate a question set for a student based on grade and syllabus topic.

    Query params:
    - grade: student's grade (required)
    - topic: syllabus topic slug (optional)
    - subject: subject filter (optional)
    - count: number of questions (default 10)
    - exclude_answered: true/false to skip already-answered questions for authenticated user
    """
    try:
        grade = request.args.get('grade')
        topic = request.args.get('topic') or request.args.get('syllabus_topic')
        subject = request.args.get('subject')
        try:
            count = min(int(request.args.get('count', 10)), 50)
        except Exception:
            count = 10
        exclude_answered = request.args.get('exclude_answered', 'false').lower() == 'true'

        if not grade:
            return jsonify({'error': 'grade is required'}), 400

        normalized_grade = sanitize_string(grade).strip().lower()
        normalized_topic = normalize_topic_token(topic)

        current_user = g.get('current_user')
        requested_difficulty = sanitize_string(request.args.get('difficulty')) if request.args.get('difficulty') else None

        adaptive = None
        target_difficulty = requested_difficulty
        if current_user and subject and not target_difficulty:
            adaptive = compute_adaptive_signals(current_user.id, subject, normalized_topic or topic)
            target_difficulty = adaptive.get('recommended_difficulty')

        recommendation_bundle = None
        recommendation_scores = {}
        recommended_questions: list[Question] = []
        if current_user and subject:
            recommendation_bundle = recommend_questions_for_user(
                user_id=current_user.id,
                subject=subject,
                grade=normalized_grade,
                topic=normalized_topic or topic,
                count=count,
                difficulty_hint=target_difficulty,
                exclude_answered=exclude_answered,
            )
            if recommendation_bundle.items:
                recommended_questions = [item.question for item in recommendation_bundle.items]
                recommendation_scores = {
                    str(item.question.id): {
                        'score': round(item.score, 4),
                        'expected_gain': round(item.expected_gain, 4),
                        'predicted_success': round(item.predicted_success, 4),
                        'novelty_bonus': round(item.novelty_bonus, 4),
                        'difficulty_alignment': round(item.difficulty_alignment, 4),
                        'popularity_bonus': round(item.popularity_bonus, 4),
                    }
                    for item in recommendation_bundle.items
                }

        fallback_questions = _select_adaptive_questions(
            base_filters={
                'grade': normalized_grade,
                'subject': subject,
                'topic': normalized_topic,
            },
            count=count,
            target_difficulty=target_difficulty,
            user_id=current_user.id if current_user else None,
            exclude_answered=exclude_answered,
        )

        questions: list[Question] = []
        seen_ids = set()
        for row in recommended_questions + fallback_questions:
            if row.id in seen_ids:
                continue
            seen_ids.add(row.id)
            questions.append(row)
            if len(questions) >= count:
                break

        recommendation_context = None
        if recommendation_bundle:
            recommendation_context = {
                **(recommendation_bundle.metadata or {}),
                'selected': len(recommended_questions),
            }
            if recommendation_scores:
                recommendation_context['scores'] = recommendation_scores

        # If bank coverage is sparse, top-up on demand so the student gets the selected count.
        if len(questions) < count and subject:
            needed = count - len(questions)
            generation_difficulty = (target_difficulty or 'medium').strip().lower()
            existing_variant_count_query = Question.query.filter(
                func.lower(Question.subject) == sanitize_string(subject).strip().lower(),
                Question.grade == normalized_grade,
                Question.difficulty == generation_difficulty,
            )
            if normalized_topic:
                existing_variant_count_query = apply_topic_filter(existing_variant_count_query, normalized_topic)
            variant_offset = existing_variant_count_query.count()

            generated_payload = generate_stem_questions(
                subject=sanitize_string(subject).strip().lower(),
                grade=normalized_grade,
                difficulty=generation_difficulty,
                count=needed,
                topic=normalized_topic,
                variant_offset=variant_offset,
            )

            # Keep topic aligned with the requested topic so filtering remains consistent.
            if normalized_topic:
                for item in generated_payload:
                    item['topic'] = normalized_topic

            # Defensive cleanup for malformed generated rows.
            cleaned_payload = []
            for item in generated_payload:
                if not item or not item.get('text'):
                    continue
                options = item.get('options') or []
                options = [str(opt).strip() for opt in options if str(opt).strip()]
                if len(options) < 2:
                    continue
                try:
                    correct_index = int(item.get('correct_index', 0))
                except Exception:
                    correct_index = 0
                if correct_index < 0 or correct_index >= len(options):
                    correct_index = 0

                cleaned_payload.append({
                    'text': str(item.get('text')).strip(),
                    'options': options,
                    'correct_index': correct_index,
                    'topic': normalize_topic_token(item.get('topic')) or normalized_topic or 'general',
                    'hint': str(item.get('hint') or 'Think carefully before answering.').strip(),
                    'explanation': str(item.get('explanation') or 'Review the concept and solve step by step.').strip(),
                })

            if cleaned_payload:
                created = save_generated_questions(
                    cleaned_payload,
                    subject=sanitize_string(subject).strip().lower(),
                    grade=normalized_grade,
                    difficulty=generation_difficulty,
                    generated_by=current_user.id if current_user else None,
                )
                questions.extend(created)

        # Final cap to respect user-selected count.
        questions = questions[:count]

        questions_list = [
            {
                'id': q.id,
                'subject': q.subject,
                'grade': q.grade,
                'difficulty': q.difficulty,
                'text': q.text,
                'options': q.options,
                'hint': q.hint,
                'syllabus_topic': q.syllabus_topic,
                'readability_level': q.readability_level,
                'tags': q.tags or []
            }
            for q in questions
        ]
        return jsonify({
            'questions': questions_list,
            'count': len(questions_list),
            'adaptive': {
                'target_difficulty': target_difficulty,
                'source': 'bkt_irt' if adaptive else ('request' if requested_difficulty else 'default'),
                **(adaptive or {}),
            },
            'recommendation': recommendation_context,
        }), 200
    except Exception as e:
        return jsonify({'error': 'Failed to generate questions', 'details': str(e)}), 500


@questions_bp.get('/recommendations')
@require_auth
@role_required('student')
def recommendation_endpoint():
    try:
        current_user = g.current_user
        subject = request.args.get('subject')
        if not subject:
            return jsonify({'error': 'subject is required'}), 400

        grade = request.args.get('grade') or (current_user.grade if current_user else None)
        topic = request.args.get('topic') or request.args.get('syllabus_topic')
        requested_difficulty = request.args.get('difficulty')
        exclude_answered = request.args.get('exclude_answered', 'true').lower() != 'false'
        try:
            count = min(int(request.args.get('count', 10)), 25)
        except Exception:
            return jsonify({'error': 'count must be an integer'}), 400

        bundle = recommend_questions_for_user(
            user_id=current_user.id,
            subject=subject,
            grade=sanitize_string(grade).strip().lower() if grade else None,
            topic=topic,
            count=count,
            difficulty_hint=requested_difficulty,
            exclude_answered=exclude_answered,
        )

        recommendation_scores = {}
        recommendations_payload = []
        for item in bundle.items:
            q = item.question
            recommendations_payload.append({
                'id': q.id,
                'subject': q.subject,
                'grade': q.grade,
                'difficulty': q.difficulty,
                'text': q.text,
                'options': q.options,
                'hint': q.hint,
                'syllabus_topic': q.syllabus_topic,
                'readability_level': q.readability_level,
                'tags': q.tags or [],
            })
            recommendation_scores[str(q.id)] = {
                'score': round(item.score, 4),
                'expected_gain': round(item.expected_gain, 4),
                'predicted_success': round(item.predicted_success, 4),
                'novelty_bonus': round(item.novelty_bonus, 4),
                'difficulty_alignment': round(item.difficulty_alignment, 4),
                'popularity_bonus': round(item.popularity_bonus, 4),
            }

        meta = bundle.metadata or {}
        if recommendation_scores:
            meta = {**meta, 'scores': recommendation_scores}
        manifest_meta = get_recommender_metadata() or {}
        meta.setdefault('artifact_id', manifest_meta.get('artifact_id'))
        meta.setdefault('metrics', manifest_meta.get('metrics'))
        meta['returned'] = len(recommendations_payload)

        return jsonify({'recommendations': recommendations_payload, 'meta': meta}), 200
    except Exception as exc:
        return jsonify({'error': 'Failed to fetch recommendations', 'details': str(exc)}), 500


@questions_bp.post('/generate')
@require_auth
@role_required('teacher','admin')
def generate_and_persist():
    """Generate and persist questions (teacher/admin only).

    Request JSON body:
    - topic (optional)
    - difficulty (optional: easy|medium|hard)
    - count (optional, default 5)
    - subject, grade (optional)
    """
    try:
        data = request.get_json() or {}
        topic = data.get('topic')
        difficulty = data.get('difficulty', 'medium')
        subject = data.get('subject', 'general')
        grade = data.get('grade')
        try:
            count = min(int(data.get('count', 5)), 50)
        except Exception:
            count = 5

        seed = data.get('seed')
        if seed is not None:
            try:
                seed = int(seed)
            except Exception:
                return jsonify({'error': 'seed must be an integer'}), 400

        # Generate using deterministic generator
        generated_data = generate_stem_questions(subject, grade or '', difficulty, count=count, seed=seed)

        # Persist generated questions with seed metadata
        new_questions = save_generated_questions(generated_data, subject, grade or '', difficulty, generated_by=g.current_user.id, seed=seed)

        return jsonify({'generated': [{'id': q.id, 'text': q.text, 'options': q.options, 'correct_index': q.correct_index, 'generation_meta': q.generation_meta} for q in new_questions]}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Failed to generate and persist questions', 'details': str(e)}), 500

@questions_bp.get('/topics')
@optional_auth
def list_topics():
    """List available syllabus topics for a given subject and/or grade."""
    try:
        subject = request.args.get('subject')
        grade = request.args.get('grade')
        q = Question.query
        if subject:
            q = apply_subject_filter(q, subject)
        if grade:
            q = q.filter(Question.grade == sanitize_string(grade).strip().lower())
        topics = q.with_entities(Question.syllabus_topic).distinct().all()
        # topics is list of tuples like [('arithmetic',), (None,), ...]
        cleaned = [t[0] for t in topics if t[0]]
        return jsonify({'topics': cleaned}), 200
    except Exception as e:
        return jsonify({'error': 'Failed to list topics', 'details': str(e)}), 500


@questions_bp.get("/<int:question_id>")
@optional_auth
def get_question(question_id):
    """
    Get a single question by ID.
    
    Args:
        question_id: Question ID
        
    Returns:
        JSON with question details (excludes correct answer and explanation until answered)
    """
    from flask import g
    current_user = g.current_user
    try:
        question = db.session.get(Question, question_id)
        if not question:
            return jsonify({"error": "Question not found"}), 404
        
        # Check if user already answered this question
        already_answered = False
        if current_user:
            answer = AnswerLog.query.filter_by(
                user_id=current_user.id,
                question_id=question_id
            ).first()
            already_answered = answer is not None
        
        response = {
            "id": question.id,
            "subject": question.subject,
            "grade": question.grade,
            "difficulty": question.difficulty,
            "text": question.text,
            "options": question.options,
            "hint": question.hint,
            "tags": question.tags or [],
            "already_answered": already_answered
        }
        
        # Only include explanation if already answered
        if already_answered:
            response["explanation"] = question.explanation
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({"error": "Failed to fetch question", "details": str(e)}), 500


@questions_bp.post("/<int:question_id>/submit")
@require_auth
def submit_answer(question_id):
    """
    Submit an answer to a question.
    
    Request Body:
    - selected_index: Index of selected option (0-based)
    - time_spent: Time spent in seconds
    - emotion: Optional emotion during answer (e.g., "focused", "confused")
    
    Returns:
        JSON with correctness, explanation, and updated progress
    """
    from flask import g
    current_user = g.current_user
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        selected_index = data.get("selected_index")
        time_spent = data.get("time_spent", 0)
        emotion = data.get("emotion")
        
        # Validation
        if selected_index is None:
            return jsonify({"error": "selected_index is required"}), 400
        
        try:
            selected_index = int(selected_index)
            time_spent = int(time_spent)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid data types"}), 400
        
        # Get question
        question = db.session.get(Question, question_id)
        if not question:
            return jsonify({"error": "Question not found"}), 404
        
        # Validate selected_index range (-1 means unanswered/timeout auto-submit)
        if selected_index < -1 or selected_index >= len(question.options):
            return jsonify({"error": "Invalid option index"}), 400
        
        # Check correctness
        is_correct = selected_index >= 0 and selected_index == question.correct_index
        
        # Save answer log
        answer_log = AnswerLog(
            user_id=current_user.id,
            question_id=question_id,
            selected_index=selected_index,
            is_correct=is_correct,
            time_spent=time_spent,
            difficulty_at_time=question.difficulty,
            emotion_at_time=sanitize_string(emotion) if emotion else None,
            answered_at=datetime.now(timezone.utc)
        )
        db.session.add(answer_log)
        
        # Log emotion if provided (for emotion tracking analysis)
        if emotion:
            emotion_log = EmotionLog(
                user_id=current_user.id,
                emotion=sanitize_string(emotion),
                confidence=1.0,  # Frontend should send this if available
                context=f"answering_{question.subject}",
                timestamp=datetime.now(timezone.utc)
            )
            db.session.add(emotion_log)
        
        # Update user progress for this subject
        progress = UserProgress.query.filter_by(
            user_id=current_user.id,
            subject=question.subject
        ).first()
        
        if not progress:
            progress = UserProgress(
                user_id=current_user.id,
                subject=question.subject,
                total_questions=0,
                correct_answers=0,
                current_difficulty=question.difficulty
            )
            db.session.add(progress)
        
        progress.total_questions = int(progress.total_questions or 0) + 1
        if is_correct:
            progress.correct_answers = int(progress.correct_answers or 0) + 1
        progress.last_updated = datetime.now(timezone.utc)
        
        # Calculate new accuracy
        accuracy = (progress.correct_answers / progress.total_questions * 100) if progress.total_questions > 0 else 0
        
        # Update or create subject performance tracking
        subject_perf = SubjectPerformance.query.filter_by(
            user_id=current_user.id,
            subject=question.subject
        ).first()
        
        if not subject_perf:
            subject_perf = SubjectPerformance(
                user_id=current_user.id,
                subject=question.subject,
                accuracy=0.0,
                streak=0,
                best_streak=0,
                total_time_spent=0
            )
            db.session.add(subject_perf)
        
        # Update streak
        subject_perf.streak = int(subject_perf.streak or 0)
        subject_perf.best_streak = int(subject_perf.best_streak or 0)
        subject_perf.total_time_spent = int(subject_perf.total_time_spent or 0)

        if is_correct:
            subject_perf.streak = int(subject_perf.streak or 0) + 1
            if subject_perf.streak > subject_perf.best_streak:
                subject_perf.best_streak = subject_perf.streak
        else:
            subject_perf.streak = 0  # Reset streak on wrong answer
        
        # Update accuracy and time
        subject_perf.accuracy = accuracy
        subject_perf.total_time_spent = int(subject_perf.total_time_spent or 0) + int(time_spent or 0)
        subject_perf.last_practiced_at = datetime.now(timezone.utc)

        adaptive = compute_adaptive_signals(
            user_id=current_user.id,
            subject=question.subject,
            concept=question.syllabus_topic,
        )
        if adaptive.get('recommended_difficulty'):
            progress.current_difficulty = adaptive['recommended_difficulty']
        
        db.session.commit()
        
        # Return response with feedback
        return jsonify({
            "correct": is_correct,
            "correct_index": question.correct_index,
            "explanation": question.explanation,
            "progress": {
                "subject": question.subject,
                "total_questions": progress.total_questions,
                "correct_answers": progress.correct_answers,
                "accuracy": round(accuracy, 2),
                "current_difficulty": progress.current_difficulty
            },
            "adaptive": adaptive,
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Failed to submit answer", "details": str(e)}), 500


