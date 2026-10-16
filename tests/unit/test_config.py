"""Unit tests for rag_assistant.config. No network, Docker or real .env needed."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_assistant.config import DEFAULT_CONFIG_PATH, load_settings

FAKE_KEY = "gsk_test_not_a_real_key"


@pytest.fixture
def fake_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provide a fake Groq key through the environment."""
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    return FAKE_KEY


def test_base_yaml_loads(fake_key: str) -> None:
    """The committed base.yaml produces valid, sensible settings."""
    settings = load_settings(DEFAULT_CONFIG_PATH, env_file=None)

    assert settings.weaviate.host == "localhost"
    assert settings.weaviate.http_port == 8080
    assert settings.weaviate.grpc_port == 50051
    assert settings.embedding.model_name == "BAAI/bge-small-en-v1.5"
    assert settings.embedding.dimension == 384
    assert settings.llm.small_model
    assert settings.llm.large_model
    assert settings.llm.small_model != settings.llm.large_model


def test_env_var_overrides_yaml(fake_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested environment variables take priority over YAML values."""
    monkeypatch.setenv("LLM__SMALL_MODEL", "some-other-model")

    settings = load_settings(DEFAULT_CONFIG_PATH, env_file=None)

    assert settings.llm.small_model == "some-other-model"


def test_api_key_is_hidden(fake_key: str) -> None:
    """The API key never appears in repr or str output."""
    settings = load_settings(DEFAULT_CONFIG_PATH, env_file=None)

    assert settings.groq_api_key.get_secret_value() == FAKE_KEY
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)


def test_missing_api_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings refuse to load without a Groq key."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(ValidationError, match="groq_api_key"):
        load_settings(DEFAULT_CONFIG_PATH, env_file=None)


def test_blank_api_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whitespace-only key is treated as missing."""
    monkeypatch.setenv("GROQ_API_KEY", "   ")

    with pytest.raises(ValidationError, match="GROQ_API_KEY is empty"):
        load_settings(DEFAULT_CONFIG_PATH, env_file=None)


def test_custom_yaml_and_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A custom YAML and .env file can be supplied explicitly."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config = tmp_path / "custom.yaml"
    config.write_text(
        "weaviate:\n  url: https://weaviate.example.com\n  grpc_port: 443\n"
        "embedding:\n  model_name: test-model\n  dimension: 8\n"
        "llm:\n  small_model: small\n  large_model: large\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(f"GROQ_API_KEY={FAKE_KEY}\n", encoding="utf-8")

    settings = load_settings(config, env_file=env_file)

    assert settings.weaviate.secure is True
    assert settings.weaviate.http_port == 443
    assert settings.embedding.dimension == 8
    assert settings.groq_api_key.get_secret_value() == FAKE_KEY


def test_missing_config_file_raises(fake_key: str, tmp_path: Path) -> None:
    """A clear error is raised when the YAML file does not exist."""
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.yaml", env_file=None)
