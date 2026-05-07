"""Export TestResult objects to IF Hub's test-results.json format.

Produces JSON compatible with the Sharpee tests viewer, with optional
I7-specific extension fields (room, score, drift, parser errors).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import runner, test_format


_KIND_MAP: dict[test_format.AssertKind, str] = {
    "contains": "ok-contains",
    "not_contains": "ok-not-contains",
    "regex": "ok-matches",
}


def _export_assertion(ar: runner.AssertionResult) -> dict[str, Any]:
    return {
        "type": _KIND_MAP.get(ar.assertion.kind, ar.assertion.kind),
        "passed": ar.passed,
        "value": ar.assertion.text,
    }


def _export_turn(tr: runner.TurnRecord) -> dict[str, Any]:
    cmd: dict[str, Any] = {
        "lineNumber": tr.index,
        "input": tr.command,
        "output": tr.observed_output.rstrip(),
        "passed": tr.status == "pass",
        "skipped": False,
        "assertions": [_export_assertion(a) for a in tr.assertions],
    }

    if tr.assertions:
        labels = [a.assertion.label for a in tr.assertions if a.assertion.label]
        if labels:
            cmd["comment"] = "\n".join(labels)

    # I7 extension fields (viewer ignores when absent)
    room = tr.state_after.room
    if room:
        cmd["room"] = room.label

    if tr.state_after.score_max is not None:
        cmd["score"] = f"{tr.state_after.score}/{tr.state_after.score_max}"
    elif tr.state_after.score:
        cmd["score"] = str(tr.state_after.score)

    if tr.analysis.score_delta:
        cmd["scoreDelta"] = tr.analysis.score_delta

    if tr.state_after.won:
        cmd["outcome"] = "win"
    elif tr.state_after.lost:
        cmd["outcome"] = "lose"

    if tr.analysis.parser_errors:
        cmd["parserErrors"] = list(tr.analysis.parser_errors)

    if tr.drift:
        cmd["drift"] = [{"kind": c.kind, "text": c.text} for c in tr.drift]

    return cmd


def _export_result(result: runner.TestResult) -> dict[str, Any]:
    commands = [_export_turn(t) for t in result.turns]
    passed = sum(1 for c in commands if c["passed"])
    failed = sum(1 for c in commands if not c["passed"])

    title = result.test.header.test or ""
    filename = result.test.path.name if result.test.path else title

    return {
        "file": filename,
        "title": title or filename,
        "description": "",
        "commands": commands,
        "summary": {
            "passed": passed,
            "failed": failed,
            "skipped": 0,
            "duration": round(result.duration_ms),
        },
    }


def emit_json(
    results: list[runner.TestResult],
    *,
    story_id: str = "",
) -> dict[str, Any]:
    transcripts = [_export_result(r) for r in results]

    total_passed = sum(t["summary"]["passed"] for t in transcripts)
    total_failed = sum(t["summary"]["failed"] for t in transcripts)
    total_duration = sum(t["summary"]["duration"] for t in transcripts)

    return {
        "engine": "inform7",
        "storyId": story_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transcripts": transcripts,
        "summary": {
            "totalPassed": total_passed,
            "totalFailed": total_failed,
            "totalSkipped": 0,
            "totalDuration": total_duration,
        },
    }


def write_json(
    results: list[runner.TestResult],
    path: Path,
    *,
    story_id: str = "",
) -> None:
    data = emit_json(results, story_id=story_id)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
