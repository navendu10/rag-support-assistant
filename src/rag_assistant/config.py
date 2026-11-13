"""Typed application settings loaded from YAML, .env and environment variables.

Precedence (highest first): explicit keyword arguments, environment variables,
the .env file, then the YAML config file. Nested values can be overridden from
the environment with ``__`` as a delimiter, e.g. ``LLM__SMALL_MODEL``.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = PROJECT_ROOT / "configs"
DEFAULT_CONFIG_PATH = CONFIGS_DIR / "base.yaml"
STAGES_DIR = CONFIGS_DIR / "stages"
PROMPTS_PATH = CONFIGS_DIR / "prompts.yaml"
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
    reasoning_effort: Literal["low", "medium", "high"] | None = "low"
    max_completion_tokens: int = Field(default=2048, gt=0)

    @property
    def generation_model(self) -> str:
        """Model that writes answers (the large model)."""
        return self.large_model

    @property
    def judge_model(self) -> str:
        """Model that grades answers (the small model)."""
        return self.small_model


class RateLimitSettings(BaseModel):
    """Client-side limits that keep us under Groq's free tier (per model)."""

    requests_per_minute: int = Field(default=25, gt=0)
    tokens_per_minute: int = Field(default=7000, gt=0)
    expected_output_tokens: int = Field(default=500, ge=0)
    max_retries: int = Field(default=6, ge=0)
    backoff_base_seconds: float = Field(default=2.0, gt=0)
    backoff_max_seconds: float = Field(default=60.0, gt=0)
    max_retry_after_seconds: float = Field(default=120.0, gt=0)


class CacheSettings(BaseModel):
    """LangChain SQLite response cache."""

    enabled: bool = True
    path: Path = Path(".cache/llm_cache.sqlite")

    @property
    def resolved_path(self) -> Path:
        """Absolute cache path (relative paths are under the project root)."""
        return self.path if self.path.is_absolute() else PROJECT_ROOT / self.path


class PathSettings(BaseModel):
    """Where data, golden sets and results live (relative to the project root)."""

    generated_dir: Path = Path("data/generated")
    golden_file: Path = Path("data/golden/questions.jsonl")
    users_file: Path = Path("data/users.json")
    results_dir: Path = Path("results")

    def resolve(self, path: Path) -> Path:
        """Resolve a configured path against the project root."""
        return path if path.is_absolute() else PROJECT_ROOT / path


class EvalSettings(BaseModel):
    """Evaluation harness settings."""

    recall_k: int = Field(default=5, gt=0)


class QuerySettings(BaseModel):
    """Query rewriting (Session 6): how much chat history the rewriter sees, and retries."""

    history_turns: int = Field(default=6, ge=0, description="latest turns given to the rewriter")
    max_attempts: int = Field(default=2, ge=1, description="1 call plus retries on invalid output")


class GroundingSettings(BaseModel):
    """Grounded generation (Session 8): retries on invalid structured output."""

    max_attempts: int = Field(default=2, ge=1, description="1 call plus retries on invalid output")


class HandoffSettings(BaseModel):
    """Human handoff (Session 8): where customer tickets are appended (git-ignored)."""

    tickets_path: Path = Path("data/tickets/tickets.jsonl")


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
    # Weaviate API keys (Session 7), read from .env; see docs/security.md. Optional so
    # unit tests and CI need none. The admin key is for ingestion, setup_access.py and the
    # deliberately insecure v0-v4 stages; customer and agent keys follow the user's role.
    weaviate_admin_key: SecretStr | None = None
    weaviate_customer_key: SecretStr | None = None
    weaviate_agent_key: SecretStr | None = None
    weaviate: WeaviateSettings
    embedding: EmbeddingSettings
    llm: LLMSettings
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    eval: EvalSettings = Field(default_factory=EvalSettings)
    query: QuerySettings = Field(default_factory=QuerySettings)
    grounding: GroundingSettings = Field(default_factory=GroundingSettings)
    handoff: HandoffSettings = Field(default_factory=HandoffSettings)

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


