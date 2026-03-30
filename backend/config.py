import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _normalize_database_url(raw_value: str | None) -> str:
    value = (raw_value or "").strip()
    if not value:
        return "sqlite:///elevate_dev.db"

    if value.startswith("postgres://"):
        value = value.replace("postgres://", "postgresql://", 1)

    if value.startswith("postgresql://"):
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        is_local = host in {"localhost", "127.0.0.1"}

        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if not is_local and "sslmode" not in query:
            query["sslmode"] = "require"

        value = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )

    return value


def _parse_cors_origins(raw_value: str | None):
    if not raw_value:
        return ["http://localhost:8000", "http://127.0.0.1:8000"]
    items = [item.strip() for item in str(raw_value).split(",")]
    return [item for item in items if item]


def _engine_options_for(uri: str):
    options = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }

    if uri.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}

    return options


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.environ.get("DATABASE_URL", "sqlite:///elevate_dev.db"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options_for(SQLALCHEMY_DATABASE_URI)
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET", "dev-jwt-secret")
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-admin-token")
    CORS_ORIGINS = _parse_cors_origins(os.environ.get("CORS_ORIGINS"))


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False
    PREFERRED_URL_SCHEME = "https"


class TestingConfig(BaseConfig):
    DEBUG = False
    TESTING = True
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:"))
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options_for(SQLALCHEMY_DATABASE_URI)


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None):
    if not name:
        name = os.environ.get("FLASK_ENV", "development")
    return CONFIG_MAP.get(name, DevelopmentConfig)


