#!/usr/bin/env python3
"""Append driver result rows into bench/RESULTS.md (issue #52 bench harness).

Rows are inserted as markdown table lines in the section matching the row's
surface, tagged with their machine tag. Append-or-fail on duplicate row
identity: existing recorded measurements are never silently overwritten.

Usage:
  python bench/append_results.py --row-file out.json
  python bench/append_results.py --row-json '{"surface": "native", ...}'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "bench" / "RESULTS.md"

# surface -> heading text in RESULTS.md
SECTION_HEADINGS = {
    "native": "## Native llama.cpp CPU results",
    "native-vulkan": "## Vulkan attempt",
    "wllama": "## wllama (browser WASM) results",
    "onnx-embed": "## ONNX embed/rerank cost",
    "onnx-rerank": "## ONNX embed/rerank cost",
}


def row_identity(row: dict) -> str:
    return "|".join(
        str(row.get(k, ""))
        for k in (
            "surface",
            "model",
            "quant",
            "threads",
            "prompt_tokens",
            "mode",
            "kind",
            "machine",
        )
    )


def row_to_markdown(row: dict) -> str:
    def fmt(v):
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    if row.get("surface") == "native-vulkan":
        cells = [
            row.get("machine", ""),
            row.get("model") or "-",
            row.get("bin") or "-",
            fmt(row.get("decode_tokens_per_second")),
            row.get("outcome", ""),
            "yes" if row.get("outcome") == "crash" else "-",
        ]
    elif row.get("surface") == "onnx-embed":
        cells = [
            row.get("machine", ""),
            row.get("model", ""),
            fmt(row.get("encode_ms")),
            fmt(row.get("embeddings_per_second")),
            row.get("outcome", ""),
        ]
    elif row.get("surface") == "onnx-rerank":
        cells = [
            row.get("machine", ""),
            row.get("model", ""),
            fmt(row.get("top15_ms")),
            fmt(row.get("top30_ms")),
            row.get("outcome", ""),
        ]
    elif row.get("surface") == "wllama":
        cells = [
            row.get("machine", ""),
            row.get("model", ""),
            row.get("mode", ""),
            fmt(row.get("threads")),
            fmt(row.get("prompt_tokens")),
            fmt(row.get("decode_tokens_per_second")),
            fmt(row.get("first_token_ms")),
            row.get("outcome", ""),
        ]
    else:  # native
        cells = [
            row.get("machine", ""),
            row.get("model", ""),
            row.get("quant", ""),
            fmt(row.get("threads")),
            fmt(row.get("prompt_tokens")),
            fmt(row.get("decode_tokens_per_second")),
            fmt(row.get("first_token_ms")),
            fmt(row.get("peak_rss_mb")),
            row.get("engine", row.get("outcome", "")),
            row.get("outcome", ""),
        ]
    return "| " + " | ".join(cells) + " |"


def append_row(row: dict, results_path: Path = RESULTS) -> None:
    surface = str(row.get("surface", ""))
    heading = SECTION_HEADINGS.get(surface)
    if heading is None:
        raise SystemExit(f"unknown surface: {surface!r}")
    text = results_path.read_text(encoding="utf-8")
    if heading not in text:
        raise SystemExit(f"section heading missing from {results_path}: {heading}")

    identity = row_identity(row)
    lines = text.splitlines()
    # Duplicate detection: same identity already recorded anywhere in the file.
    if identity in "\n".join(lines):
        raise SystemExit(f"row already recorded (append-or-fail): {identity}")

    heading_idx = next(i for i, ln in enumerate(lines) if ln.strip() == heading)
    # Find the last table row line following the heading (before next heading).
    insert_at = None
    for i in range(heading_idx + 1, len(lines)):
        ln = lines[i]
        if ln.startswith("## "):
            break
        if ln.startswith("|"):
            insert_at = i + 1
    if insert_at is None:
        raise SystemExit(f"no table found under section: {heading}")
    lines.insert(insert_at, row_to_markdown(row))
    results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"appended row into '{heading}': {identity}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--row-file", type=str)
    src.add_argument("--row-json", type=str)
    parser.add_argument("--results", type=str, default=str(RESULTS))
    args = parser.parse_args(argv)

    if args.row_file:
        row = json.loads(Path(args.row_file).read_text(encoding="utf-8"))
    else:
        row = json.loads(args.row_json)
    if isinstance(row, list):
        for r in row:
            append_row(r, Path(args.results))
    else:
        append_row(row, Path(args.results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
