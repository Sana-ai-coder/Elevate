"""One-command startup bootstrap for Elevate.

This script is intentionally idempotent:
- Seeds users only when missing.
- Ensures question bank size is healthy.
- Supports optional strict rebuild mode via env var.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXE = ROOT / ".venv" / "Scripts" / "python.exe"
_APP_CACHE = None


def _run(command: list[str], description: str) -> None:
    print(f"[BOOTSTRAP] {description}")
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        raise RuntimeError(f"Failed step: {description}")


def _bootstrap_pip() -> None:
    subprocess.run([str(PYTHON_EXE), "-m", "ensurepip", "--upgrade"], cwd=ROOT)
    probe = subprocess.run([str(PYTHON_EXE), "-m", "pip", "--version"], cwd=ROOT)
    if probe.returncode != 0:
        raise RuntimeError("pip bootstrap failed in virtual environment")


def _read_counts() -> tuple[int, int]:
    global _APP_CACHE

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from backend.app import create_app
    from backend.models import Question, User

    if _APP_CACHE is None:
        _APP_CACHE = create_app("development")

    app = _APP_CACHE
    with app.app_context():
        return User.query.count(), Question.query.count()


def _ensure_users(user_count: int) -> None:
    if user_count > 0:
        print(f"[BOOTSTRAP] Users present: {user_count}. Skipping user seed.")
        return

    _run([str(PYTHON_EXE), "seed_users.py"], "Seeding default users")


def _ensure_dependencies() -> None:
    """Install required packages if backend imports fail due missing modules."""
    try:
        global _APP_CACHE
        _APP_CACHE = None
        _read_counts()
        return
    except Exception as exc:
        message = str(exc)
        recoverable = (
            isinstance(exc, ModuleNotFoundError)
            or isinstance(exc, ImportError)
            or "No module named" in message
            or "cannot import name 'Flask' from 'flask'" in message
        )

        if not recoverable:
            raise

        print(f"[BOOTSTRAP] Dependency issue detected: {message}")
        _bootstrap_pip()

        _run(
            [
                str(PYTHON_EXE),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
                "setuptools",
                "wheel",
            ],
            "Upgrading pip tooling for dependency recovery",
        )

        runtime_packages = [
            "flask",
            "flask_sqlalchemy",
            "flask_cors",
            "python-dotenv",
            "PyJWT",
            "werkzeug",
            "bleach",
            "alembic",
            "psycopg2-binary",
            "numpy",
            "scikit-learn",
            "scipy",
            "shap",
            "joblib",
        ]
        _run(
            [
                str(PYTHON_EXE),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--force-reinstall",
                *runtime_packages,
            ],
            "Installing runtime backend dependencies",
        )

        # Verify that imports now resolve correctly.
        _read_counts()


def _ensure_questions(question_count: int) -> None:
    strict_rebuild = os.environ.get("ELEVATE_STRICT_REBUILD", "0") == "1"
    per_subtopic = int(os.environ.get("ELEVATE_PER_SUBTOPIC", "80"))
    min_questions = int(os.environ.get("ELEVATE_MIN_QUESTIONS", "300"))

    if strict_rebuild:
        _run(
            [
                str(PYTHON_EXE),
                "backend/seed_questions.py",
                "--reset-questions",
                "--strict-stem-rebuild",
                "--per-subtopic",
                str(max(20, min(per_subtopic, 200))),
            ],
            "Strict question bank rebuild",
        )
        return

    if question_count >= min_questions:
        print(
            f"[BOOTSTRAP] Question bank healthy: {question_count} questions (target >= {min_questions})."
        )
        return

    # Non-destructive top-up path for normal startup.
    # 63 is current count of (subject, grade, topic) groups in syllabus manifest.
    missing = max(0, min_questions - question_count)
    groups = 63
    per_topic = max(8, min(50, (missing + groups - 1) // groups))

    _run(
        [
            str(PYTHON_EXE),
            "backend/seed_questions.py",
            "--augment-large",
            "--per-topic",
            str(per_topic),
        ],
        f"Top-up question bank with synthetic coverage (per-topic={per_topic})",
    )


def _ensure_interaction_dataset() -> None:
    build_enabled = os.environ.get("ELEVATE_BUILD_INTERACTION_DATASET", "0") == "1"
    if not build_enabled:
        print("[BOOTSTRAP] Interaction dataset build disabled by env flag.")
        return

    max_age_hours = max(1, int(os.environ.get("ELEVATE_DATASET_MAX_AGE_HOURS", "24")))
    min_events = max(1000, int(os.environ.get("ELEVATE_DATASET_MIN_EVENTS", "20000")))
    min_users = max(10, int(os.environ.get("ELEVATE_DATASET_MIN_USERS", "60")))
    dataset_seed = int(os.environ.get("ELEVATE_DATASET_SEED", "42"))

    _run(
        [
            str(PYTHON_EXE),
            "scripts/build_interaction_dataset.py",
            "--if-stale",
            "--max-age-hours",
            str(max_age_hours),
            "--min-events",
            str(min_events),
            "--min-users",
            str(min_users),
            "--seed",
            str(dataset_seed),
        ],
        "Ensuring interaction dataset snapshot for model training",
    )


def _ensure_bkt_model() -> None:
    """Train BKT model if it doesn't exist or dataset is fresh."""
    build_enabled = os.environ.get("ELEVATE_BUILD_BKT_MODEL", "0") == "1"
    if not build_enabled:
        print("[BOOTSTRAP] BKT model training disabled by env flag.")
        return

    model_dir = ROOT / "backend" / "models" / "bkt"
    model_dir.mkdir(parents=True, exist_ok=True)

    latest_model = model_dir / "bkt_model_latest.pkl"
    if latest_model.exists():
        print(f"[BOOTSTRAP] BKT model exists: {latest_model}. Skipping retraining.")
        return

    _run(
        [str(PYTHON_EXE), "scripts/fit_bkt_model.py"],
        "Training BKT EM parameters on interaction dataset",
    )


