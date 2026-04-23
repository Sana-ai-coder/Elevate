"""
backend/hf_training_service.py
===============================
Thin HTTP client that talks to the Hugging Face Spaces training service.
All config comes from environment variables — zero hardcoding.
Supabase-safe: no DB calls here.
"""

import os
import time
import requests
from flask import current_app


def get_hf_training_service_url() -> str:
    return os.environ.get(
        "AI_TOPIC_SERVICE_URL",
        current_app.config.get("AI_TOPIC_SERVICE_URL", "")
    ).rstrip("/")


def _hf_headers() -> dict:
    # FIX 1: Look for AI_TOPIC_SERVICE_TOKEN to match your Render environment variables!
    token = os.environ.get(
        "AI_TOPIC_SERVICE_TOKEN",
        current_app.config.get("AI_TOPIC_SERVICE_TOKEN", "")
    )
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def start_hf_strict_training(payload: dict | None = None) -> dict:
    """
    POST to /training/strict/start on the HF training service.
    Returns: { ok, payload, latency_ms, error, status_code }
    """
    base_url = get_hf_training_service_url()
    if not base_url:
        return {
            "ok": False,
            "error": "AI_TOPIC_SERVICE_URL not configured",
            "status_code": 503,
        }

    # FIX 2: Match the app.py exact path!
    url = f"{base_url}/training/strict/start"
    t0 = time.time()
    try:
        resp = requests.post(
            url,
            json=payload or {},
            headers=_hf_headers(),
            timeout=30,
        )
        latency = int((time.time() - t0) * 1000)
        if not resp.ok:
            return {
                "ok": False,
                "error": resp.text[:512],
                "status_code": resp.status_code,
                "latency_ms": latency,
            }
        return {
            "ok": True,
            "payload": resp.json(),
            "latency_ms": latency,
            "status_code": resp.status_code,
        }
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Request timed out", "status_code": 504}
    except requests.exceptions.ConnectionError as exc:
        return {"ok": False, "error": f"Connection error: {exc}", "status_code": 503}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "status_code": 500}


def get_hf_strict_training_status(job_id: str) -> dict:
    """
    GET /training/strict/status/<job_id> on the HF training service.
    Returns: { ok, payload, latency_ms, error, status_code }
    """
    base_url = get_hf_training_service_url()
    if not base_url:
        return {
            "ok": False,
            "error": "AI_TOPIC_SERVICE_URL not configured",
            "status_code": 503,
        }

    # FIX 3: Match the app.py exact path for status checking!
    url = f"{base_url}/training/strict/status/{job_id}"
    t0 = time.time()
    try:
        resp = requests.get(url, headers=_hf_headers(), timeout=15)
        latency = int((time.time() - t0) * 1000)
        if not resp.ok:
            return {
                "ok": False,
                "error": resp.text[:512],
                "status_code": resp.status_code,
                "latency_ms": latency,
            }
        return {
            "ok": True,
            "payload": resp.json(),
            "latency_ms": latency,
            "status_code": resp.status_code,
        }
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Request timed out", "status_code": 504}
    except requests.exceptions.ConnectionError as exc:
        return {"ok": False, "error": f"Connection error: {exc}", "status_code": 503}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "status_code": 500}