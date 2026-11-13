"""Calibrate the invented-content judge against hand labels (Session 8).

Usage:
    python scripts/calibrate_invented_judge.py                # small model (the default judge)
    python scripts/calibrate_invented_judge.py --model large

Reads data/golden/invented_calibration.jsonl (stage, id, hand label, why), rebuilds each
stored answer's context offline, runs the judge and prints the agreement. Writes
results/calibration/invented_judge_<model>.md. Only rows with ``counted: true`` count
towards the agreement; the others are subtle extra cases, reported alongside.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from rag_assistant.config import get_settings, load_prompts, load_stage
from rag_assistant.eval.contexts import chunk_lookup, stored_context
from rag_assistant.eval.judge import Judge
from rag_assistant.eval.runner import judge_invented, load_rows, output_dir
from rag_assistant.generation.grounded import format_sources
from rag_assistant.llm import make_chat_model

LABELS_FILE = "data/golden/invented_calibration.jsonl"


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", choices=("small", "large"), default="small")
    return parser.parse_args()


def _cell(text: str) -> str:
    """Make text safe for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    """Judge every labelled answer and report the agreement."""
    args = parse_args()
    settings = get_settings()
    model = settings.llm.small_model if args.model == "small" else settings.llm.large_model
    judge = Judge(make_chat_model(settings, model), load_prompts())
    generated = settings.paths.resolve(settings.paths.generated_dir)
    labels = [
        json.loads(line)
        for line in settings.paths.resolve(settings.paths.golden_file)
        .with_name("invented_calibration.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    lookups, rows = {}, {}
    lines = [
        "| id | counted | hand label | judge | agree | judge reason |",
        "|---|---|---|---|---|---|",
    ]
    agree = counted = 0
    for label in labels:
        stage = load_stage(label["stage"])
        if stage.name not in lookups:
            lookups[stage.name] = chunk_lookup(stage, generated)
            rows[stage.name] = {r.id: r for r in load_rows(output_dir(settings, stage, False))}
        row = rows[stage.name][label["id"]]
        sources = format_sources(stored_context(row, lookups[stage.name]))
        verdict = judge_invented(judge, row, sources)
        same = verdict.verdict == label["label"]
        if label["counted"]:
            counted += 1
            agree += same
        print(f"{label['id']}: hand {label['label']:<9} judge {verdict.verdict}  {verdict.reason}")
        lines.append(
            f"| {label['id']} | {'yes' if label['counted'] else 'extra'} | {label['label']} | "
            f"{verdict.verdict or 'ERROR'} | {'yes' if same else '**no**'} | "
            f"{_cell(verdict.reason)} |"
        )
    summary = f"Agreement on counted items: {agree}/{counted} (model `{model}`)"
    print(f"\n{summary}")
    out = settings.paths.resolve(settings.paths.results_dir) / "calibration"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"invented_judge_{args.model}.md").write_text(
        "\n\n".join(
            [
                "# Invented-content judge calibration",
                f"- Date: {date.today().isoformat()}; labels: `{LABELS_FILE}` (hand-labelled "
                "against each answer's rebuilt context); keep the judge at 9/10 or better",
                f"- {summary}",
                "\n".join(lines),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
