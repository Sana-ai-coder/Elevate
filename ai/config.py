"""Configuration for the standalone AI topic MCQ service."""

from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
MODELS_DIR = ROOT_DIR / "models_cache"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


LLM_MODEL = os.environ.get("LLM_MODEL", "TinyLlama/TinyLlama-1.1B-Chat-v1.0")
LORA_ADAPTER_PATH = (os.environ.get("LORA_ADAPTER_PATH") or "").strip() or None

HF_TOKEN = (
    os.environ.get("HF_TOKEN")
    or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
    or ""
).strip()
if HF_TOKEN:
    # Keep aliases in sync so transformers/huggingface_hub can pick up auth reliably.
    os.environ.setdefault("HF_TOKEN", HF_TOKEN)
    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", HF_TOKEN)
    os.environ.setdefault("HUGGINGFACEHUB_API_TOKEN", HF_TOKEN)

# Strict behavior defaults
LLM_ONLY_MODE = _env_bool("LLM_ONLY_MODE", True)
ENABLE_TEMPLATE_FALLBACK = _env_bool("ENABLE_TEMPLATE_FALLBACK", False)
LLM_MAX_ATTEMPTS = int(os.environ.get("LLM_MAX_ATTEMPTS", "18"))
LLM_TOTAL_TIME_BUDGET_SECONDS = float(os.environ.get("LLM_TOTAL_TIME_BUDGET_SECONDS", "120"))

# Generation controls
MAX_PROMPT_TOKENS = int(os.environ.get("MAX_PROMPT_TOKENS", "1200"))
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "420"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.3"))
TOP_P = float(os.environ.get("TOP_P", "0.92"))
LLM_GENERATE_MAX_TIME_SECONDS = float(os.environ.get("LLM_GENERATE_MAX_TIME_SECONDS", "18"))

# Context retrieval
WEB_CONTEXT_MAX_CHARS = int(os.environ.get("WEB_CONTEXT_MAX_CHARS", "3200"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "4"))

# Runtime tuning
LLM_BATCH_SIZE = int(os.environ.get("LLM_BATCH_SIZE", "4"))
MAX_LLM_QUESTIONS_PER_REQUEST = int(os.environ.get("MAX_LLM_QUESTIONS_PER_REQUEST", "8"))
FACT_SENTENCE_MIN_CHARS = int(os.environ.get("FACT_SENTENCE_MIN_CHARS", "55"))
CPU_LLM_MAX_TARGET = int(os.environ.get("CPU_LLM_MAX_TARGET", "2"))
CPU_LLM_MAX_ATTEMPTS = int(os.environ.get("CPU_LLM_MAX_ATTEMPTS", "2"))
CPU_LLM_MAX_NEW_TOKENS = int(os.environ.get("CPU_LLM_MAX_NEW_TOKENS", "96"))
CPU_LLM_DISABLE_THRESHOLD = int(os.environ.get("CPU_LLM_DISABLE_THRESHOLD", "8"))
