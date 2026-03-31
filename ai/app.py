"""Standalone FastAPI app for topic-based MCQ generation."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv


AI_ROOT = Path(__file__).resolve().parent
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

load_dotenv(AI_ROOT / ".env", override=False)
load_dotenv(AI_ROOT.parent / ".env", override=False)

from mcq.generator import get_mcq_generator
from mcq.validator import MCQValidator


_MODEL_READY = False
_PRELOAD_STARTED = False
_PRELOAD_ERROR: Optional[str] = None


def _ensure_generator_loaded():
    return get_mcq_generator()


def _preload_model_blocking() -> None:
    global _MODEL_READY, _PRELOAD_STARTED, _PRELOAD_ERROR
    _PRELOAD_STARTED = True
    started = time.perf_counter()
    try:
        generator = _ensure_generator_loaded()
        if hasattr(generator, "ensure_model_loaded"):
            generator.ensure_model_loaded()
        _MODEL_READY = bool(getattr(generator, "is_model_loaded", lambda: True)())
        if not _MODEL_READY:
            raise RuntimeError("Model failed to report ready state after preload.")

        _PRELOAD_ERROR = None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        print(f"[topic-mcq] model preload completed in {elapsed_ms}ms")
    except Exception as exc:
        _MODEL_READY = False
        _PRELOAD_ERROR = str(exc)
        print(f"[topic-mcq] preload failed: {exc}")
        raise


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _preload_model_blocking()

    yield


app = FastAPI(title="Elevate Topic MCQ Service", version="1.0.0", lifespan=lifespan)


@app.get("/")
def root() -> dict:
    if not _MODEL_READY:
        raise HTTPException(status_code=503, detail="Model is still loading.")

    return {
        "status": "ok",
        "service": "topic-mcq",
        "message": "Service is running",
        "health_endpoint": "/health",
        "generate_endpoint": "/mcq/generate",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


def _get_required_service_token() -> str:
    token = (
        os.environ.get("AI_TOPIC_SERVICE_TOKEN")
        or os.environ.get("TOPIC_AI_SERVICE_TOKEN")
        or ""
    )
    return str(token).strip()


def _get_auth_scheme() -> str:
    scheme = os.environ.get("AI_TOPIC_SERVICE_AUTH_SCHEME", "Bearer")
    return str(scheme or "Bearer").strip() or "Bearer"


def _enforce_service_auth(authorization_header: Optional[str]) -> None:
    expected_token = _get_required_service_token()
    if not expected_token:
        return

    if not authorization_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")

    provided = str(authorization_header).strip()
    expected_prefix = f"{_get_auth_scheme()} "
    if provided.lower().startswith(expected_prefix.lower()):
        provided = provided[len(expected_prefix):].strip()

    if provided != expected_token:
        raise HTTPException(status_code=401, detail="Invalid service token.")


class GenerateMCQRequest(BaseModel):
    source_type: str = Field(default="topic")
    source: str = Field(..., min_length=1)
    num_questions: int = Field(default=5, ge=1, le=50)
    difficulty: str = Field(default="medium")
    subject: str = Field(default="science")
    grade: str = Field(default="high")
    seed: Optional[int] = None
    test_title: Optional[str] = None
    test_description: Optional[str] = None


class ScoreMCQRequest(BaseModel):
    mcqs: List[Dict]
    user_answers: Dict[int, str]


@app.get("/health")
def health() -> dict:
    generator = _ensure_generator_loaded()
    model_ready = bool(getattr(generator, "is_model_loaded", lambda: _MODEL_READY)())

    model_device = None
    llm = getattr(generator, "llm", None)
    if model_ready and llm is not None:
        model_device = str(getattr(llm, "device", "cpu"))

    payload = {
        "status": "ok",
        "service": "topic-mcq",
        "model_ready": model_ready,
        "preload_started": _PRELOAD_STARTED,
        "preload_error": _PRELOAD_ERROR,
        "device": model_device,
    }
    if not model_ready:
        payload["status"] = "starting"
        return JSONResponse(status_code=503, content=payload)
    return payload

@app.post("/warmup")
def warmup(authorization: Optional[str] = Header(default=None, alias="Authorization")) -> dict:
    global _MODEL_READY
    _enforce_service_auth(authorization)
    started = time.perf_counter()
    generator = _ensure_generator_loaded()
    if hasattr(generator, "ensure_model_loaded"):
        generator.ensure_model_loaded()
    _MODEL_READY = bool(getattr(generator, "is_model_loaded", lambda: True)())
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "status": "ready",
        "service": "topic-mcq",
        "model_ready": _MODEL_READY,
        "warmup_ms": elapsed_ms,
    }


@app.post("/mcq/generate")
def generate_mcqs(
    payload: GenerateMCQRequest,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict:
    global _MODEL_READY
    _enforce_service_auth(authorization)
    if payload.source_type.lower() != "topic":
        raise HTTPException(status_code=400, detail="Only source_type='topic' is supported.")

    started = time.perf_counter()
    print(
        "[topic-mcq] generate request "
        f"topic={payload.source} subject={payload.subject} grade={payload.grade} "
        f"difficulty={payload.difficulty} count={payload.num_questions}"
    )

    generator = _ensure_generator_loaded()
    if not bool(getattr(generator, "is_model_loaded", lambda: _MODEL_READY)()):
        raise HTTPException(status_code=503, detail="Model is not loaded yet. Please retry shortly.")

    try:
        mcqs = generator.generate_from_topic(
            topic=payload.source,
            num_questions=payload.num_questions,
            difficulty=payload.difficulty,
            subject=payload.subject,
            grade=payload.grade,
            seed=payload.seed,
            test_title=payload.test_title,
            test_description=payload.test_description,
        )
        _MODEL_READY = bool(getattr(generator, "is_model_loaded", lambda: _MODEL_READY)())
    except Exception as exc:
        # Log the full error for debugging, but raise a generic one to the client
        print(f"[topic-mcq] generation failed with exception: {exc}")
        raise HTTPException(status_code=500, detail=f"MCQ generation failed internally.")

    # The new parser in the generator is now the source of truth for validation.
    # If it returns an empty list, we pass it on. The backend will handle the fallback.
    valid_mcqs = mcqs

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    meta = getattr(generator, "last_generation_meta", {}) or {}
    print(
        f"[topic-mcq] generate response "
        f"count={len(valid_mcqs)} latency_ms={elapsed_ms} "
        f"llm_count={meta.get('llm_count')} "
        f"cache_hit={meta.get('cache_hit')}"
    )

    return {
        "source_type": "topic",
        "source": payload.source,
        "count": len(valid_mcqs),
        "difficulty": payload.difficulty,
        "subject": payload.subject,
        "grade": payload.grade,
        "mcqs": valid_mcqs,
        "meta": {
            **meta,
            "latency_ms": elapsed_ms,
        },
    }


@app.post("/mcq/score")
def score_mcqs(payload: ScoreMCQRequest) -> dict:
    return MCQValidator.score_answers(payload.mcqs, payload.user_answers)


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
