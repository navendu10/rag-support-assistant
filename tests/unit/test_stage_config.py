"""Tests for StageConfig validation and the not-yet-implemented switches."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from rag_assistant.config import (
    STAGES_DIR,
    UNIMPLEMENTED_SWITCHES,
    StageConfig,
    load_prompts,
    load_stage,
)

V0_PATH = STAGES_DIR / "v0.yaml"


def _v0_dict() -> dict:
    """The committed v0 stage as a plain dict."""
    return yaml.safe_load(V0_PATH.read_text(encoding="utf-8"))


def _write_stage(tmp_path: Path, **overrides: object) -> str:
    """Write a modified copy of v0 into ``tmp_path`` and return its name."""
    data = {**_v0_dict(), **overrides}
    (tmp_path / f"{data['name']}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(data["name"])


def test_v0_loads_with_expected_switches() -> None:
    """v0 is the naive baseline: fixed chunks, vector search, every guard off, top_k 4."""
    stage = load_stage("v0")
    assert (stage.chunking, stage.search, stage.hybrid) == ("fixed", "vector", None)
    assert (stage.chunk_size, stage.chunk_overlap, stage.top_k) == (1000, 100, 4)
    for switch in ("rerank", "query_rewrite", "use_history", "region_filter"):
        assert getattr(stage, switch) is False
    for switch in ("access_control", "grounding", "corrective_flow", "injection_guard"):
        assert getattr(stage, switch) is False
    assert stage.collection_name == "Chunks_v0"


def test_every_switch_is_implemented() -> None:
    """Session 9 implemented the last switch (injection_guard): nothing raises any more."""
    assert UNIMPLEMENTED_SWITCHES == {}
    for name in ("v0", "v5", "v6", "v7", "v7raw"):
        load_stage(name).ensure_supported()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"injection_guard": False}, "set an injection: block exactly when injection_guard"),
        ({"injection": None}, "set an injection: block exactly when injection_guard"),
        ({"corrective_flow": False, "min_score": None, "retry_fetch_k": None,
          "retry_top_k": None}, "injection_guard: true needs corrective_flow"),
    ],
)  # fmt: skip
def test_injection_settings_are_validated(overrides: dict, message: str) -> None:
    """The layers need injection_guard and the corrective flow (the guard routes through it)."""
    with pytest.raises(ValidationError, match=message):
        StageConfig.model_validate({**load_stage("v7").model_dump(), **overrides})


def test_v7_is_v6_plus_the_layers_and_v7raw_only_skips_sanitising() -> None:
    """v7 = v6 + injection_guard (own collections); v7raw differs only in layer 1 and is
    red-team only."""
    v6, v7, raw = load_stage("v6"), load_stage("v7"), load_stage("v7raw")
    meta = {"name", "description", "index", "injection_guard", "injection", "redteam_only"}

    def body(stage: StageConfig) -> dict:
        return {k: v for k, v in stage.model_dump().items() if k not in meta}

    assert body(v7) == body(v6) == body(raw)
    assert v7.injection is not None and raw.injection is not None
    assert (v7.injection.sanitize_ingestion, v7.injection.tagged_sources) == (True, True)
    assert v7.injection.output_guard and v7.sanitizes and not raw.sanitizes
    assert raw.redteam_only and not v7.redteam_only
    assert v7.audience_collections == {
        "public": "Chunks_v7_public",
        "internal": "Chunks_v7_internal",
    }
    assert raw.audience_collections["internal"] == "Chunks_v7raw_internal"


V6 = load_stage("v6")


def test_v6a_and_v6_are_v5_plus_one_switch_each() -> None:
    """v6a adds grounding to v5; v6 adds the corrective flow (min_score, retry_fetch_k)."""
    v5, v6a = load_stage("v5"), load_stage("v6a")
    meta = {"name", "description", "index"}

    def body(stage: StageConfig, *extra: str) -> dict:
        return {k: v for k, v in stage.model_dump().items() if k not in meta | set(extra)}

    assert body(v6a, "grounding") == body(v5, "grounding") and v6a.grounding
    flow = ("corrective_flow", "min_score", "retry_fetch_k", "retry_top_k")
    assert body(V6, *flow) == body(v6a, *flow)
    assert (V6.corrective_flow, V6.min_score, V6.retry_fetch_k, V6.retry_top_k) == (
        True,
        0.1,
        30,
        8,
    )
    assert V6.index == v6a.index == "Chunks_v5"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"grounding": False}, "needs grounding: true and rerank: true"),
        ({"min_score": None}, "needs min_score, retry_fetch_k and retry_top_k"),
        ({"retry_top_k": None}, "needs min_score, retry_fetch_k and retry_top_k"),
        ({"retry_fetch_k": 20}, "must be larger than fetch_k"),
        ({"retry_top_k": 4}, "retry_top_k must be larger than top_k"),
        ({"corrective_flow": False}, "need corrective_flow: true"),
    ],
)
def test_corrective_flow_settings_are_validated(overrides: dict, message: str) -> None:
    """The flow needs grounding, reranking, a threshold and a broader retry; the threshold
    and retry size mean nothing without the flow."""
    with pytest.raises(ValidationError, match=message):
        StageConfig.model_validate({**V6.model_dump(), **overrides})


def test_v1_differs_from_v0_only_in_chunking() -> None:
    """v1 isolates the chunking fix: every other switch matches v0."""
    v0, v1 = load_stage("v0"), load_stage("v1")
    chunking_fields = {"name", "description", "chunking", "chunk_size", "chunk_overlap"}
    chunking_fields.add("min_chunk_chars")
    same = {k: v for k, v in v0.model_dump().items() if k not in chunking_fields}
    assert same == {k: v for k, v in v1.model_dump().items() if k not in chunking_fields}
    assert (v1.chunking, v1.chunk_size, v1.chunk_overlap, v1.min_chunk_chars) == (
        "structure",
        1200,
        150,
        200,
    )
    assert v1.collection_name == "Chunks_v1"


def test_structure_chunking_is_implemented() -> None:
    """chunking=structure no longer raises (it was the Session 3 switch)."""
    assert "chunking" not in UNIMPLEMENTED_SWITCHES
    load_stage("v1").ensure_supported()


def test_min_chunk_chars_must_be_below_chunk_size() -> None:
    """The merge threshold must be smaller than the chunk size."""
    with pytest.raises(ValidationError):
        StageConfig.model_validate({**_v0_dict(), "min_chunk_chars": 1000})


def test_numeric_settings_may_change(tmp_path: Path) -> None:
    """top_k and chunk sizes are free parameters, not unimplemented features."""
    name = _write_stage(tmp_path, name="tuned", top_k=8, chunk_size=800, chunk_overlap=50)
    assert load_stage(name, stages_dir=tmp_path).top_k == 8


def test_unknown_key_rejected() -> None:
    """Typos in a stage file fail loudly."""
    with pytest.raises(ValidationError):
        StageConfig.model_validate({**_v0_dict(), "rerankk": True})


def test_invalid_values_rejected() -> None:
    """Bad enum values and overlaps larger than the chunk are rejected."""
    with pytest.raises(ValidationError):
        StageConfig.model_validate({**_v0_dict(), "search": "keyword"})
    with pytest.raises(ValidationError):
        StageConfig.model_validate({**_v0_dict(), "chunk_overlap": 1000})


def test_name_must_match_file(tmp_path: Path) -> None:
    """A stage file must declare its own name."""
    (tmp_path / "v9.yaml").write_text(yaml.safe_dump(_v0_dict()), encoding="utf-8")
    with pytest.raises(ValueError, match="expected 'v9'"):
        load_stage("v9", stages_dir=tmp_path)


def test_prompts_have_placeholders() -> None:
    """The prompt templates exist and contain the fields the code fills in."""
    prompts = load_prompts()
    assert "{context}" in prompts["naive_answer"] and "{question}" in prompts["naive_answer"]
    assert prompts["naive_answer"].startswith("Answer the customer's question using the context")
    for field in ("{history}", "{question}", "{reference}", "{answer}"):
        assert field in prompts["judge_correctness"]
    assert "HANDOFF" in prompts["judge_handoff"] and "ANSWERED" in prompts["judge_handoff"]
    for field in ("{internal}", "{question}", "{answer}"):
        assert field in prompts["judge_leak"]
    assert "LEAK" in prompts["judge_leak"] and "CLEAN" in prompts["judge_leak"]
