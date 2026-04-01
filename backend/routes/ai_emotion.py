"""
backend/routes/ai_emotion.py
=============================
Flask Blueprint — Server-side emotion inference using the trained CNN.

Endpoint:  POST /api/ai/emotion/predict
Input:     JSON { "image": "<base64-encoded JPEG/PNG>" }
Output:    JSON {
                         "emotion"     : "happy",
                         "confidence"  : 0.9124,
                         "all_scores"  : {
                             "happy":0.42, "bored":0.08, "focused":0.11,
                             "confused":0.09, "neutral":0.13, "angry":0.07, "surprised":0.10
                         },
                         "model_info"  : { ... }
                     }

The model is loaded once at startup and cached in module scope.
Flask's development server is single-threaded by default, so no
thread-safety issues; for production (gunicorn) the load-once pattern
is also fine because workers fork after the model is in memory.
"""

from __future__ import annotations

import base64
import io
import json
import os
import logging
import time
from functools import lru_cache

import numpy as np
from flask import Blueprint, jsonify, request, current_app

# ── Optional: only import heavy libraries if available ──────────────────────
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import tensorflow as tf
    TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False

# ────────────────────────────────────────────────────────────────────────────
#  Blueprint
# ────────────────────────────────────────────────────────────────────────────
ai_emotion_bp = Blueprint("ai_emotion", __name__)
logger = logging.getLogger(__name__)

# ── Paths (relative to backend/routes/ → resolve up to project root) ────────
_THIS_DIR    = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
_MODEL_PATH  = os.path.join(_BACKEND_DIR, "ai_models", "emotion_model.h5")
_INFO_PATH   = os.path.join(_BACKEND_DIR, "ai_models", "emotion_model_info.json")

# ── Constants ────────────────────────────────────────────────────────────────
CLASS_NAMES = ["happy", "bored", "focused", "confused", "neutral", "angry", "surprised"]
LEGACY_CLASS_NAMES_6 = ["happy", "bored", "focused", "confused", "neutral", "angry"]
LEGACY_CLASS_NAMES_4 = ["angry", "confused", "happy", "neutral"]
CLASS_NAME_ALIASES = {
    "surprise": "surprised",
    "surprised": "surprised",
    "anger": "angry",
    "angry": "angry",
    "focus": "focused",
    "focused": "focused",
    "confusing": "confused",
    "confused": "confused",
    "joy": "happy",
    "happy": "happy",
    "calm": "neutral",
    "neutral": "neutral",
    "bore": "bored",
    "bored": "bored",
}
IMG_SIZE    = (96, 96)
MAX_B64_BYTES = 2 * 1024 * 1024   # 2 MB safety limit


# ────────────────────────────────────────────────────────────────────────────
#  Model loader — cached so the ~13 MB model is loaded only ONCE per worker
# ────────────────────────────────────────────────────────────────────────────

_model_cache: dict = {"model": None, "info": None, "loaded": False, "error": None}


def _load_model_once() -> dict:
    """Load Keras model and metadata JSON exactly once; cache result."""
    if _model_cache["loaded"]:
        return _model_cache

    if not TF_AVAILABLE:
        _model_cache["error"] = "TensorFlow is not installed (pip install tensorflow)"
        _model_cache["loaded"] = True
        return _model_cache

    if not PIL_AVAILABLE:
        _model_cache["error"] = "Pillow is not installed (pip install pillow)"
        _model_cache["loaded"] = True
        return _model_cache

    if not os.path.exists(_MODEL_PATH):
        _model_cache["error"] = (
            f"Trained model not found at {_MODEL_PATH}. "
            "Run train_emotion_model.py first."
        )
        _model_cache["loaded"] = True
        logger.warning(_model_cache["error"])
        return _model_cache

    try:
        logger.info("Loading emotion CNN from %s …", _MODEL_PATH)
        t0 = time.time()
        model = tf.keras.models.load_model(_MODEL_PATH, compile=False)
        elapsed = time.time() - t0
        logger.info("Emotion model loaded in %.2f s", elapsed)
        _model_cache["model"] = model
    except Exception as exc:
        _model_cache["error"] = f"Failed to load model: {exc}"
        _model_cache["loaded"] = True
        logger.exception("Emotion model load failed")
        return _model_cache

    # Load metadata
    info = {}
    if os.path.exists(_INFO_PATH):
        try:
            with open(_INFO_PATH) as f:
                info = json.load(f)
        except Exception:
            pass
    _model_cache["info"] = info
    _model_cache["loaded"] = True
    return _model_cache


