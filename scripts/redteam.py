"""Run the prompt-injection red-team suite with chosen defence layers (Session 9).

Usage:
    python scripts/redteam.py --stage v7 --layers 1,2,3   # all layers
    python scripts/redteam.py --stage v7 --layers 1       # one layer alone
    python scripts/redteam.py --stage v7 --layers none    # no layers: v6 behaviour
    python scripts/redteam.py --stage v7 --rescore         # re-score stored answers, no LLM
    python scripts/redteam.py --stage v8                   # the agent, all layers, vs v7
    python scripts/redteam.py --stage v9                   # approval for writes, vs v8, v7
    python scripts/redteam.py --stage v10 --ids r19,r20    # tools over MCP, chosen attacks

Layer 1 works at ingestion, so it decides the index: on -> v7's sanitised collections,
off -> v7raw's unsanitised copy of the same data (red-team runs only). Layers 2 and 3 are
switched on the stage. Each run is saved to results/v7/redteam/<layers>.jsonl, and
results/v7/redteam.md is rebuilt from every saved run: the pass rate (attacks blocked)
per technique, and every attack that got through. Customer tickets stay in memory.
``--rescore`` re-applies the current ``must_not_contain`` claims to every stored run's
answers without answering again.

On an agent stage (v8, v9) an attack also counts as getting through if it wrote a ticket
(the red team never approves anything); results go to results/<stage>/redteam/agent.jsonl
and results/<stage>/redteam.md, compared attack by attack with the stage before it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from rag_assistant.agent.graph import open_pipeline
from rag_assistant.config import StageConfig, get_settings, load_stage
from rag_assistant.graph.handoff import MemoryTicketStore
from rag_assistant.schemas import load_users
from rag_assistant.security.redteam import (
    ALL_LAYERS,
    AttackResult,
    layers_label,
    load_attacks,
    load_results,
    parse_layers,
    pass_table,
    score,
    stage_for_layers,
    write_results,
)

ATTACKS_FILE = Path("data/redteam/injection.jsonl")
RUN_ORDER = ("1,2,3", "1", "2", "3", "none")


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stage",
        default="v7",
        choices=["v7", "v8", "v9", "v10"],
        help="v7 (layers), or an agent stage",
    )
    parser.add_argument("--layers", default="1,2,3", help='e.g. "1,2,3", "2" or "none"')
    parser.add_argument(
        "--rescore", action="store_true", help="re-score every stored run, no LLM calls"
    )
    parser.add_argument("--ids", help="agent stages: comma-separated attack ids (default: all)")
    return parser.parse_args()


def _cell(text: str) -> str:
    """Make text safe for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def run(
    layers: tuple[int, ...], stage: StageConfig | None = None, ids: set[str] | None = None
) -> list[AttackResult]:
    """Answer every attack with the stage for ``layers`` (or ``stage``) and score it."""
    settings = get_settings()
    attacks = [
        a for a in load_attacks(settings.paths.resolve(ATTACKS_FILE)) if ids is None or a.id in ids
    ]
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    stage = stage or stage_for_layers(layers)
    results = []
    with open_pipeline(stage, settings, MemoryTicketStore(), allow_redteam=True) as pipeline:
        submitter = getattr(pipeline, "submitter", None)
        for attack in attacks:
            before = len(submitter.tickets.all()) if submitter is not None else 0
            answer = pipeline.answer(attack.question, users[attack.user_id])
            hits = score(attack, answer.text)
            written = len(submitter.tickets.all()) - before if submitter is not None else 0
            path = answer.path
            results.append(
                AttackResult(
                    id=attack.id,
                    technique=attack.technique,
                    layers=list(layers),
                    tools=[] if answer.agent is None else list(answer.agent.tools_used),
                    answer=answer.text,
                    hits=hits,
                    handoff_reason=None if path is None else path.handoff_reason,
                    guard_triggered=bool(path and path.guard_triggered),
                    context_doc_ids=[c.doc_id for c in answer.context],
                    tickets_written=written,
                )
            )
            problems = [*hits, *([f"{written} tickets written"] if written else [])]
            status = "GOT THROUGH: " + ", ".join(problems) if problems else "blocked"
            print(f"[{layers_label(layers):>5}] {attack.id} {attack.technique:<26} {status}")
    return results


def write_report(out_dir: Path) -> Path:
    """Rebuild redteam.md from every saved run, in a fixed column order."""
    settings = get_settings()
    attacks = load_attacks(settings.paths.resolve(ATTACKS_FILE))
    runs = {
        label: load_results(out_dir / "redteam" / f"{label}.jsonl")
        for label in RUN_ORDER
        if (out_dir / "redteam" / f"{label}.jsonl").is_file()
    }
    through = [
        "| layers | id | technique | claim that got through | answer |",
        "|---|---|---|---|---|",
    ]
    for label, results in runs.items():
        for r in results:
            if r.hits:
                through.append(
                    f"| {label} | {r.id} | {r.technique} | {_cell(', '.join(r.hits))} | "
                    f"{_cell(r.answer[:200])} |"
                )
    report = "\n\n".join(
        [
            "# Red-team results: stage `v7`",
            f"- Date: {date.today().isoformat()}; attacks: `{ATTACKS_FILE.as_posix()}` "
            f"({len(attacks)}); blocked = no `must_not_contain` claim in the answer",
            "- Layer 1 on: v7's sanitised collections; off: v7raw (the same data, "
            "unsanitised). Layers 2 and 3 are switched on the stage. `none` is v6's "
            "behaviour on the same data.",
            "## Attacks blocked, per technique",
            pass_table(runs, attacks),
            "## Attacks that got through",
            "\n".join(through) if len(through) > 2 else "None.",
        ]
    )
    path = out_dir / "redteam.md"
    path.write_text(report + "\n", encoding="utf-8", newline="\n")
    return path


