"""Rich-based collapsible renderer for test runs.

Five verbosity levels:

    L0 (--quiet)  one line per test (overall pass/fail + counts)
    L1 (default)  one line per turn (command, room, score, asserts)
    L2 (-v)       L1 + indented game-output block per turn
    L3 (-vv)      L2 + parser errors, drift summaries, raw bytes
    L4 (-vvv)     L3 + DEBUG verb dumps (when available)

Failure auto-expand: regardless of base level, failing turns render at
L2 just for that turn so users see what went wrong inline.

Streaming: each call to `on_turn(record)` appends a row immediately so
users watch progress. `finish(result)` prints the summary.
"""

from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.text import Text

from . import i7, runner, test_format


def _truncate(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


# Verbosity levels
L_QUIET = 0
L_DEFAULT = 1
L_VERBOSE = 2
L_DEBUG = 3
L_TRACE = 4


class Display:
    """Streaming + summary renderer for a single test run."""

    def __init__(
        self,
        *,
        verbosity: int = L_DEFAULT,
        console: Optional[Console] = None,
    ) -> None:
        self.verbosity = verbosity
        self.console = console or Console()
        self._first_turn_printed = False

    # ─── Lifecycle ─────────────────────────────────────────────────

    def start(self, test: test_format.TestFile) -> None:
        if self.verbosity >= L_DEFAULT:
            name = test.header.test or (test.path.name if test.path else "test")
            self.console.print(f"[bold]· {name}[/bold]")
        self._first_turn_printed = False

    def on_turn(self, record: runner.TurnRecord) -> None:
        if self.verbosity == L_QUIET:
            return  # quiet mode: only the final summary
        if record.is_setup and self.verbosity < L_VERBOSE:
            return  # setup turns are silent at default verbosity
        self._render_turn(record)
        self._first_turn_printed = True

    def finish(self, result: runner.TestResult) -> None:
        if self.verbosity == L_QUIET:
            self._render_quiet_line(result)
        else:
            self._render_summary(result)

    # ─── Per-turn rendering ────────────────────────────────────────

    def _render_turn(self, record: runner.TurnRecord) -> None:
        # If this turn failed, force at least L2 just for this turn
        effective = max(self.verbosity, L_VERBOSE) if record.status == "fail" else self.verbosity

        # L1 row
        self.console.print(self._row(record))

        if effective >= L_VERBOSE:
            self._render_output_block(record)
            self._render_failed_assertions_inline(record)
        if effective >= L_DEBUG:
            self._render_debug_panel(record)

    def _row(self, record: runner.TurnRecord) -> Text:
        """One-line summary row in L1 format. Truncates command/room to fit ~80 cols."""
        status_glyph = self._status_glyph(record)
        cmd = _truncate(record.command or "(empty)", 22)
        room = _truncate(self._room_label(record), 22)
        score = self._score_label(record)
        turn_label = self._turn_label(record)
        score_delta = self._score_delta(record)
        outcome = self._outcome_marker(record)
        asserts = self._assert_count(record)

        asserts_text, asserts_style = self._assert_count_styled(record)

        line = Text()
        line.append(f"  {status_glyph} ", style=self._status_style(record))
        line.append(f"> {cmd}".ljust(25), style="cyan")
        line.append(room.ljust(24), style="magenta")
        line.append(score.ljust(7), style="yellow")
        line.append(turn_label.ljust(5), style="dim")
        line.append((score_delta or "").ljust(4), style="green")
        line.append((outcome or "").ljust(5),
                    style="bold green" if outcome == "WIN" else "bold red")
        line.append(asserts_text, style=asserts_style)
        return line

    def _render_output_block(self, record: runner.TurnRecord) -> None:
        """Indented game-output block under the row."""
        text = record.observed_output.strip()
        if not text:
            return
        for line in text.splitlines():
            self.console.print(f"      {line}", style="dim white", highlight=False)
        self.console.print()  # spacer

    def _render_failed_assertions_inline(self, record: runner.TurnRecord) -> None:
        """When a turn failed, show what went wrong right under it."""
        if record.status != "fail":
            return
        for a in record.assertions:
            if a.passed:
                continue
            self.console.print(
                f"      [red]✗[/red] {a.assertion.raw_line.strip()}  "
                f"[dim]— {a.detail}[/dim]"
            )
        if record.error:
            self.console.print(f"      [red]✗ {record.error}[/red]")
        self.console.print()

    def _render_debug_panel(self, record: runner.TurnRecord) -> None:
        """L3+: parser errors, drift, raw bytes."""
        an = record.analysis
        if an.parser_errors:
            self.console.print(
                f"      [yellow]parser:[/yellow] {', '.join(an.parser_errors)}"
            )
        if record.drift:
            self.console.print("      [yellow]drift:[/yellow]")
            self.console.print(self._render_drift_text(record.drift))
        if self.verbosity >= L_TRACE:
            byte_len = len(record.observed_output.encode("utf-8"))
            self.console.print(
                f"      [dim]raw: {byte_len} bytes · "
                f"elapsed {record.elapsed_ms:.0f}ms[/dim]"
            )

    def _render_drift_text(self, chunks) -> Text:
        """Word-level diff colored for terminal display.

        red strikethrough = removed (in recorded only)
        green             = added (in observed only)
        dim               = unchanged context
        """
        line = Text("        ")
        for i, chunk in enumerate(chunks):
            text = chunk.text.strip()
            if not text:
                continue
            if i > 0:
                line.append(" ")
            if chunk.kind == "equal":
                line.append(text, style="dim")
            elif chunk.kind == "delete":
                line.append(text, style="red strike")
            elif chunk.kind == "insert":
                line.append(text, style="green")
        return line

    # ─── Summary rendering ─────────────────────────────────────────

    def _render_summary(self, result: runner.TestResult) -> None:
        glyph, style = ("✓", "green") if result.status == "pass" else ("✗", "red")
        outcome = self._summary_outcome(result)
        score = self._summary_score(result)
        asserts = f"{result.assertion_pass}/{result.assertion_total} ✓"

        self.console.print()
        bits = [f"{result.turn_count} turns"]
        if outcome:
            bits.append(outcome)
        if score:
            bits.append(score)
        bits.append(asserts)
        if result.error:
            bits.append(f"[red]error: {result.error}[/red]")
        self.console.print(
            f"  [{style}]{glyph}[/{style}] " + " · ".join(bits)
        )

    def _render_quiet_line(self, result: runner.TestResult) -> None:
        glyph, style = ("✓", "green") if result.status == "pass" else ("✗", "red")
        name = result.test.header.test or (
            result.test.path.name if result.test.path else "test"
        )
        outcome = self._summary_outcome(result)
        score = self._summary_score(result)
        asserts = f"{result.assertion_pass}/{result.assertion_total} ✓"
        bits = [f"{result.turn_count} turns"]
        if outcome:
            bits.append(outcome)
        if score:
            bits.append(score)
        bits.append(asserts)
        self.console.print(
            f"[{style}]{glyph}[/{style}] {name}  " + " · ".join(bits)
        )

    # ─── Field formatters ──────────────────────────────────────────

    def _status_glyph(self, record: runner.TurnRecord) -> str:
        return "✓" if record.status == "pass" else "✗"

    def _status_style(self, record: runner.TurnRecord) -> str:
        return "green" if record.status == "pass" else "red"

    def _room_label(self, record: runner.TurnRecord) -> str:
        room = record.state_after.room
        if room is None:
            return "—"
        return f"@ {room.label}"

    def _score_label(self, record: runner.TurnRecord) -> str:
        s = record.state_after
        if s.score_max is not None:
            return f"$ {s.score}/{s.score_max}"
        if s.score:
            return f"$ {s.score}"
        return ""

    def _turn_label(self, record: runner.TurnRecord) -> str:
        return f"T:{record.state_after.turn}"

    def _score_delta(self, record: runner.TurnRecord) -> str:
        d = record.analysis.score_delta
        if d > 0:
            return f"+{d}"
        if d < 0:
            return f"{d}"
        return ""

    def _outcome_marker(self, record: runner.TurnRecord) -> str:
        s = record.state_after
        if s.won:
            return "WIN"
        if s.lost:
            return "LOSE"
        if s.ended:
            return "END"
        return ""

    def _assert_count(self, record: runner.TurnRecord) -> str:
        text, _ = self._assert_count_styled(record)
        return text

    def _assert_count_styled(self, record: runner.TurnRecord) -> tuple[str, str]:
        passed = sum(1 for a in record.assertions if a.passed)
        total = len(record.assertions)
        if total == 0:
            return "—", "dim"
        if passed == total:
            return f"{passed}/{total} ✓", "dim"
        return f"{passed}/{total}", "red bold"

    def _summary_outcome(self, result: runner.TestResult) -> str:
        if result.outcome == "walkthrough":
            return "WALKTHROUGH"
        if result.outcome == "scenario":
            return "scenario"
        return ""

    def _summary_score(self, result: runner.TestResult) -> str:
        if not result.turns:
            return ""
        last = result.turns[-1].state_after
        if last.score_max is not None:
            return f"{last.score}/{last.score_max}"
        if last.score:
            return f"score {last.score}"
        return ""


# ─── Multi-test summary ────────────────────────────────────────────


def render_suite_summary(
    results: list[runner.TestResult],
    *,
    console: Optional[Console] = None,
) -> None:
    """Print a final summary across many tests run as a suite."""
    c = console or Console()
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    walkthroughs = sum(1 for r in results if r.outcome == "walkthrough")

    c.print()
    c.print("─" * 64)
    c.print(
        f"  [bold]{len(results)}[/bold] tests · "
        f"[green]{passed} passed[/green] · "
        + (f"[red]{failed} failed[/red]" if failed else f"{failed} failed")
        + f" · {walkthroughs} walkthrough(s)"
    )
    if failed:
        c.print("\n  failed:")
        for r in results:
            if r.status == "fail":
                name = r.test.header.test or (
                    r.test.path.name if r.test.path else "?"
                )
                first_fail = next(
                    (t for t in r.turns if t.status == "fail"),
                    None,
                )
                where = f" at turn {first_fail.index}" if first_fail else ""
                c.print(f"    [red]✗[/red] {name}{where}")
