"""
backend/app.py  — Application factory
======================================
Changes from original:
  • Registers the new  /api/ai/emotion  blueprint (ai_emotion.py)
  • All other code is identical to the original
"""

from flask import Flask, send_from_directory, Response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
load_dotenv(os.path.join(PROJECT_ROOT, '.env'), override=False)

from .config import get_config
from .models import (
    db, User, School, Question, UserProgress, EmotionLog,
    SubjectPerformance, AnswerLog, Test, TestQuestion, TestResult,
    TeacherRequest, SyllabusTopic, UserSetting,
)
from .routes.auth      import auth_bp
from .routes.questions import questions_bp
from .routes.progress  import progress_bp
from .routes.reports   import reports_bp
from .routes.emotions  import emotions_bp
from .routes.admin     import admin_bp
from .routes.teacher   import teacher_bp
from .routes.student   import student_bp
from .routes.settings  import settings_bp

# ── NEW: AI emotion inference blueprint ─────────────────────────────────────
from .routes.ai_emotion import ai_emotion_bp

from .logging_config import configure_logging


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)

    @app.route("/api/health", methods=["GET"])
    def health():
        return {"status": "ok"}

    @app.route("/health", methods=["GET"])
    def health_simple():
        return {"status": "ok"}

    cfg = get_config(config_name)
    app.config.from_object(cfg)

    configure_logging(app)

    allowed_origins = app.config.get("CORS_ORIGINS") or ["*"]
    if isinstance(allowed_origins, str):
        allowed_origins = [allowed_origins]

    CORS(
        app,
        resources={r"/api/*": {"origins": allowed_origins}},
        allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    db.init_app(app)

    # with app.app_context():
    #     db.create_all()
    #     app.logger.info("Database tables created/verified")

    app.url_map.strict_slashes = False

    # ── Existing blueprints ──────────────────────────────────────────────────
    app.register_blueprint(auth_bp,      url_prefix="/api/auth")
    app.register_blueprint(questions_bp, url_prefix="/api/questions")
    app.register_blueprint(progress_bp,  url_prefix="/api/progress")
    app.register_blueprint(emotions_bp,  url_prefix="/api/emotions")
    app.register_blueprint(reports_bp,   url_prefix="/api/reports")
    app.register_blueprint(admin_bp,     url_prefix="/api/admin")
    app.register_blueprint(teacher_bp,   url_prefix="/api/teacher")
    app.register_blueprint(student_bp,   url_prefix="/api/student")
    app.register_blueprint(settings_bp,  url_prefix="/api/settings")

    # ── NEW: AI blueprint ────────────────────────────────────────────────────
    app.register_blueprint(ai_emotion_bp, url_prefix="/api/ai/emotion")

    # ── Frontend static serving ──────────────────────────────────────────────
    FRONTEND_DIR = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'frontend')
    )

    @app.get('/')
    def serve_index():
        return send_from_directory(FRONTEND_DIR, 'index.html')

    @app.get('/admin')
    def serve_admin():
        return send_from_directory(FRONTEND_DIR, 'admin.html')

    @app.get('/favicon.ico')
    def serve_favicon():
        return Response(status=204)

    @app.get('/<path:filename>')
    def serve_frontend_files(filename):
        if filename.startswith('api/'):
            return not_found(None)
        full = os.path.join(FRONTEND_DIR, filename)
        if os.path.exists(full):
            return send_from_directory(FRONTEND_DIR, filename)
        return not_found(None)

    @app.errorhandler(404)
    def not_found(error):
        return {"error": "Resource not found"}, 404

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.error(f"Internal server error: {str(error)}")
        return {"error": "Internal server error"}, 500

    return app

import logging
# Mute Werkzeug health check spam
log = logging.getLogger('werkzeug')
class HealthCheckFilter(logging.Filter):
    def filter(self, record):
        return 'GET /health' not in record.getMessage()
log.addFilter(HealthCheckFilter())

if __name__ == "__main__":
    env_name = os.environ.get("FLASK_ENV", "development")
    app = create_app(env_name)
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=(env_name == "development"),
    )