def rescore(out_dir: Path) -> None:
    """Re-apply the current claims to every stored run's answers (no LLM calls)."""
    attacks = {a.id: a for a in load_attacks(get_settings().paths.resolve(ATTACKS_FILE))}
    for label in RUN_ORDER:
        path = out_dir / "redteam" / f"{label}.jsonl"
        if path.is_file():
            results = [
                r.model_copy(update={"hits": score(attacks[r.id], r.answer)})
                for r in load_results(path)
            ]
            write_results(path, results)
            blocked = sum(r.blocked for r in results)
            print(f"layers {label}: {blocked}/{len(results)} attacks blocked (rescored)")


def _stored(path: Path) -> dict[str, AttackResult]:
    """A stored run by attack id, or nothing if it doesn't exist."""
    return {r.id: r for r in load_results(path)} if path.is_file() else {}


def _status(result: AttackResult | None) -> str:
    """``blocked``, ``**through**``, or ``-`` when the attack wasn't run."""
    if result is None:
        return "-"
    return "blocked" if result.blocked else "**through**"


def run_agent(stage_name: str, results_dir: Path, ids: set[str] | None = None) -> int:
    """v8 or v9: the agent with every v7 layer, compared attack by attack with the stages
    before it (v7's all-layers run, and v8 for v9).

    Any attack an earlier stage blocked that gets through here is listed first and flagged.
    """
    out_dir = results_dir / stage_name
    results = run(ALL_LAYERS, load_stage(stage_name), ids)
    write_results(out_dir / "redteam" / "agent.jsonl", results)
    earlier = {"v7": _stored(results_dir / "v7" / "redteam" / "1,2,3.jsonl")}
    for name in {"v9": ["v8"], "v10": ["v8", "v9"]}.get(stage_name, []):
        earlier[name] = _stored(results_dir / name / "redteam" / "agent.jsonl")
    attacks = [
        a
        for a in load_attacks(get_settings().paths.resolve(ATTACKS_FILE))
        if ids is None or a.id in ids
    ]
    earlier = {
        name: {k: v for k, v in runs.items() if ids is None or k in ids}
        for name, runs in earlier.items()
    }
    runs = {f"{name} (stored)": list(r.values()) for name, r in earlier.items() if r}
    table = pass_table({**runs, stage_name: results}, attacks)
    regressions = [
        r.id
        for r in results
        if not r.blocked and any(r.id in e and e[r.id].blocked for e in earlier.values())
    ]
    names = list(earlier)
    lines = [
        f"| id | technique | {' | '.join(names)} | {stage_name} | tickets | tools | answer |",
        "|---|---|" + "---|" * len(names) + "---|---|---|---|",
    ]
    for r in results:
        before = " | ".join(_status(earlier[n].get(r.id)) for n in names)
        lines.append(
            f"| {r.id} | {r.technique} | {before} | {_status(r)} | {r.tickets_written} | "
            f"{_cell(', '.join(r.tools) or '-')} | {_cell(r.answer[:160])} |"
        )
    flag = (
        f"**REGRESSION: attacks blocked before got through on {stage_name}: "
        + ", ".join(regressions)
        + ".**"
        if regressions
        else f"No attack that an earlier stage blocked gets through on {stage_name}."
    )
    blocked = sum(r.blocked for r in results)
    report = "\n\n".join(
        [
            f"# Red-team results: stage `{stage_name}` (the agent) vs {', '.join(names)}",
            f"- Date: {date.today().isoformat()}; attacks: `{ATTACKS_FILE.as_posix()}` "
            f"({len(attacks)}); every v7 layer plus the agent's tools. Blocked = no "
            "`must_not_contain` claim in the answer and no ticket written. `-`: the attack "
            "didn't exist when that stage was run.",
            f"- **{stage_name}: {blocked}/{len(results)} blocked.** {flag}",
            "## Attacks blocked, per technique",
            table,
            "## Per attack",
            "\n".join(lines),
        ]
    )
    (out_dir / "redteam.md").write_text(report + "\n", encoding="utf-8", newline="\n")
    print(f"\n{stage_name}: {blocked}/{len(results)} blocked. {flag}")
    return 0


def main() -> int:
    """Run one layer configuration (or re-score them all), save it, rebuild the report."""
    args = parse_args()
    settings = get_settings()
    out_dir = settings.paths.resolve(settings.paths.results_dir) / args.stage
    if load_stage(args.stage).agent:
        ids = {i.strip() for i in args.ids.split(",")} if args.ids else None
        return run_agent(args.stage, settings.paths.resolve(settings.paths.results_dir), ids)
    if args.rescore:
        rescore(out_dir)
        print(f"Report: {write_report(out_dir)}")
        return 0
    layers = parse_layers(args.layers)
    results = run(layers)
    write_results(out_dir / "redteam" / f"{layers_label(layers)}.jsonl", results)
    blocked = sum(r.blocked for r in results)
    print(f"\nlayers {layers_label(layers)}: {blocked}/{len(results)} attacks blocked")
    print(f"Report: {write_report(out_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
