"""Typed application settings loaded from YAML, .env and environment variables.

Precedence (highest first): explicit keyword arguments, environment variables,
the .env file, then the YAML config file. Nested values can be overridden from
the environment with ``__`` as a delimiter, e.g. ``LLM__SMALL_MODEL``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "base.yaml"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
CONFIG_PATH_ENV_VAR = "RAG_CONFIG_FILE"


class WeaviateSettings(BaseModel):
    """Connection details for the Weaviate vector database."""

    url: HttpUrl = HttpUrl("http://localhost:8080")
    grpc_port: int = Field(default=50051, gt=0, lt=65536)

    @property
    def host(self) -> str:
        """Hostname part of the HTTP URL."""
        return self.url.host or "localhost"

    @property
    def http_port(self) -> int:
        """Port part of the HTTP URL (defaults to the scheme's port)."""
        return self.url.port or (443 if self.url.scheme == "https" else 80)

    @property
    def secure(self) -> bool:
        """Whether the connection uses TLS."""
        return self.url.scheme == "https"


class EmbeddingSettings(BaseModel):
    """Which FastEmbed model to use and the vector size it produces."""

    model_name: str = Field(min_length=1)
    dimension: int = Field(gt=0)


class LLMSettings(BaseModel):
    """Groq model names and generation defaults."""

    small_model: str = Field(min_length=1)
    large_model: str = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30, gt=0)


class Settings(BaseSettings):
    """Top-level settings object for the whole application."""

    model_config = SettingsConfigDict(
        yaml_file=DEFAULT_CONFIG_PATH,
        env_file=DEFAULT_ENV_PATH,
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    groq_api_key: SecretStr
    weaviate: WeaviateSettings
    embedding: EmbeddingSettings
    llm: LLMSettings

    @field_validator("groq_api_key")
    @classmethod
    def _key_not_blank(cls, value: SecretStr) -> SecretStr:
        """Reject an empty or whitespace-only API key."""
        if not value.get_secret_value().strip():
            raise ValueError("GROQ_API_KEY is empty")
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Add the YAML file as the lowest-priority source."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def load_settings(
    config_path: str | Path | None = None,
    env_file: str | Path | None = DEFAULT_ENV_PATH,
) -> Settings:
    """Build settings from a specific YAML file and optional .env file.

    Args:
        config_path: YAML file to read. Defaults to ``$RAG_CONFIG_FILE`` or
            ``configs/base.yaml``.
        env_file: .env file to read, or ``None`` to read only real environment
            variables (useful in tests).

    Returns:
        A validated ``Settings`` instance.
    """
    path = Path(config_path or os.environ.get(CONFIG_PATH_ENV_VAR) or DEFAULT_CONFIG_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    class _Settings(Settings):
        model_config = SettingsConfigDict(yaml_file=path, env_file=env_file)

    return _Settings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use."""
    return load_settings()