# ------------------------------------------------------------------ Stages


# TEXT properties that keyword (BM25) search may use; they match ingestion/store.py.
KEYWORD_PROPERTIES = ("text", "title", "product_code", "section")
_QUERY_PROPERTY = re.compile(r"^(?P<name>[a-z_]+)(?:\^(?P<boost>\d+(?:\.\d+)?))?$")


class HybridConfig(BaseModel):
    """Settings for ``search: hybrid`` (BM25 keyword search fused with vector search).

    ``alpha`` weights the two: 1.0 is pure vector, 0.0 is pure keyword. With
    ``alpha_strategy: dynamic`` the retriever uses ``code_alpha`` when the question
    contains a product or error code and ``default_alpha`` otherwise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    alpha_strategy: Literal["fixed", "dynamic"] = "fixed"
    alpha: float | None = Field(default=None, ge=0.0, le=1.0, description="fixed only")
    code_alpha: float | None = Field(default=None, ge=0.0, le=1.0, description="dynamic only")
    default_alpha: float | None = Field(default=None, ge=0.0, le=1.0, description="dynamic only")
    fusion: Literal["relative_score", "ranked"] = "relative_score"
    query_properties: tuple[str, ...] = ("text", "title", "product_code^2")

    @field_validator("query_properties")
    @classmethod
    def _known_properties(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Each entry is a keyword-searchable property, optionally boosted (``name^2``)."""
        if not value:
            raise ValueError("query_properties must not be empty")
        names = []
        for entry in value:
            match = _QUERY_PROPERTY.match(entry)
            if match is None or match["name"] not in KEYWORD_PROPERTIES:
                raise ValueError(
                    f"query property {entry!r} must be one of {KEYWORD_PROPERTIES}, "
                    "optionally with a boost such as '^2'"
                )
            names.append(match["name"])
        if len(set(names)) != len(names):
            raise ValueError("query_properties lists a property twice")
        return value

    @model_validator(mode="after")
    def _alphas_match_strategy(self) -> HybridConfig:
        """fixed needs ``alpha`` only; dynamic needs ``code_alpha`` and ``default_alpha`` only."""
        dynamic = (self.code_alpha, self.default_alpha)
        if self.alpha_strategy == "fixed":
            if self.alpha is None or dynamic != (None, None):
                raise ValueError("alpha_strategy fixed needs alpha and no code/default_alpha")
        elif self.alpha is not None or None in dynamic:
            raise ValueError("alpha_strategy dynamic needs code_alpha and default_alpha, no alpha")
        return self


# How reranked candidates are ordered: by the cross-encoder alone, by reciprocal rank
# fusion with the search rank, or by a weighted blend of the two normalised scores.
RerankFusion = Literal["replace", "rrf", "blend"]

# Fields that decide what ingestion stores; a stage reusing an index must match them.
INGESTION_FIELDS = ("chunking", "chunk_size", "chunk_overlap", "min_chunk_chars")
# Reranked stages are scored on recall@5, so at least 5 candidates are needed.
MIN_FETCH_K = 5
# access_control stages store each audience in its own collection (Session 7).
AUDIENCES = ("public", "internal")


