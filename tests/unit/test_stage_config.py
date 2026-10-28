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
    for switch in ("access_control", "grounding", "injection_guard"):
        assert getattr(stage, switch) is False
    assert stage.collection_name == "Chunks_v0"


OTHER_VALUES = {
    "min_score": 0.5,
    "query_rewrite": True,
    "use_history": True,
    "region_filter": True,
    "access_control": True,
    "grounding": True,
    "injection_guard": True,
}


RERANK_ON = {
    "rerank": True,
    "rerank_model": "Xenova/ms-marco-MiniLM-L-6-v2",
    "fetch_k": 20,
    "rerank_fusion": "replace",
}


@pytest.mark.parametrize(("switch", "value"), OTHER_VALUES.items())
def test_unimplemented_switch_raises_naming_session(
    tmp_path: Path, switch: str, value: object
) -> None:
    """Any non-v0 value raises NotImplementedError that names the session adding it."""
    extra = RERANK_ON if switch == "min_score" else {}  # min_score needs reranking on
    name = _write_stage(tmp_path, name="probe", **extra, **{switch: value})
    session = UNIMPLEMENTED_SWITCHES[switch][1]
    with pytest.raises(NotImplementedError, match=rf"{switch}=.*Session {session}"):
        load_stage(name, stages_dir=tmp_path)


def test_every_switch_has_a_session() -> None:
    """The switch table covers exactly the toggles tested above."""
    assert set(UNIMPLEMENTED_SWITCHES) == set(OTHER_VALUES)


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
