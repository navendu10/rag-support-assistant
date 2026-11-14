"""Run the prompt-injection red-team suite with chosen defence layers (Session 9).

Usage:
    python scripts/redteam.py --stage v7 --layers 1,2,3   # all layers
    python scripts/redteam.py --stage v7 --layers 1       # one layer alone
    python scripts/redteam.py --stage v7 --layers none    # no layers: v6 behaviour
    python scripts/redteam.py --stage v7 --rescore         # re-score stored answers, no LLM

Layer 1 works at ingestion, so it decides the index: on -> v7's sanitised collections,
off -> v7raw's unsanitised copy of the same data (red-team runs only). Layers 2 and 3 are
switched on the stage. Each run is saved to results/v7/redteam/<layers>.jsonl, and
results/v7/redteam.md is rebuilt from every saved run: the pass rate (attacks blocked)
per technique, and every attack that got through. Customer tickets stay in memory.
``--rescore`` re-applies the current ``must_not_contain`` claims to every stored run's
answers without answering again.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from rag_assistant.config import get_settings
from rag_assistant.graph.handoff import MemoryTicketStore
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import load_users
from rag_assistant.security.redteam import (
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
    parser.add_argument("--stage", default="v7", choices=["v7"], help="the defended stage")
    parser.add_argument("--layers", default="1,2,3", help='e.g. "1,2,3", "2" or "none"')
    parser.add_argument(
        "--rescore", action="store_true", help="re-score every stored run, no LLM calls"
    )
    return parser.parse_args()


def _cell(text: str) -> str:
    """Make text safe for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def run(layers: tuple[int, ...]) -> list[AttackResult]:
    """Answer every attack with the stage for ``layers`` and score it."""
    settings = get_settings()
    attacks = load_attacks(settings.paths.resolve(ATTACKS_FILE))
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    stage = stage_for_layers(layers)
    results = []
    with Pipeline.from_stage(
        stage, settings, tickets=MemoryTicketStore(), allow_redteam=True
    ) as pipeline:
        for attack in attacks:
            answer = pipeline.answer(attack.question, users[attack.user_id])
            hits = score(attack, answer.text)
            path = answer.path
            results.append(
                AttackResult(
                    id=attack.id,
                    technique=attack.technique,
                    layers=list(layers),
                    answer=answer.text,
                    hits=hits,
                    handoff_reason=None if path is None else path.handoff_reason,
                    guard_triggered=bool(path and path.guard_triggered),
                    context_doc_ids=[c.doc_id for c in answer.context],
                )
            )
            status = "GOT THROUGH: " + ", ".join(hits) if hits else "blocked"
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


def main() -> int:
    """Run one layer configuration (or re-score them all), save it, rebuild the report."""
    args = parse_args()
    settings = get_settings()
    out_dir = settings.paths.resolve(settings.paths.results_dir) / args.stage
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
