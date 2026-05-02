"""Test runner: execute a parsed TestFile against a game binary.

Builds a stream of TurnRecord objects — one per turn — each carrying the
sent command, raw observed output, derived state (room identity, score,
score delta, won/lost flags), assertion results, and soft-diff drift info.

Streaming is supported via the `on_turn` callback, fired right after each
turn lands so a live display can update as the test runs.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

from . import diff as diff_mod
from . import i7, test_format
from .interpreter import (
    GlulxeInterpreter,
    InterpreterError,
    PromptTimeout,
)


Outcome = Literal["walkthrough", "scenario", "error"]
Status = Literal["pass", "fail"]


@dataclass
class AssertionResult:
    assertion: test_format.Assertion
    passed: bool
    detail: str = ""  # explanation when failed
    # Character ranges in the turn's observed output where the assertion's
    # pattern matched. For positive assertions on pass: the satisfying
    # match(es). For negation assertions on fail: the unwanted occurrences.
    # Empty otherwise (or for assertions with no observable match site).
    matches: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class TurnRecord:
    index: int  # 1-based within its phase (setup or body)
    command: str
    observed_output: str  # raw text from interpreter
    analysis: i7.TurnAnalysis  # parsed structural info
    state_after: i7.GameState  # snapshot at end of this turn
    assertions: list[AssertionResult] = field(default_factory=list)
    drift: Optional[list[diff_mod.DiffChunk]] = None  # soft-diff vs recorded_output, None if no drift
    error: Optional[str] = None  # turn-level fatal (timeout, etc.)
    elapsed_ms: float = 0.0
    is_setup: bool = False  # True if this came from a .before file
    before_source: Optional[str] = None  # which .before file (display only)

    @property
    def status(self) -> Status:
        if self.error is not None:
            return "fail"
        if any(not a.passed for a in self.assertions):
            return "fail"
        return "pass"


@dataclass
class TestResult:
    test: test_format.TestFile
    turns: list[TurnRecord]  # body turns only — what the .test file declared
    outcome: Outcome
    error: Optional[str] = None  # test-level fatal (couldn't launch, etc.)
    duration_ms: float = 0.0
    opening_text: str = ""  # banner + initial room printed before any command
    setup_turns: list[TurnRecord] = field(default_factory=list)  # from .before files

    @property
    def status(self) -> Status:
        if self.outcome == "error":
            return "fail"
        if any(t.status == "fail" for t in self.turns):
            return "fail"
        return "pass"

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def assertion_pass(self) -> int:
        return sum(1 for t in self.turns for a in t.assertions if a.passed)

    @property
    def assertion_total(self) -> int:
        return sum(len(t.assertions) for t in self.turns)


# ─── Execution ───────────────────────────────────────────────────────


def run_test(
    test: test_format.TestFile,
    *,
    interpreter: Optional[GlulxeInterpreter] = None,
    win_config: i7.WinConfig = i7.WinConfig(),
    on_turn: Optional[Callable[[TurnRecord], None]] = None,
    seed_override: Optional[str] = None,
    timeout_per_turn: float = 10.0,
) -> TestResult:
    """Execute a parsed TestFile end-to-end.

    `on_turn` fires after each turn lands so a live display can stream.
    Returns a TestResult after the interpreter has closed.
    """
    started = time.time()

    game_path = _resolve_game(test)
    if game_path is None or not game_path.is_file():
        return TestResult(
            test=test,
            turns=[],
            outcome="error",
            error=f"game file not found: {test.header.game!r}",
            duration_ms=(time.time() - started) * 1000,
        )

    seed = seed_override if seed_override is not None else test.header.seed
    interp = interpreter or GlulxeInterpreter()

    state = i7.GameState()
    records: list[TurnRecord] = []
    setup_records: list[TurnRecord] = []
    banner = ""

    try:
        interp.launch(game_path, seed=seed)
        # Read the initial banner up to the first prompt. We don't treat
        # it as a turn, but we DO feed it through analyze_turn so the
        # initial room/score register in state, and we keep the text
        # itself so the report can show the game's opening narrative.
        try:
            banner = interp.read_until_prompt(timeout=timeout_per_turn)
        except (PromptTimeout, InterpreterError) as e:
            return TestResult(
                test=test,
                turns=[],
                outcome="error",
                error=f"failed to read banner: {e}",
                duration_ms=(time.time() - started) * 1000,
            )
        banner_analysis = i7.analyze_turn(banner, win_config=win_config)
        state.apply_turn(banner_analysis)
        # Reset turn count — banner doesn't count as a player turn
        state.turn = 0

        # ─── .before files: silent setup (no assertions) ──────────
        for before_path_str in test.header.before:
            before_path = _resolve_before_path(test, before_path_str)
            try:
                before_file = test_format.parse_before_file(before_path)
            except (FileNotFoundError, OSError, test_format.TestFormatError) as e:
                return TestResult(
                    test=test, turns=[], setup_turns=setup_records,
                    outcome="error",
                    error=f"failed to load .before file {before_path_str!r}: {e}",
                    duration_ms=(time.time() - started) * 1000,
                    opening_text=banner,
                )
            for cmd in before_file.commands:
                t0 = time.time()
                try:
                    interp.send_command(cmd)
                    response = interp.read_until_prompt(timeout=timeout_per_turn)
                except (PromptTimeout, InterpreterError) as e:
                    return TestResult(
                        test=test, turns=[], setup_turns=setup_records,
                        outcome="error",
                        error=f"setup error in {before_path_str!r}: {e}",
                        duration_ms=(time.time() - started) * 1000,
                        opening_text=banner,
                    )
                analysis = i7.analyze_turn(response, win_config=win_config)
                state.apply_turn(analysis)
                rec = TurnRecord(
                    index=len(setup_records) + 1,
                    command=cmd,
                    observed_output=response,
                    analysis=analysis,
                    state_after=_snapshot_state(state),
                    elapsed_ms=(time.time() - t0) * 1000,
                    is_setup=True,
                    before_source=before_path_str,
                )
                setup_records.append(rec)
                if on_turn:
                    on_turn(rec)

        for idx, turn_def in enumerate(test.turns, start=1):
            t0 = time.time()
            record = TurnRecord(
                index=idx,
                command=turn_def.command,
                observed_output="",
                analysis=i7.TurnAnalysis(),
                state_after=state,  # placeholder; replaced below
            )
            try:
                interp.send_command(turn_def.command)
                response = interp.read_until_prompt(timeout=timeout_per_turn)
            except PromptTimeout as e:
                record.error = f"timeout: {e}"
                record.elapsed_ms = (time.time() - t0) * 1000
                records.append(record)
                if on_turn:
                    on_turn(record)
                break
            except InterpreterError as e:
                record.error = str(e)
                record.elapsed_ms = (time.time() - t0) * 1000
                records.append(record)
                if on_turn:
                    on_turn(record)
                break

            analysis = i7.analyze_turn(response, win_config=win_config)
            state.apply_turn(
                analysis,
                force_new_room=bool(
                    turn_def.room and turn_def.room.force_new
                ),
            )

            record.observed_output = response
            record.analysis = analysis
            record.state_after = _snapshot_state(state)
            record.assertions = _evaluate_assertions(turn_def.assertions, response)
            record.drift = _compute_drift(turn_def.recorded_output, response)
            record.elapsed_ms = (time.time() - t0) * 1000

            # Verify metadata-marker assertions (@?, $?)
            meta_failures = _evaluate_meta_assertions(turn_def, state)
            record.assertions.extend(meta_failures)

            records.append(record)
            if on_turn:
                on_turn(record)

            if state.ended:
                break
    finally:
        interp.close()

    outcome: Outcome = (
        "walkthrough" if state.won
        else "scenario"
    )
    duration_ms = (time.time() - started) * 1000
    return TestResult(
        test=test,
        turns=records,
        outcome=outcome,
        duration_ms=duration_ms,
        opening_text=banner,
        setup_turns=setup_records,
    )


# ─── Helpers ─────────────────────────────────────────────────────────


def _resolve_game(test: test_format.TestFile) -> Optional[Path]:
    if not test.header.game:
        return None
    raw = Path(test.header.game)
    if raw.is_absolute():
        return raw
    # Resolve relative to the .test file's directory if we know it
    if test.path is not None:
        return (test.path.parent / raw).resolve()
    return raw.resolve()


def _resolve_before_path(test: test_format.TestFile, before_str: str) -> Path:
    """Resolve a .before path relative to the .test file's directory."""
    raw = Path(before_str)
    if raw.is_absolute():
        return raw
    if test.path is not None:
        return (test.path.parent / raw).resolve()
    return raw.resolve()


