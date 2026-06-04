from __future__ import annotations

import json
import re
from collections import Counter

from .models import ParseResult


def parse_foundry_output(raw_output: str, exit_code: int) -> ParseResult:
    text = raw_output.lower()
    if exit_code != 0 and any(marker in text for marker in ("compiler run failed", "compilation failed", "error:")):
        return ParseResult("ERROR", "Foundry command failed due to compile or tooling output.", {"exit_code": exit_code})

    if re.search(r"\b0 failed\b", text) and re.search(r"\b\d+ passed\b", text):
        return ParseResult("PASS", "Foundry reported passing tests with no failures.", {"exit_code": exit_code})

    if any(marker in text for marker in ("failed", "revert", "panic", "counterexample")):
        return ParseResult("NEEDS_REVIEW", "Foundry output includes a failure or revert that needs manual review.", {"exit_code": exit_code})

    if exit_code != 0:
        return ParseResult("ERROR", "Foundry command exited non-zero without a hypothesis-readable failure.", {"exit_code": exit_code})

    return ParseResult("NEEDS_REVIEW", "Foundry output was inconclusive.", {"exit_code": exit_code})


def parse_slither_output(raw_output: str, exit_code: int, json_payload: str | None = None) -> ParseResult:
    if exit_code != 0:
        return ParseResult("ERROR", "Slither command failed.", {"exit_code": exit_code})

    payload = json_payload or raw_output
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ParseResult("ERROR", "Slither JSON output could not be parsed.", {"exit_code": exit_code})

    detectors = data.get("results", {}).get("detectors", [])
    counts: Counter[str] = Counter()
    locations: list[str] = []

    for finding in detectors:
        impact = str(finding.get("impact", "Informational")).lower()
        counts[impact] += 1
        first_element = (finding.get("elements") or [{}])[0]
        source_mapping = first_element.get("source_mapping", {})
        filename = source_mapping.get("filename_relative") or source_mapping.get("filename_absolute")
        function_name = first_element.get("name")
        if filename or function_name:
            locations.append("::".join(part for part in (filename, function_name) if part))

    high = counts["high"]
    medium = counts["medium"]
    low = counts["low"]
    info = counts["informational"] + counts["info"]
    summary = f"Slither findings: high={high}, medium={medium}, low={low}, info={info}."
    if locations:
        summary += " Top locations: " + ", ".join(locations[:3]) + "."

    status = "NEEDS_REVIEW" if high or medium else "PASS"
    return ParseResult(
        status,
        summary,
        {
            "exit_code": exit_code,
            "counts": {"high": high, "medium": medium, "low": low, "info": info},
            "top_locations": locations[:5],
        },
    )