class StageConfig(BaseModel):
    """On/off switches for one pipeline stage (``configs/stages/<name>.yaml``).

    v0 is the naive baseline. Each later session implements one or more switches;
    until then, ``ensure_supported`` rejects any non-v0 value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    description: str = ""
    chunking: Literal["fixed", "structure"]
    chunk_size: int = Field(gt=0)
    chunk_overlap: int = Field(ge=0)
    min_chunk_chars: int = Field(default=200, ge=0, description="structure chunking only")
    search: Literal["vector", "hybrid"]
    hybrid: HybridConfig | None = None
    rerank: bool
    rerank_model: str | None = Field(default=None, min_length=1, description="rerank only")
    fetch_k: int | None = Field(default=None, gt=0, le=100, description="rerank only")
    min_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="corrective_flow only: retry trigger"
    )
    rerank_fusion: RerankFusion | None = Field(default=None, description="rerank only")
    rerank_blend_weight: float | None = Field(
        default=None, ge=0.0, le=1.0, description="blend only: weight of the rerank score"
    )
    index: str | None = Field(
        default=None,
        pattern=r"^Chunks_[A-Za-z0-9_]+$",
        description="reuse another stage's collection instead of ingesting a copy",
    )
    query_rewrite: bool
    use_history: bool
    region_filter: bool
    access_control: bool
    grounding: bool
    corrective_flow: bool
    retry_fetch_k: int | None = Field(
        default=None, gt=0, le=100, description="corrective_flow only: the wider search"
    )
    retry_top_k: int | None = Field(
        default=None, gt=0, le=50, description="corrective_flow only: the wider context"
    )
    injection_guard: bool
    top_k: int = Field(gt=0, le=50)

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self) -> StageConfig:
        """Chunk overlap must be smaller than the chunk size."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.min_chunk_chars >= self.chunk_size:
            raise ValueError("min_chunk_chars must be smaller than chunk_size")
        return self

    @model_validator(mode="after")
    def _hybrid_settings_match_search(self) -> StageConfig:
        """``search: hybrid`` needs a ``hybrid`` block; ``search: vector`` must not have one."""
        if (self.search == "hybrid") != (self.hybrid is not None):
            raise ValueError("set a hybrid: block exactly when search is hybrid")
        return self

    @model_validator(mode="after")
    def _rerank_settings_match_switch(self) -> StageConfig:
        """``rerank: true`` needs a model and ``fetch_k``; ``rerank: false`` allows neither."""
        settings = (
            self.rerank_model,
            self.fetch_k,
            self.min_score,
            self.rerank_fusion,
            self.rerank_blend_weight,
        )
        if not self.rerank:
            if any(value is not None for value in settings):
                raise ValueError(
                    "rerank_model, fetch_k, min_score, rerank_fusion and rerank_blend_weight "
                    "need rerank: true"
                )
            return self
        if self.rerank_model is None or self.fetch_k is None or self.rerank_fusion is None:
            raise ValueError("rerank: true needs rerank_model, fetch_k and rerank_fusion")
        if (self.rerank_fusion == "blend") != (self.rerank_blend_weight is not None):
            raise ValueError("set rerank_blend_weight exactly when rerank_fusion is blend")
        if self.fetch_k < max(self.top_k, MIN_FETCH_K):
            raise ValueError(f"fetch_k must be at least max(top_k, {MIN_FETCH_K})")
        return self

    @model_validator(mode="after")
    def _history_needs_rewrite(self) -> StageConfig:
        """History reaches retrieval only through the rewriter, so it needs ``query_rewrite``."""
        if self.use_history and not self.query_rewrite:
            raise ValueError("use_history: true needs query_rewrite: true")
        return self

    @model_validator(mode="after")
    def _corrective_flow_settings(self) -> StageConfig:
        """``corrective_flow`` needs grounding, reranking, ``min_score`` and a wider retry
        (``retry_fetch_k`` > ``fetch_k``, ``retry_top_k`` > ``top_k``); those three settings
        need ``corrective_flow``."""
        retry = (self.min_score, self.retry_fetch_k, self.retry_top_k)
        if not self.corrective_flow:
            if any(value is not None for value in retry):
                raise ValueError(
                    "min_score, retry_fetch_k and retry_top_k need corrective_flow: true"
                )
            return self
        if not (self.grounding and self.rerank):
            raise ValueError("corrective_flow: true needs grounding: true and rerank: true")
        if self.min_score is None or self.retry_fetch_k is None or self.retry_top_k is None:
            raise ValueError("corrective_flow: true needs min_score, retry_fetch_k and retry_top_k")
        if self.retry_fetch_k <= int(self.fetch_k or 0):
            raise ValueError("retry_fetch_k must be larger than fetch_k (a wider search)")
        if not self.top_k < self.retry_top_k <= self.retry_fetch_k:
            raise ValueError("retry_top_k must be larger than top_k and at most retry_fetch_k")
        return self

    @property
    def collection_name(self) -> str:
        """Weaviate collection for this stage: ``index`` if set, else e.g. ``Chunks_v0``.

        Stages with ``access_control`` have two collections instead, named after this
        one; see ``audience_collections``.
        """
        return self.index or f"Chunks_{self.name}"

    @property
    def audience_collections(self) -> dict[str, str]:
        """``access_control`` only: audience -> collection, e.g. public -> Chunks_v5_public.

        A stage reusing an index (v6a and v6 set ``index: Chunks_v5``) reads its owner's
        collections.
        """
        if not self.access_control:
            raise ValueError(f"stage {self.name} has access_control: false")
        return {audience: f"{self.collection_name}_{audience}" for audience in AUDIENCES}

    @property
    def index_owner(self) -> str:
        """The stage whose ingestion builds this stage's collection (itself, unless reused)."""
        return self.collection_name.removeprefix("Chunks_")

    @property
    def reuses_index(self) -> bool:
        """True when this stage searches another stage's collection."""
        return self.index_owner != self.name

    def ensure_supported(self) -> None:
        """Raise ``NotImplementedError`` for any switch a later session will add."""
        problems = [
            f"{switch}={getattr(self, switch)!r} arrives in Session {session} ({feature})"
            for switch, (supported, session, feature) in UNIMPLEMENTED_SWITCHES.items()
            if getattr(self, switch) != supported
        ]
        if problems:
            raise NotImplementedError(
                f"Stage '{self.name}' uses features that are not implemented yet: "
                + "; ".join(problems)
            )


