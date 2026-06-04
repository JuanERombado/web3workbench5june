from __future__ import annotations

import json

from .models import MockRunResult
from .parsers import parse_foundry_output, parse_slither_output


def make_mock_run(tool: str, command: str) -> MockRunResult:
    if not command.startswith("mock:"):
        return MockRunResult(1, "ERROR: Milestone 1 supports mock commands only.\n")

    parts = command.split(":")
    if len(parts) != 3:
        return MockRunResult(1, "ERROR: Mock command must be mock:<tool>:<case>.\n")

    _, mock_tool, case = parts
    if mock_tool != tool:
        return MockRunResult(1, f"ERROR: Mock tool '{mock_tool}' does not match selected tool '{tool}'.\n")

    if tool == "foundry":
        return _mock_foundry(case)
    if tool == "slither":
        return _mock_slither(case)
    return MockRunResult(1, f"ERROR: Unsupported tool '{tool}'.\n")


def parse_tool_output(tool: str, raw_output: str, exit_code: int, parser_payload: str | None = None):
    if tool == "foundry":
        return parse_foundry_output(raw_output, exit_code)
    if tool == "slither":
        return parse_slither_output(raw_output, exit_code, parser_payload)
    raise ValueError(f"Unsupported tool: {tool}")


def _mock_foundry(case: str) -> MockRunResult:
    if case == "pass":
        return MockRunResult(0, "[PASS] testProof() (gas: 12345)\nSuite result: ok. 1 passed; 0 failed; 0 skipped\n")
    if case == "error":
        return MockRunResult(1, "Compiler run failed:\nError: Source file requires different compiler version\n")
    if case == "needs_review":
        return MockRunResult(1, "[FAIL: revert: invariant broken] testProof() (gas: 45678)\nSuite result: FAILED. 0 passed; 1 failed\n")
    return MockRunResult(1, f"ERROR: Unsupported foundry mock case '{case}'.\n")


def _mock_slither(case: str) -> MockRunResult:
    if case == "needs_review":
        payload = {
            "results": {
                "detectors": [
                    {
                        "check": "reentrancy-eth",
                        "impact": "High",
                        "elements": [
                            {
                                "type": "function",
                                "name": "withdraw",
                                "source_mapping": {"filename_relative": "src/Vault.sol"},
                            }
                        ],
                    }
                ]
            }
        }
        raw = json.dumps(payload, indent=2)
        return MockRunResult(0, raw + "\n", raw)
    if case == "pass":
        payload = {"results": {"detectors": []}}
        raw = json.dumps(payload, indent=2)
        return MockRunResult(0, raw + "\n", raw)
    if case == "error":
        return MockRunResult(1, "ERROR: Slither failed to analyze target.\n")
    return MockRunResult(1, f"ERROR: Unsupported slither mock case '{case}'.\n")
