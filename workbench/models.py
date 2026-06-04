from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolStatus = Literal["PENDING", "PASS", "FAIL", "NEEDS_REVIEW", "ERROR"]
ManualVerdict = Literal["UNREVIEWED", "KEEP", "KILL", "NEEDS_MORE_CONTEXT", "BUILD_POC"]
ToolName = Literal["foundry", "slither"]

TOOL_STATUSES: tuple[str, ...] = ("PENDING", "PASS", "FAIL", "NEEDS_REVIEW", "ERROR")
MANUAL_VERDICTS: tuple[str, ...] = (
    "UNREVIEWED",
    "KEEP",
    "KILL",
    "NEEDS_MORE_CONTEXT",
    "BUILD_POC",
)
TOOLS: tuple[str, ...] = ("foundry", "slither")


@dataclass(frozen=True)
class ParseResult:
    tool_status: str
    summary: str
    metadata: dict


@dataclass(frozen=True)
class MockRunResult:
    exit_code: int
    raw_output: str
    parser_payload: str | None = None