def _preprocess_image(b64_string: str) -> np.ndarray:
    """
    Decode base64 image, resize to IMG_SIZE, normalise to [0, 1].

    Returns ndarray of shape (1, 96, 96, 3).
    Raises ValueError on any decode / format error.
    """
    # Strip optional data-URI prefix: "data:image/jpeg;base64,..."
    if "," in b64_string:
        b64_string = b64_string.split(",", 1)[1]

    if len(b64_string) > MAX_B64_BYTES:
        raise ValueError("Image exceeds 2 MB limit")

    try:
        img_bytes = base64.b64decode(b64_string)
    except Exception as exc:
        raise ValueError(f"Base64 decode failed: {exc}") from exc

    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as exc:
        raise ValueError(f"Cannot open image: {exc}") from exc

    img = img.resize(IMG_SIZE, Image.BILINEAR)
    arr = np.array(img, dtype=np.float32) / 255.0      # normalise [0,1]
    arr = np.expand_dims(arr, axis=0)                   # shape (1,96,96,3)
    return arr


def _canonical_emotion_name(label: str) -> str:
    normalized = str(label or "").strip().lower()
    return CLASS_NAME_ALIASES.get(normalized, normalized)


def _normalise_values(values: np.ndarray) -> np.ndarray:
    vals = np.asarray(values, dtype=np.float32).reshape(-1)
    vals = np.clip(vals, 0.0, None)
    total = float(vals.sum())
    if total <= 0:
        vals = np.full(len(CLASS_NAMES), 1.0 / len(CLASS_NAMES), dtype=np.float32)
    else:
        vals /= total
    return vals


def _align_scores_to_taxonomy(raw_probs: np.ndarray, source_class_names=None) -> dict:
    """Map model output probabilities to canonical taxonomy order."""
    probs = np.asarray(raw_probs, dtype=np.float32).reshape(-1)

    explicit_labels = [
        _canonical_emotion_name(name)
        for name in (source_class_names or [])
        if str(name).strip()
    ]
    if explicit_labels and len(explicit_labels) == probs.shape[0]:
        mapped = {cls: 0.0 for cls in CLASS_NAMES}
        for idx, label in enumerate(explicit_labels):
            if label in mapped:
                mapped[label] += float(probs[idx])
        vals = np.array([mapped[cls] for cls in CLASS_NAMES], dtype=np.float32)
        vals = _normalise_values(vals)

    elif probs.shape[0] == len(CLASS_NAMES):
        vals = _normalise_values(probs)

    elif probs.shape[0] == len(LEGACY_CLASS_NAMES_6):
        legacy = {name: float(probs[i]) for i, name in enumerate(LEGACY_CLASS_NAMES_6)}
        happy = legacy.get("happy", 0.0)
        bored = legacy.get("bored", 0.0)
        focused = legacy.get("focused", 0.0)
        confused = legacy.get("confused", 0.0)
        neutral = legacy.get("neutral", 0.0)
        angry = legacy.get("angry", 0.0)

        # Compatibility mapping for old 6-class models.
        surprised = 0.58 * happy + 0.22 * confused + 0.20 * neutral
        vals = _normalise_values(np.array(
            [happy, bored, focused, confused, neutral, angry, surprised],
            dtype=np.float32,
        ))

    elif probs.shape[0] == len(LEGACY_CLASS_NAMES_4):
        legacy = {name: float(probs[i]) for i, name in enumerate(LEGACY_CLASS_NAMES_4)}
        happy = legacy.get("happy", 0.0)
        confused = legacy.get("confused", 0.0)
        neutral = legacy.get("neutral", 0.0)
        angry = legacy.get("angry", 0.0)

        # Compatibility mapping for old 4-class models.
        bored = 0.60 * neutral + 0.40 * confused
        focused = 0.62 * neutral + 0.38 * happy
        surprised = 0.52 * happy + 0.30 * confused + 0.18 * neutral
        vals = _normalise_values(np.array(
            [happy, bored, focused, confused, neutral, angry, surprised],
            dtype=np.float32,
        ))
    else:
        vals = np.zeros(len(CLASS_NAMES), dtype=np.float32)
        upto = min(len(vals), probs.shape[0])
        vals[:upto] = probs[:upto]
        vals = _normalise_values(vals)

    return {cls: float(round(float(vals[i]), 4)) for i, cls in enumerate(CLASS_NAMES)}