def _evaluate_assertions(
    assertions: list[test_format.Assertion],
    response: str,
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    for a in assertions:
        matches: list[tuple[int, int]] = []
        if a.kind == "contains":
            matches = _all_substring_positions(response, a.text)
            passed = bool(matches)
            detail = "" if passed else f"missing substring: {a.text!r}"
        elif a.kind == "not_contains":
            occurrences = _all_substring_positions(response, a.text)
            passed = not occurrences
            # On failure, expose the unwanted occurrences so the report
            # can highlight where the forbidden text appeared.
            matches = [] if passed else occurrences
            detail = "" if passed else f"unexpected substring: {a.text!r}"
        elif a.kind == "regex":
            try:
                matches = [
                    (m.start(), m.end())
                    for m in re.finditer(a.text, response, re.IGNORECASE)
                ]
                passed = bool(matches)
                detail = "" if passed else f"no match: /{a.text}/"
            except re.error as e:
                passed = False
                detail = f"bad regex /{a.text}/: {e}"
        else:
            passed = False
            detail = f"unknown assertion kind: {a.kind}"
        results.append(
            AssertionResult(assertion=a, passed=passed, detail=detail, matches=matches)
        )
    return results


def _all_substring_positions(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Return [(start, end), ...] for every occurrence of needle in haystack."""
    if not needle:
        return []
    out: list[tuple[int, int]] = []
    cursor = 0
    n = len(needle)
    while True:
        idx = haystack.find(needle, cursor)
        if idx == -1:
            break
        out.append((idx, idx + n))
        cursor = idx + max(n, 1)
    return out


def _evaluate_meta_assertions(
    turn_def: test_format.Turn,
    state: i7.GameState,
) -> list[AssertionResult]:
    """Convert @? and $? markers into pseudo-assertions we report uniformly."""
    out: list[AssertionResult] = []

    if turn_def.room and turn_def.room.is_assertion:
        expected = turn_def.room.name
        expected_inst = turn_def.room.instance
        actual_room = state.room
        if actual_room is None:
            passed = False
            detail = f"expected room {expected!r}, but no room detected"
        elif actual_room.name != expected:
            passed = False
            detail = f"expected room {expected!r}, got {actual_room.name!r}"
        elif expected_inst is not None and actual_room.instance != expected_inst:
            passed = False
            detail = (
                f"expected room {expected!r} #{expected_inst}, "
                f"got #{actual_room.instance}"
            )
        else:
            passed = True
            detail = ""
        synthetic = test_format.Assertion(
            kind="contains",
            text=f"@? {expected}" + (f" #{expected_inst}" if expected_inst else ""),
            raw_line=f"@? {expected}",
            line_no=turn_def.line_no,
        )
        out.append(AssertionResult(synthetic, passed, detail))

    if turn_def.score and turn_def.score.is_assertion:
        expected_got = turn_def.score.score
        expected_max = turn_def.score.score_max
        actual_got = state.score
        actual_max = state.score_max
        if actual_got != expected_got:
            passed = False
            detail = f"expected score {expected_got}/{expected_max}, got {actual_got}"
        elif actual_max is not None and actual_max != expected_max:
            passed = False
            detail = (
                f"expected score-max {expected_max}, got {actual_max}"
            )
        else:
            passed = True
            detail = ""
        synthetic = test_format.Assertion(
            kind="contains",
            text=f"$? {expected_got}/{expected_max}",
            raw_line=f"$? {expected_got}/{expected_max}",
            line_no=turn_def.line_no,
        )
        out.append(AssertionResult(synthetic, passed, detail))

    return out


def _compute_drift(
    recorded: str, observed: str
) -> Optional[list[diff_mod.DiffChunk]]:
    """Return a word-level diff, or None if there's no real drift.

    'No real drift' means the word sequences match — line wrapping and
    other whitespace-only differences don't count.
    """
    if not recorded.strip():
        return None
    chunks = diff_mod.word_diff(recorded, observed)
    if not chunks or not diff_mod.has_drift(chunks):
        return None
    return chunks


def _snapshot_state(state: i7.GameState) -> i7.GameState:
    """Shallow copy of a GameState for storage in a TurnRecord.

    The RoomRegistry is shared across snapshots — that's fine because
    rooms are append-only, and snapshots only need to reference the
    current room identity (which is immutable).
    """
    return i7.GameState(
        room=state.room,
        previous_room=state.previous_room,
        score=state.score,
        score_max=state.score_max,
        turn=state.turn,
        ended=state.ended,
        won=state.won,
        lost=state.lost,
        rooms=state.rooms,
    )
