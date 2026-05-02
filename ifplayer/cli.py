"""Command-line entry point for ifPlayer.

Subcommands:
  play <game>                    interactive REPL
  run <test> [--game G]          run one .test file
  test <tests...> [--game G]     run many .test files, summary across all
  new <name> --game G            stub a new .test file
  update <test>                  re-run, capture observed output, rewrite file
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from . import display as display_mod
from . import repl as repl_mod
from . import report as report_mod
from . import runner, test_format


def _verbosity(verbose: int, quiet: bool) -> int:
    if quiet:
        return display_mod.L_QUIET
    return min(display_mod.L_DEFAULT + verbose, display_mod.L_TRACE)


def _make_console() -> Console:
    """Build a Console that handles Unicode glyphs on legacy Windows shells."""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    return Console(legacy_windows=False)


@click.group(invoke_without_command=False)
@click.version_option()
def main() -> None:
    """Inform 7 test runner. Drives glulxe via subprocess and reports pass/fail."""


# ─── play ────────────────────────────────────────────────────────────


@main.command()
@click.argument("game", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--seed", type=str, default=None, help="RNG seed.")
def play(game: Path, seed: Optional[str]) -> None:
    """Open an interactive REPL against GAME."""
    code = repl_mod.play(game.resolve(), seed=seed)
    sys.exit(code)


# ─── run ─────────────────────────────────────────────────────────────


@main.command()
@click.argument("test_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--game", "game_override", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="Override the game path from the test header.")
@click.option("--seed", type=str, default=None, help="Override the seed from the test header.")
@click.option("-v", "verbose", count=True, help="Increase verbosity (-v, -vv, -vvv).")
@click.option("-q", "--quiet", is_flag=True, help="Quiet — one line per test.")
@click.option("--html-report", "html_report", type=click.Path(dir_okay=False, path_type=Path),
              default=None, help="Write a self-contained HTML report to PATH.")
def run(
    test_path: Path,
    game_override: Optional[Path],
    seed: Optional[str],
    verbose: int,
    quiet: bool,
    html_report: Optional[Path],
) -> None:
    """Run a single .test file."""
    console = _make_console()
    test = _load_test(test_path, game_override, console)
    if test is None:
        sys.exit(2)

    disp = display_mod.Display(verbosity=_verbosity(verbose, quiet), console=console)
    disp.start(test)
    result = runner.run_test(test, on_turn=disp.on_turn, seed_override=seed)
    disp.finish(result)
    if html_report:
        report_mod.write_report([result], html_report, title=f"ifPlayer · {test.header.test or test_path.name}")
        console.print(f"[dim]html report:[/dim] {html_report}")
    sys.exit(0 if result.status == "pass" else 1)


# ─── test (suite) ────────────────────────────────────────────────────


@main.command(name="test")
@click.argument("test_paths", type=click.Path(exists=True, dir_okay=False, path_type=Path), nargs=-1, required=True)
@click.option("--game", "game_override", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--seed", type=str, default=None)
@click.option("-v", "verbose", count=True)
@click.option("-q", "--quiet", is_flag=True)
@click.option("--stop-on-fail", is_flag=True, help="Halt on first failing test.")
@click.option("--html-report", "html_report", type=click.Path(dir_okay=False, path_type=Path),
              default=None, help="Write a self-contained HTML report to PATH.")
def test_cmd(
    test_paths: tuple[Path, ...],
    game_override: Optional[Path],
    seed: Optional[str],
    verbose: int,
    quiet: bool,
    stop_on_fail: bool,
    html_report: Optional[Path],
) -> None:
    """Run many .test files and print a suite summary."""
    console = _make_console()
    level = _verbosity(verbose, quiet)
    results: list[runner.TestResult] = []
    for path in test_paths:
        test = _load_test(path, game_override, console)
        if test is None:
            continue
        disp = display_mod.Display(verbosity=level, console=console)
        disp.start(test)
        result = runner.run_test(test, on_turn=disp.on_turn, seed_override=seed)
        disp.finish(result)
        results.append(result)
        if stop_on_fail and result.status == "fail":
            break
    display_mod.render_suite_summary(results, console=console)
    if html_report:
        report_mod.write_report(results, html_report)
        console.print(f"[dim]html report:[/dim] {html_report}")
    failed_any = any(r.status == "fail" for r in results)
    sys.exit(1 if failed_any else 0)


# ─── new ─────────────────────────────────────────────────────────────


@main.command()
@click.argument("name")
@click.option("--game", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Path to the I7 game file (.ulx or .gblorb).")
@click.option("--seed", default="42", show_default=True)
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Output path. Defaults to <name>.test in current directory.")
def new_cmd(name: str, game: Path, seed: str, output: Optional[Path]) -> None:
    """Stub a new .test file."""
    out_path = output or Path(f"{name}.test")
    if out_path.exists():
        click.echo(f"refusing to overwrite existing file: {out_path}", err=True)
        sys.exit(2)

    # Write game path relative to the .test file location when possible
    try:
        rel_game = Path(game).resolve().relative_to(out_path.resolve().parent)
        game_str = str(rel_game).replace("\\", "/")
    except ValueError:
        game_str = str(Path(game).resolve()).replace("\\", "/")

    stub = (
        f"test: {name}\n"
        f"game: {game_str}\n"
        f"seed: {seed}\n"
        f"---\n"
        f"\n"
        f"> look\n"
        f"\n"
        f"? \n"
    )
    out_path.write_text(stub, encoding="utf-8")
    click.echo(f"created {out_path}")


# ─── update ──────────────────────────────────────────────────────────


@main.command()
@click.argument("test_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--game", "game_override", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--seed", type=str, default=None)
def update(
    test_path: Path,
    game_override: Optional[Path],
    seed: Optional[str],
) -> None:
    """Re-run a test and rewrite it with captured output and metadata."""
    console = _make_console()
    test = _load_test(test_path, game_override, console)
    if test is None:
        sys.exit(2)

    result = runner.run_test(test, seed_override=seed)
    if result.outcome == "error":
        console.print(f"[red]update failed:[/red] {result.error}")
        sys.exit(2)

    # Rebuild test file from observed turns, preserving author assertions
    refreshed = test_format.TestFile(
        header=test.header,
        turns=[],
        path=test.path,
    )
    for original_turn, record in zip(test.turns, result.turns):
        room_marker = original_turn.room
        if record.state_after.room:
            # Update the room marker — keep the assertion flag the author chose
            preserve_assert = bool(room_marker and room_marker.is_assertion)
            preserve_force = bool(room_marker and room_marker.force_new)
            instance = (
                record.state_after.room.instance
                if record.state_after.room.instance > 1
                else None
            )
            room_marker = test_format.RoomMarker(
                name=record.state_after.room.name,
                instance=instance,
                is_assertion=preserve_assert,
                force_new=preserve_force,
            )

        score_marker = original_turn.score
        if record.state_after.score_max is not None:
            preserve_assert = bool(score_marker and score_marker.is_assertion)
            score_marker = test_format.ScoreMarker(
                score=record.state_after.score,
                score_max=record.state_after.score_max,
                is_assertion=preserve_assert,
            )

        refreshed.turns.append(test_format.Turn(
            command=original_turn.command,
            line_no=original_turn.line_no,
            room=room_marker,
            score=score_marker,
            turn_label=record.state_after.turn,
            recorded_output=record.observed_output.strip(),
            assertions=original_turn.assertions,
        ))

    new_text = test_format.emit(refreshed)
    test_path.write_text(new_text, encoding="utf-8")
    console.print(f"[green]updated[/green] {test_path}")


# ─── helpers ─────────────────────────────────────────────────────────


def _load_test(
    path: Path,
    game_override: Optional[Path],
    console: Console,
) -> Optional[test_format.TestFile]:
    try:
        test = test_format.parse_file(path)
    except test_format.TestFormatError as e:
        console.print(f"[red]parse error[/red] in {path}: {e}")
        return None
    except OSError as e:
        console.print(f"[red]read error[/red] {path}: {e}")
        return None
    if game_override is not None:
        test.header.game = str(game_override.resolve()).replace("\\", "/")
    return test


if __name__ == "__main__":
    main()
