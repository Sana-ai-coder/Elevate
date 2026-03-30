"""HTTP client for the standalone topic MCQ AI service."""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, Dict, List
from urllib import error as urlerror
from urllib import request as urlrequest

from .validation import sanitize_string


DEFAULT_TOPIC_AI_SERVICE_URL = "http://127.0.0.1:7860"
DEFAULT_TOPIC_AI_TIMEOUT_SECONDS = 120


TRUTHY_VALUES = {"1", "true", "yes", "y", "on"}
FALSY_VALUES = {"0", "false", "no", "n", "off"}


def _parse_bool(raw_value: Any, default: bool) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return raw_value != 0
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if value in TRUTHY_VALUES:
            return True
        if value in FALSY_VALUES:
            return False
    return default


def get_topic_ai_service_url() -> str:
    url = (
        os.environ.get("AI_TOPIC_SERVICE_URL")
        or os.environ.get("TOPIC_AI_SERVICE_URL")
        or DEFAULT_TOPIC_AI_SERVICE_URL
    )
    return str(url).strip().rstrip("/")


def get_topic_ai_timeout_seconds() -> int:
    raw_value = os.environ.get("AI_TOPIC_SERVICE_TIMEOUT_SECONDS", DEFAULT_TOPIC_AI_TIMEOUT_SECONDS)
    try:
        timeout = int(raw_value)
    except (TypeError, ValueError):
        timeout = DEFAULT_TOPIC_AI_TIMEOUT_SECONDS
    return max(5, min(timeout, 300))


def get_topic_ai_service_token() -> str:
    token = (
        os.environ.get("AI_TOPIC_SERVICE_TOKEN")
        or os.environ.get("TOPIC_AI_SERVICE_TOKEN")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        or ""
    )
    return str(token).strip()


def get_topic_ai_auth_scheme() -> str:
    scheme = os.environ.get("AI_TOPIC_SERVICE_AUTH_SCHEME", "Bearer")
    return str(scheme or "Bearer").strip() or "Bearer"


def topic_ai_default_llm_only_mode() -> bool:
    return _parse_bool(os.environ.get("AI_TOPIC_LLM_ONLY_DEFAULT"), True)


def is_topic_ai_service_available() -> bool:
    return bool(get_topic_ai_service_url())


def _derive_source_topic(topic: str | None, subject: str, grade: str) -> str:
    clean_topic = sanitize_string(topic or "", max_length=128) or ""
    if clean_topic:
        return clean_topic

    clean_subject = sanitize_string(subject or "", max_length=64) or "general"
    clean_grade = sanitize_string(grade or "", max_length=32) or "general"
    return f"{clean_subject} {clean_grade}".strip()


def _normalize_options(raw_options: Any) -> List[str]:
    options: List[str] = []
    if isinstance(raw_options, list):
        options = [sanitize_string(str(opt), max_length=300) for opt in raw_options]
    elif isinstance(raw_options, dict):
        ordered = []
        for key in ("A", "B", "C", "D", "a", "b", "c", "d"):
            value = raw_options.get(key)
            if value is not None:
                ordered.append(value)
        options = [sanitize_string(str(opt), max_length=300) for opt in ordered]

    clean_options: List[str] = []
    seen = set()
    for option in options:
        if not option:
            continue
        key = " ".join(option.strip().lower().split())
        if key in seen:
            continue
        seen.add(key)
        clean_options.append(option.strip())
        if len(clean_options) >= 4:
            break

    return clean_options


def _extract_correct_index(item: Dict[str, Any], options: List[str]) -> int:
    try:
        index = int(item.get("correct_index"))
        if 0 <= index < len(options):
            return index
    except (TypeError, ValueError):
        pass

    answer = sanitize_string(item.get("correct_answer") or item.get("answer") or "", max_length=16)
    if answer:
        normalized = answer.strip().upper()
        if normalized in {"A", "B", "C", "D"}:
            candidate = ["A", "B", "C", "D"].index(normalized)
            if candidate < len(options):
                return candidate

    return 0


def _normalize_service_question(item: Any, default_topic: str) -> Dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    text = sanitize_string(item.get("question") or item.get("text") or "", max_length=4000)
    if not text:
        return None

    options = _normalize_options(item.get("options"))
    if len(options) < 2:
        return None

    correct_index = _extract_correct_index(item, options)
    if correct_index < 0 or correct_index >= len(options):
        correct_index = 0

    return {
        "text": text,
        "options": options,
        "correct_index": correct_index,
        "hint": sanitize_string(item.get("hint") or "", max_length=500) or None,
        "explanation": sanitize_string(item.get("explanation") or "", max_length=1500) or None,
        "topic": sanitize_string(item.get("topic") or default_topic or "", max_length=128) or None,
    }


