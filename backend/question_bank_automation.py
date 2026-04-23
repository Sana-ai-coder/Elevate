from __future__ import annotations

import json
import os
import random
import re
import threading
from datetime import timedelta

from .ai_topic_service import generate_topic_mcqs
from .models import MCQPipelineEvent, Question, QuestionAutomationState, db, utcnow
from .validation import sanitize_string
from sqlalchemy import inspect as sa_inspect

_LOCK = threading.Lock()
_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_APP = None


def _normalize_text_key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().lower())
    normalized = re.sub(r"[^a-z0-9 ]", "", normalized)
    return normalized


def _manifest_path() -> str:
    return os.path.join(os.path.dirname(__file__), "data", "syllabus_topics.json")


def _load_manifest() -> dict:
    path = _manifest_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _iter_variants(manifest: dict):
    variants = []
    for subject, payload in manifest.items():
        subject_value = sanitize_string(subject, max_length=64).strip().lower()
        if not subject_value:
            continue
        if isinstance(payload, dict):
            for grade, topics in payload.items():
                grade_value = sanitize_string(grade, max_length=32).strip().lower()
                if not grade_value or not isinstance(topics, list):
                    continue
                levels = ["easy", "medium", "hard"]
                if grade_value == "college":
                    levels.append("expert")
                for topic in topics:
                    topic_value = sanitize_string(str(topic), max_length=128).strip().lower().replace("-", "_").replace(" ", "_")
                    if not topic_value:
                        continue
                    for difficulty in levels:
                        variants.append((subject_value, grade_value, difficulty, topic_value))
        elif isinstance(payload, list):
            grade_value = "high"
            for topic in payload:
                topic_value = sanitize_string(str(topic), max_length=128).strip().lower().replace("-", "_").replace(" ", "_")
                if not topic_value:
                    continue
                for difficulty in ("easy", "medium", "hard"):
                    variants.append((subject_value, grade_value, difficulty, topic_value))
    return variants


def _get_or_create_state() -> QuestionAutomationState:
    _ensure_automation_table()
    state = db.session.get(QuestionAutomationState, 1)
    if not state:
        # Default ON so production keeps growing the bank continuously.
        state = QuestionAutomationState(id=1, is_enabled=True, hourly_batch_size=10, total_generated_count=0)
        db.session.add(state)
        db.session.commit()
    return state


def _ensure_automation_table() -> None:
    """Create automation table if missing (Supabase-safe best-effort)."""
    try:
        inspector = sa_inspect(db.engine)
        if "question_automation_state" in set(inspector.get_table_names()):
            return
        QuestionAutomationState.__table__.create(bind=db.engine, checkfirst=True)
        db.session.commit()
    except Exception:
        # If the DB user cannot create tables, callers will raise when querying.
        db.session.rollback()


def _existing_text_keys() -> set[str]:
    try:
        return {_normalize_text_key(row[0]) for row in db.session.query(Question.text).all() if row and row[0]}
    except Exception:
        db.session.rollback()
        return set()


def _normalize_generated_item(item: dict, subject: str, grade: str, difficulty: str, topic: str):
    if not isinstance(item, dict):
        return None
    text = sanitize_string(item.get("text") or item.get("question") or "", max_length=4000)
    options = item.get("options") if isinstance(item.get("options"), list) else []
    options = [sanitize_string(str(opt), max_length=300) for opt in options]
    options = [opt for opt in options if opt]
    if not text or len(options) < 2:
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
        "difficulty": difficulty if difficulty in {"easy", "medium", "hard", "expert"} else "medium",
        "text": text,
        "options": options[:4],
        "correct_index": correct_index,
        "hint": sanitize_string(item.get("hint") or "", max_length=500) or None,
        "explanation": sanitize_string(item.get("explanation") or "", max_length=1500) or None,
        "syllabus_topic": topic,
        "is_generated": True,
        "generation_meta": {
            "source": sanitize_string(item.get("source") or "topic_ai_service", max_length=64) or "topic_ai_service",
            "auto_generated": True,
            "generated_at": utcnow().isoformat(),
        },
    }


