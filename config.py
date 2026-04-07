import json
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource


class _CommaSepMixin:
    """Mixin that adds comma-separated list support to settings sources.

    Handles all formats for list[int] fields:
      ADMIN_IDS=123456789           → [123456789]
      ADMIN_IDS=123456789,456789    → [123456789, 456789]
      ADMIN_IDS=[123456789,456789]  → [123456789, 456789]  (JSON also works)
      ALLOWED_USERS=                → []
    """

    def decode_complex_value(
        self, field_name: str, field: FieldInfo, value: Any
    ) -> Any:
        if not isinstance(value, str):
            return super().decode_complex_value(field_name, field, value)  # type: ignore[misc]
        stripped = value.strip()
        if not stripped:
            return []
        if stripped[0] in ("[", "{", '"'):
            return json.loads(stripped)
        # Comma-separated values: "123,456" → wrap as JSON array
        try:
            return json.loads(f"[{stripped}]")
        except json.JSONDecodeError:
            # Fallback: return as list of strings (pydantic will coerce types)
            return [x.strip() for x in stripped.split(",") if x.strip()]


class _CommaSepEnvSource(_CommaSepMixin, EnvSettingsSource):
    """EnvSettingsSource with comma-separated list support (for systemd EnvironmentFile)."""


class _CommaSepDotEnvSource(_CommaSepMixin, DotEnvSettingsSource):
    """DotEnvSettingsSource with comma-separated list support (for .env file)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    BOT_TOKEN: str
    ADMIN_IDS: list[int] = []
    ALLOWED_USERS: list[int] = []
    CHECK_INTERVAL_HOURS: int = 2
    MAX_PRODUCTS_PER_USER: int = 50
    LOG_LEVEL: str = "INFO"
    DATABASE_PATH: str = "data/tracker.db"
    WB_REQUEST_TIMEOUT: int = 10
    WB_MAX_RETRIES: int = 3

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: EnvSettingsSource,
        dotenv_settings: DotEnvSettingsSource,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        cfg = settings_cls.model_config
        return (
            init_settings,
            _CommaSepEnvSource(
                settings_cls,
                case_sensitive=cfg.get("case_sensitive"),
            ),
            _CommaSepDotEnvSource(
                settings_cls,
                env_file=cfg.get("env_file"),
                env_file_encoding=cfg.get("env_file_encoding"),
                case_sensitive=cfg.get("case_sensitive"),
            ),
            file_secret_settings,
        )


settings = Settings()