def _extract_error_message(raw_body: str) -> str:
    body = (raw_body or "").strip()
    if not body:
        return "Topic AI service returned an empty error response."

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return body

    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, dict):
            nested_message = detail.get("message") or detail.get("error")
            if nested_message:
                return str(nested_message)
        if parsed.get("error"):
            return str(parsed.get("error"))

    return body


def generate_topic_mcqs(
    *,
    subject: str,
    grade: str,
    difficulty: str,
    topic: str | None,
    count: int,
    seed: int | None = None,
    llm_only: bool | None = None,
) -> Dict[str, Any]:
    requested_count = max(1, min(int(count), 50))
    effective_llm_only = topic_ai_default_llm_only_mode() if llm_only is None else bool(llm_only)
    source_topic = _derive_source_topic(topic, subject, grade)
    endpoint = f"{get_topic_ai_service_url()}/mcq/generate"
    request_started = time.perf_counter()

    def _elapsed_ms() -> int:
        return max(0, int((time.perf_counter() - request_started) * 1000))

    payload = {
        "source_type": "topic",
        "source": source_topic,
        "num_questions": requested_count,
        "difficulty": str(difficulty or "medium").strip().lower(),
        "subject": str(subject or "science").strip().lower(),
        "grade": str(grade or "high").strip().lower(),
        "seed": seed,
        "llm_only": effective_llm_only,
    }

    request_body = json.dumps(payload).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
    }

    token = get_topic_ai_service_token()
    if token:
        request_headers["Authorization"] = f"{get_topic_ai_auth_scheme()} {token}"

    request_obj = urlrequest.Request(
        endpoint,
        data=request_body,
        method="POST",
        headers=request_headers,
    )

    timeout = get_topic_ai_timeout_seconds()

    try:
        with urlrequest.urlopen(request_obj, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw_response = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8")
        except Exception:
            error_body = ""

        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 502) or 502),
            "error": _extract_error_message(error_body) or f"Topic AI service HTTP {exc.code}",
            "questions": [],
            "meta": {},
            "requested_count": requested_count,
            "generated_count": 0,
            "llm_only": effective_llm_only,
            "service_url": get_topic_ai_service_url(),
            "service_endpoint": endpoint,
            "service_latency_ms": _elapsed_ms(),
        }
    except (urlerror.URLError, socket.timeout, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "status_code": 503,
            "error": f"Topic AI service unavailable: {exc}",
            "questions": [],
            "meta": {},
            "requested_count": requested_count,
            "generated_count": 0,
            "llm_only": effective_llm_only,
            "service_url": get_topic_ai_service_url(),
            "service_endpoint": endpoint,
            "service_latency_ms": _elapsed_ms(),
        }

    if status_code < 200 or status_code >= 300:
        return {
            "ok": False,
            "status_code": status_code,
            "error": _extract_error_message(raw_response),
            "questions": [],
            "meta": {},
            "requested_count": requested_count,
            "generated_count": 0,
            "llm_only": effective_llm_only,
            "service_url": get_topic_ai_service_url(),
            "service_endpoint": endpoint,
            "service_latency_ms": _elapsed_ms(),
        }

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status_code": 502,
            "error": "Topic AI service returned invalid JSON.",
            "questions": [],
            "meta": {},
            "requested_count": requested_count,
            "generated_count": 0,
            "llm_only": effective_llm_only,
            "service_url": get_topic_ai_service_url(),
            "service_endpoint": endpoint,
            "service_latency_ms": _elapsed_ms(),
        }

    rows = []
    if isinstance(parsed, dict):
        candidates = parsed.get("mcqs")
        if isinstance(candidates, list):
            for candidate in candidates:
                normalized = _normalize_service_question(candidate, source_topic)
                if normalized:
                    rows.append(normalized)
                    if len(rows) >= requested_count:
                        break

    meta = parsed.get("meta") if isinstance(parsed, dict) and isinstance(parsed.get("meta"), dict) else {}

    return {
        "ok": True,
        "status_code": status_code,
        "error": None,
        "questions": rows,
        "meta": meta,
        "requested_count": requested_count,
        "generated_count": len(rows),
        "llm_only": effective_llm_only,
        "service_url": get_topic_ai_service_url(),
        "service_endpoint": endpoint,
        "service_latency_ms": _elapsed_ms(),
    }
