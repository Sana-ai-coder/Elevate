"""HTTP client for the standalone topic MCQ AI service."""

from __future__ import annotations

import json
import os
import re
import socket
import time
from typing import Any, Dict, List
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse

from .validation import sanitize_string

import concurrent.futures


DEFAULT_TOPIC_AI_SERVICE_URL = "http://127.0.0.1:7860"
DEFAULT_TOPIC_AI_TIMEOUT_SECONDS = 180


def _to_hf_space_subdomain(owner: str, space: str) -> str:
    combined = f"{owner}-{space}".strip().lower().replace("_", "-")
    combined = re.sub(r"[^a-z0-9-]", "-", combined)
    combined = re.sub(r"-+", "-", combined).strip("-")
    return combined


def _normalize_topic_ai_service_url(raw_url: str) -> str:
    candidate = str(raw_url or "").strip()
    if not candidate:
        return DEFAULT_TOPIC_AI_SERVICE_URL

    # Allow compact "owner/space" format for convenience.
    if "://" not in candidate and candidate.count("/") == 1 and " " not in candidate:
        owner, space = [segment.strip() for segment in candidate.split("/", 1)]
        if owner and space:
            return f"https://{_to_hf_space_subdomain(owner, space)}.hf.space"

    if candidate.startswith("huggingface.co/"):
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    host = (parsed.netloc or "").strip().lower()

    if host in {"huggingface.co", "www.huggingface.co"}:
        segments = [segment for segment in (parsed.path or "").split("/") if segment]
        if len(segments) >= 3 and segments[0].lower() == "spaces":
            owner = segments[1]
            space = segments[2]
            return f"https://{_to_hf_space_subdomain(owner, space)}.hf.space"

    if host.endswith(".hf.space"):
        scheme = (parsed.scheme or "https").strip().lower()
        return f"{scheme}://{host}"

    return candidate.rstrip("/")


def get_topic_ai_service_url() -> str:
    raw_url = (
        os.environ.get("AI_TOPIC_SERVICE_URL")
        or os.environ.get("TOPIC_AI_SERVICE_URL")
        or DEFAULT_TOPIC_AI_SERVICE_URL
    )
    return _normalize_topic_ai_service_url(raw_url)


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

def _calculate_smart_batches(total_questions: int, max_parallel: int = 5) -> list[int]:
    """
    Intelligently splits the requested questions across parallel workers.
    Example: 7 questions -> [2, 2, 1, 1, 1]
    Example: 30 questions -> [6, 6, 6, 6, 6]
    """
    if total_questions <= 0:
        return []
    
    # If they ask for less than 5 questions, we don't need 5 workers
    workers = min(total_questions, max_parallel)
    base = total_questions // workers
    remainder = total_questions % workers
    
    batches = []
    for i in range(workers):
        if i < remainder:
            batches.append(base + 1)
        else:
            batches.append(base)
            
    return [b for b in batches if b > 0]

def _fetch_single_batch(
    count: int, subject: str, grade: str, difficulty: str, source_topic: str, 
    seed: int, test_title: str, test_description: str, base_url: str, endpoint: str, headers: dict, timeout: int
) -> list:
    """Helper function to execute a single HTTP request to your custom HF Space."""
    import json
    from urllib import request as urlrequest
    
    payload = {
        "source_type": "topic",
        "source": source_topic,
        "num_questions": count,
        "difficulty": difficulty.strip().lower(),
        "subject": subject.strip().lower(),
        "grade": grade.strip().lower(),
        "seed": seed,
        "test_title": test_title,
        "test_description": test_description
    }
    
    request_body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(endpoint, data=request_body, method="POST", headers=headers)
    
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            return parsed.get("mcqs", [])
    except Exception as e:
        print(f"[Batch Error] Failed to generate chunk of {count}: {e}")
        return []


def generate_topic_mcqs(
    *,
    subject: str,
    grade: str,
    difficulty: str,
    topic: str | None,
    count: int,
    seed: int | None = None,
    test_title: str | None = None,
    test_description: str | None = None,
) -> Dict[str, Any]:
    import time
    import json
    from urllib import request as urlrequest

    requested_count = max(1, min(int(count), 50))
    source_topic = _derive_source_topic(topic, subject, grade)
    base_url = get_topic_ai_service_url()
    endpoint = f"{base_url}/mcq/generate"
    
    request_started = time.perf_counter()

    payload = {
        "source_type": "topic",
        "source": source_topic,
        "num_questions": requested_count,
        "difficulty": str(difficulty or "medium").strip().lower(),
        "subject": str(subject or "science").strip().lower(),
        "grade": str(grade or "high").strip().lower(),
        "seed": seed,
    }
    if test_title: payload["test_title"] = sanitize_string(test_title, 255)
    if test_description: payload["test_description"] = sanitize_string(test_description, 1000)

    request_body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}

    token = get_topic_ai_service_token()
    if token:
        request_headers["Authorization"] = f"{get_topic_ai_auth_scheme()} {token}"

    request_obj = urlrequest.Request(endpoint, data=request_body, method="POST", headers=request_headers)
    timeout = get_topic_ai_timeout_seconds()

    try:
        with urlrequest.urlopen(request_obj, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw_response = response.read().decode("utf-8")
    except Exception as exc:
        return {
            "ok": False, "status_code": 503, "error": f"Topic AI service unavailable: {exc}",
            "questions": [], "meta": {}, "requested_count": requested_count, "generated_count": 0,
            "service_url": base_url, "service_endpoint": endpoint, "service_latency_ms": int((time.perf_counter() - request_started) * 1000),
        }

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError:
        return {
            "ok": False, "status_code": 502, "error": "Invalid JSON from AI.",
            "questions": [], "meta": {}, "requested_count": requested_count, "generated_count": 0,
            "service_url": base_url, "service_endpoint": endpoint, "service_latency_ms": int((time.perf_counter() - request_started) * 1000),
        }

    rows = []
    meta = parsed.get("meta", {})
    candidates = parsed.get("mcqs", [])
    
    if isinstance(candidates, list):
        for candidate in candidates:
            normalized = _normalize_service_question(candidate, source_topic)
            if normalized: rows.append(normalized)

    rows = rows[:requested_count]

    return {
        "ok": len(rows) > 0,
        "status_code": status_code,
        "error": None if rows else "Failed to parse questions",
        "questions": rows,
        "meta": meta,
        "requested_count": requested_count,
        "generated_count": len(rows),
        "service_url": base_url,
        "service_endpoint": endpoint,
        "service_latency_ms": int((time.perf_counter() - request_started) * 1000),
    }
