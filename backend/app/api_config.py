"""Small explicit public-surface settings; no commercial configuration lives here."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def boolean_setting(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false.")
    return value == "true"


@dataclass(frozen=True)
class ApiSettings:
    environment: str
    legacy_endpoints: bool
    docs: bool
    cors_origins: tuple[str, ...]


def load_api_settings() -> ApiSettings:
    environment = os.getenv("APP_ENV", "development")
    if environment not in {"development", "production"}:
        raise ValueError("APP_ENV must be development or production.")
    development = environment == "development"
    default_origins = "http://localhost:5173,http://127.0.0.1:5173" if development else ""
    origins = []
    for value in os.getenv("CORS_ALLOWED_ORIGINS", default_origins).split(","):
        value = value.strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and parsed.username is None and parsed.password is None
                     and not parsed.query and not parsed.fragment
                     and parsed.path in {"", "/"} and "*" not in value
                     and not any(character.isspace() for character in value))
            parsed.port  # Validate port syntax without logging the configured value.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("CORS_ALLOWED_ORIGINS must contain explicit HTTP(S) origins.")
        origins.append(value.rstrip("/"))
    return ApiSettings(
        environment=environment,
        legacy_endpoints=boolean_setting("ENABLE_LEGACY_ENDPOINTS", development),
        docs=boolean_setting("ENABLE_API_DOCS", development),
        cors_origins=tuple(origins),
    )