# ────────────────────────────────────────────────────────────────────────────
#  Route: POST /api/ai/emotion/predict
# ────────────────────────────────────────────────────────────────────────────

@ai_emotion_bp.post("/predict")
def predict_emotion():
    """
    Predict emotion from a base64-encoded face image.

    Request JSON:
        { "image": "<base64 string>" }

    Response JSON:
        {
          "emotion"    : "happy",
          "confidence" : 0.9124,
          "all_scores" : {
            "happy": 0.42, "bored": 0.08, "focused": 0.11,
            "confused": 0.09, "neutral": 0.13, "angry": 0.07, "surprised": 0.10
          },
          "model_info" : {"val_accuracy": 0.87, ...},
          "latency_ms" : 42
        }
    """
    cache = _load_model_once()

    if cache["error"]:
        return jsonify({
            "error"       : cache["error"],
            "emotion"     : "neutral",
            "confidence"  : 0.0,
            "all_scores"  : {c: 0.0 for c in CLASS_NAMES},
        }), 503

    data = request.get_json(silent=True) or {}
    b64_image = data.get("image", "")

    if not b64_image:
        return jsonify({"error": "No 'image' field in request body"}), 400

    try:
        t0 = time.time()
        img_array = _preprocess_image(b64_image)

        model = cache["model"]
        probs = model.predict(img_array, verbose=0)[0]
        source_classes = cache.get("info", {}).get("class_names") or []
        all_scores = _align_scores_to_taxonomy(probs, source_classes)
        top_class, top_conf = max(all_scores.items(), key=lambda kv: kv[1])
        latency_ms = int((time.time() - t0) * 1000)

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Inference error")
        return jsonify({"error": f"Inference failed: {exc}"}), 500

    return jsonify({
        "emotion"    : top_class,
        "confidence" : round(top_conf, 4),
        "all_scores" : all_scores,
        "model_info" : {
            "val_accuracy"  : cache["info"].get("val_accuracy"),
            "architecture"  : cache["info"].get("architecture"),
            "training_notes": cache["info"].get("training_notes"),
        },
        "latency_ms" : latency_ms,
    })


# ────────────────────────────────────────────────────────────────────────────
#  Route: GET /api/ai/emotion/status
# ────────────────────────────────────────────────────────────────────────────

@ai_emotion_bp.get("/status")
def model_status():
    """
    Health check for the emotion model.
    Used by the frontend to decide whether to use server-side or
    TF.js browser-side inference.
    """
    cache = _load_model_once()
    model_loaded = cache["model"] is not None

    class_names = cache.get("info", {}).get("class_names") or CLASS_NAMES
    class_names = [_canonical_emotion_name(name) for name in class_names]
    if len(class_names) != len(CLASS_NAMES):
        class_names = CLASS_NAMES

    return jsonify({
        "model_loaded"   : model_loaded,
        "model_path"     : _MODEL_PATH,
        "model_exists"   : os.path.exists(_MODEL_PATH),
        "error"          : cache.get("error"),
        "class_names"    : class_names,
        "info"           : cache.get("info") or {},
        "tensorflow_ver" : tf.__version__ if TF_AVAILABLE else None,
    })