def generate_ai_question_batch(*, target_count: int, source: str, started_by: int | None = None) -> dict:
    _ensure_automation_table()
    target = max(1, min(int(target_count or 1), 2000))
    manifest = _load_manifest()
    variants = _iter_variants(manifest)
    if not variants:
        return {"requested": target, "generated": 0, "duplicates": 0, "errors": ["syllabus_topics.json missing or empty"]}

    random.shuffle(variants)
    variants_cycle = list(variants)
    created = 0
    duplicates = 0
    errors: list[str] = []
    seen_keys = _existing_text_keys()
    idx = 0

    while created < target and idx < max(len(variants_cycle) * 12, target * 3):
        subject, grade, difficulty, topic = variants_cycle[idx % len(variants_cycle)]
        idx += 1
        needed = min(10, target - created)

        result = generate_topic_mcqs(
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            topic=topic,
            count=needed,
            seed=random.randint(1, 2_147_483_647),
            generation_mode="standard",
        )
        if not result.get("ok"):
            err = sanitize_string(result.get("error") or "generation failed", max_length=250) or "generation failed"
            if err not in errors:
                errors.append(err)
            continue

        payload = result.get("questions") or []
        for item in payload:
            normalized = _normalize_generated_item(item, subject, grade, difficulty, topic)
            if not normalized:
                continue
            key = _normalize_text_key(normalized["text"])
            if not key or key in seen_keys:
                duplicates += 1
                continue
            seen_keys.add(key)
            db.session.add(Question(**normalized))
            created += 1
            if created >= target:
                break

    db.session.commit()

    event = MCQPipelineEvent(
        triggered_by=started_by,
        subject="automation",
        grade=None,
        difficulty=None,
        topic=source,
        requested_count=target,
        generated_count=created,
        failed_count=max(0, target - created),
        fallback_used=False,
        outcome="success" if created >= target else ("partial" if created > 0 else "failed"),
        llm_provider="gemini",
        latency_ms=None,
        error_message="; ".join(errors[:3]) if errors else None,
    )
    db.session.add(event)
    db.session.commit()
    return {"requested": target, "generated": created, "duplicates": duplicates, "errors": errors}


def _run_hourly_generation_tick():
    with _LOCK:
        state = _get_or_create_state()
        if not state.is_enabled:
            return
        now = utcnow()
        should_run = not state.last_run_at or (now - state.last_run_at) >= timedelta(hours=1)
        if not should_run:
            if not state.next_run_at:
                state.next_run_at = state.last_run_at + timedelta(hours=1) if state.last_run_at else now + timedelta(hours=1)
                db.session.commit()
            return

        try:
            stats = generate_ai_question_batch(
                target_count=state.hourly_batch_size or 10,
                source="hourly_auto",
                started_by=state.started_by,
            )
            state.last_generated_count = int(stats.get("generated") or 0)
            state.total_generated_count = int(state.total_generated_count or 0) + int(state.last_generated_count or 0)
            state.last_error = "; ".join((stats.get("errors") or [])[:2]) or None
        except Exception as exc:
            db.session.rollback()
            state.last_generated_count = 0
            state.last_error = str(exc)[:300]
        finally:
            state.last_run_at = utcnow()
            state.next_run_at = state.last_run_at + timedelta(hours=1)
            db.session.add(state)
            db.session.commit()


def _run_startup_bootstrap_if_needed():
    with _LOCK:
        state = _get_or_create_state()
        target_min = max(100, min(int(os.environ.get("QUESTION_AUTOMATION_BOOTSTRAP_COUNT") or 700), 5000))
        current_generated = int(
            db.session.query(Question.id)
            .filter(Question.is_generated.is_(True))
            .count()
        )
        if current_generated >= target_min:
            return

        missing = target_min - current_generated
        try:
            stats = generate_ai_question_batch(
                target_count=missing,
                source="startup_bootstrap",
                started_by=state.started_by,
            )
            generated_now = int(stats.get("generated") or 0)
            state.last_generated_count = generated_now
            state.total_generated_count = int(state.total_generated_count or 0) + generated_now
            state.last_error = "; ".join((stats.get("errors") or [])[:2]) or None
            state.last_run_at = utcnow()
            state.next_run_at = state.last_run_at + timedelta(hours=1)
            db.session.add(state)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            state.last_error = str(exc)[:300]
            db.session.add(state)
            db.session.commit()


def _worker_loop():
    while not _STOP_EVENT.wait(20):
        if _APP is None:
            continue
        with _APP.app_context():
            _run_startup_bootstrap_if_needed()
            _run_hourly_generation_tick()


def initialize_question_automation_worker(app):
    global _APP, _WORKER_THREAD
    _APP = app
    with app.app_context():
        _get_or_create_state()
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, daemon=True, name="question-automation-worker")
    _WORKER_THREAD.start()


def get_question_automation_status() -> dict:
    with _LOCK:
        try:
            state = _get_or_create_state()
            return state.as_dict()
        except Exception as exc:
            db.session.rollback()
            return {
                "id": 1,
                "is_enabled": False,
                "hourly_batch_size": 10,
                "last_run_at": None,
                "next_run_at": None,
                "last_generated_count": 0,
                "total_generated_count": 0,
                "last_error": f"automation_unavailable: {str(exc)[:240]}",
                "started_by": None,
                "stopped_by": None,
                "updated_at": None,
            }


def start_hourly_question_automation(*, started_by: int | None = None, hourly_batch_size: int = 10) -> dict:
    with _LOCK:
        state = _get_or_create_state()
        state.is_enabled = True
        state.hourly_batch_size = max(1, min(int(hourly_batch_size or 10), 100))
        state.started_by = started_by
        state.stopped_by = None
        if not state.next_run_at:
            state.next_run_at = utcnow() + timedelta(hours=1)
        db.session.commit()
        return state.as_dict()


def stop_hourly_question_automation(*, stopped_by: int | None = None) -> dict:
    with _LOCK:
        state = _get_or_create_state()
        state.is_enabled = False
        state.stopped_by = stopped_by
        state.next_run_at = None
        db.session.commit()
        return state.as_dict()