def _ensure_dkt_model() -> None:
    """Train Deep Knowledge Tracing model if it doesn't exist."""
    build_enabled = os.environ.get("ELEVATE_BUILD_DKT_MODEL", "0") == "1"
    if not build_enabled:
        print("[BOOTSTRAP] DKT model training disabled by env flag.")
        return

    model_dir = ROOT / "backend" / "models" / "dkt"
    model_dir.mkdir(parents=True, exist_ok=True)

    latest_model = model_dir / "dkt_model_latest.pt"
    if latest_model.exists():
        print(f"[BOOTSTRAP] DKT model exists: {latest_model}. Skipping retraining.")
        return

    _run(
        [str(PYTHON_EXE), "scripts/train_dkt_model.py"],
        "Training Deep Knowledge Tracing model on interaction dataset",
    )


def _ensure_emotion_model() -> None:
    """Build TFJS emotion model artifact when missing."""
    build_enabled = os.environ.get("ELEVATE_BUILD_EMOTION_MODEL", "0") == "1"
    if not build_enabled:
        print("[BOOTSTRAP] Emotion model training disabled by env flag.")
        return

    tfjs_model = ROOT / "frontend" / "js" / "emotion_tfjs" / "model.json"
    metrics_info = ROOT / "backend" / "ai_models" / "emotion_model_info.json"
    if tfjs_model.exists() and metrics_info.exists():
        print(f"[BOOTSTRAP] Emotion TFJS model exists: {tfjs_model}. Skipping retraining.")
        return

    _run(
        [str(PYTHON_EXE), "scripts/train_emotion_fast.py"],
        "Training fast FER-style emotion model and exporting TFJS artifact",
    )


def _ensure_at_risk_model() -> None:
    """Train at-risk predictor artifact if missing."""
    build_enabled = os.environ.get("ELEVATE_BUILD_AT_RISK_MODEL", "0") == "1"
    if not build_enabled:
        print("[BOOTSTRAP] At-risk model training disabled by env flag.")
        return

    model_dir = ROOT / "backend" / "models" / "at_risk_predictor"
    latest_manifest = model_dir / "latest_manifest.json"
    model_dir.mkdir(parents=True, exist_ok=True)

    if latest_manifest.exists():
        print(f"[BOOTSTRAP] At-risk model exists: {latest_manifest}. Skipping retraining.")
        return

    _run(
        [str(PYTHON_EXE), "scripts/train_at_risk_predictor.py"],
        "Training at-risk predictor (Task 7)",
    )


def main() -> int:
    if not PYTHON_EXE.exists():
        print("[BOOTSTRAP] Virtual environment python not found.")
        return 1

    try:
        _ensure_dependencies()
        users_before, questions_before = _read_counts()
        print(
            f"[BOOTSTRAP] Current data status -> users: {users_before}, questions: {questions_before}"
        )

        _ensure_users(users_before)
        _ensure_questions(questions_before)
        _ensure_interaction_dataset()
        _ensure_bkt_model()
        _ensure_dkt_model()
        _ensure_emotion_model()
        _ensure_at_risk_model()

        users_after, questions_after = _read_counts()
        print(
            f"[BOOTSTRAP] Final data status   -> users: {users_after}, questions: {questions_after}"
        )
        return 0
    except Exception as exc:
        print(f"[BOOTSTRAP] ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