# switch -> (only supported value so far, session that adds the rest, feature)
# (chunking since Session 3, search since Session 4, rerank since Session 5,
# query_rewrite, use_history and region_filter since Session 6, access_control since
# Session 7, and grounding, corrective_flow and min_score since Session 8.)
UNIMPLEMENTED_SWITCHES: dict[str, tuple[object, int, str]] = {
    "injection_guard": (False, 9, "prompt-injection defences"),
}


def load_stage(name: str, stages_dir: Path = STAGES_DIR) -> StageConfig:
    """Load, validate and check support for ``configs/stages/<name>.yaml``."""
    path = stages_dir / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Stage config not found: {path}")
    stage = StageConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if stage.name != name:
        raise ValueError(f"{path.name} declares name '{stage.name}', expected '{name}'")
    stage.ensure_supported()
    if stage.reuses_index:
        check_index_owner(stage, stages_dir)
    return stage


def check_index_owner(stage: StageConfig, stages_dir: Path = STAGES_DIR) -> None:
    """A reused index must come from an existing stage with identical ingestion settings."""
    owner_path = stages_dir / f"{stage.index_owner}.yaml"
    if not owner_path.is_file():
        raise ValueError(f"{stage.name}: index {stage.index} has no stage {owner_path.name}")
    owner = StageConfig.model_validate(yaml.safe_load(owner_path.read_text(encoding="utf-8")))
    if owner.reuses_index:
        raise ValueError(f"{stage.name}: {owner.name} itself reuses an index; point at the owner")
    if owner.access_control != stage.access_control:
        raise ValueError(
            f"{stage.name} reuses {stage.index}, but only one of them has access_control"
        )
    differ = [f for f in INGESTION_FIELDS if getattr(owner, f) != getattr(stage, f)]
    if differ:
        raise ValueError(
            f"{stage.name} reuses {stage.index} but its {', '.join(differ)} differ from "
            f"{owner.name}; give it its own index"
        )


def load_prompts(path: Path = PROMPTS_PATH) -> dict[str, str]:
    """Load the prompt templates from ``configs/prompts.yaml``."""
    prompts = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(prompts, dict) or not all(isinstance(v, str) for v in prompts.values()):
        raise ValueError(f"{path} must map prompt names to strings")
    return prompts
