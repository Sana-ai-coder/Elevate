"""HTTP client for strict ML training orchestration on Hugging Face Space."""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from .ai_topic_service import _normalize_topic_ai_service_url


DEFAULT_SERVICE_URL = "http://127.0.0.1:7860"


def _read_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw is not None else int(default)
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, min(value, maximum))


def _read_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw is not None else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(minimum, min(value, maximum))


def _read_optional_timeout_env(name: str, default: float | None = None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return None
    return value


def get_hf_training_service_url() -> str:
    raw_url = (
        os.environ.get("HF_ML_TRAINING_SERVICE_URL")
        or os.environ.get("AI_TOPIC_SERVICE_URL")
        or DEFAULT_SERVICE_URL
    )
    return _normalize_topic_ai_service_url(str(raw_url).strip())


def get_hf_training_service_token() -> str:
    token = (
        os.environ.get("HF_ML_TRAINING_SERVICE_TOKEN")
        or os.environ.get("AI_TOPIC_SERVICE_TOKEN")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        or ""
    )
    return str(token).strip()


def get_hf_training_auth_scheme() -> str:
    scheme = os.environ.get("HF_ML_TRAINING_AUTH_SCHEME", "Bearer")
    return str(scheme or "Bearer").strip() or "Bearer"


def _request_json(*, method: str, endpoint: str, payload: dict[str, Any] | None, timeout_seconds: float | None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}

    token = get_hf_training_service_token()
    if token:
        headers["Authorization"] = f"{get_hf_training_auth_scheme()} {token}"

    req = urlrequest.Request(endpoint, data=body, method=method.upper(), headers=headers)
    started = time.perf_counter()

    def _latency_ms() -> int:
        return max(0, int((time.perf_counter() - started) * 1000))

    try:
        if timeout_seconds is None:
            with urlrequest.urlopen(req) as response:
                status_code = int(getattr(response, "status", 200) or 200)
                raw = response.read().decode("utf-8")
        else:
            with urlrequest.urlopen(req, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200) or 200)
                raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8")
        except Exception:
            raw = ""
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 502) or 502),
            "error": raw.strip() or f"HTTP {getattr(exc, 'code', 502)}",
            "latency_ms": _latency_ms(),
            "endpoint": endpoint,
        }
    except (urlerror.URLError, socket.timeout, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "status_code": 503,
            "error": f"Service unavailable: {exc}",
            "latency_ms": _latency_ms(),
            "endpoint": endpoint,
        }

    if status_code < 200 or status_code >= 300:
        return {
            "ok": False,
            "status_code": status_code,
            "error": raw.strip() or f"HTTP {status_code}",
            "latency_ms": _latency_ms(),
            "endpoint": endpoint,
        }

    parsed: dict[str, Any]
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status_code": 502,
            "error": "Invalid JSON response from HF training service",
            "latency_ms": _latency_ms(),
            "endpoint": endpoint,
        }

    return {
        "ok": True,
        "status_code": status_code,
        "payload": parsed,
        "latency_ms": _latency_ms(),
        "endpoint": endpoint,
    }


def start_hf_strict_training() -> dict[str, Any]:
    service_url = get_hf_training_service_url()
    endpoint = f"{service_url}/training/strict/start"
    timeout_seconds = _read_optional_timeout_env("HF_ML_TRAINING_REQUEST_TIMEOUT_SECONDS", default=None)

    payload = {
        "github_repo_url": str(os.environ.get("HF_ML_TRAINING_GITHUB_REPO_URL") or "").strip() or None,
        "github_ref": str(os.environ.get("HF_ML_TRAINING_GITHUB_REF") or "main").strip() or "main",
        "min_emotion_accuracy": _read_float_env("HF_ML_MIN_EMOTION_ACCURACY", 0.90, 0.50, 0.999),
        "process_count": _read_int_env("HF_ML_TRAINING_PROCESSES", 4, 1, 16),
    }
    return _request_json(method="POST", endpoint=endpoint, payload=payload, timeout_seconds=timeout_seconds)


def get_hf_strict_training_status(job_id: str) -> dict[str, Any]:
    service_url = get_hf_training_service_url()
    endpoint = f"{service_url}/training/strict/status/{job_id}"
    timeout_seconds = _read_optional_timeout_env("HF_ML_TRAINING_STATUS_REQUEST_TIMEOUT_SECONDS", default=None)
    return _request_json(method="GET", endpoint=endpoint, payload=None, timeout_seconds=timeout_seconds)


def wait_for_hf_strict_training(job_id: str) -> dict[str, Any]:
    poll_seconds = _read_int_env("HF_ML_TRAINING_POLL_SECONDS", 20, 1, 3600)

    while True:
        status = get_hf_strict_training_status(job_id)
        if not status.get("ok"):
            return status

        payload = status.get("payload") if isinstance(status.get("payload"), dict) else {}
        state = str(payload.get("status") or "").strip().lower()

        if state in {"succeeded", "failed"}:
            return status

        time.sleep(poll_seconds)


def trigger_and_wait_hf_strict_training() -> dict[str, Any]:
    started = start_hf_strict_training()
    if not started.get("ok"):
        return started

    payload = started.get("payload") if isinstance(started.get("payload"), dict) else {}
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        return {
            "ok": False,
            "status_code": 502,
            "error": "HF training start response missing job_id",
            "endpoint": started.get("endpoint"),
            "latency_ms": started.get("latency_ms"),
        }

    return wait_for_hf_strict_training(job_id)