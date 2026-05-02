"""Interactive REPL — `ifplayer play <game.ulx>`.

You type, the game responds. Slash-commands give you light controls
without leaving the session:

    /quit        exit
    /seed N      restart with a fresh seed
    /save FILE   write a captured transcript to FILE
    /room        show the current room identity (incl. #N)
    /score       show current score / turn count
    /help        list slash-commands

Captured transcripts can be promoted to a .test file by pasting them
into a new file and adding assertions.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from . import i7
from .interpreter import GlulxeInterpreter, InterpreterError, PromptTimeout


SLASH_HELP = """\
slash-commands:
  /quit         exit
  /seed N       restart with a fresh seed
  /save FILE    save transcript so far to FILE
  /room         show current room identity
  /score        show score / turn count
  /help         this help
"""


def play(
    game_path: Path,
    *,
    seed: Optional[str] = None,
    win_config: i7.WinConfig = i7.WinConfig(),
    console: Optional[Console] = None,
) -> int:
    """Run the interactive REPL. Returns an exit code."""
    if console is None:
        if sys.platform == "win32":
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass
        c = Console(legacy_windows=False)
    else:
        c = console

    interp = GlulxeInterpreter()
    state = i7.GameState()
    transcript: list[str] = []  # alternating "command" and "response" entries

    def restart(new_seed: Optional[str]) -> None:
        nonlocal state
        interp.close()
        interp.launch(game_path, seed=new_seed)
        banner = interp.read_until_prompt()
        transcript.append(banner)
        c.print(banner.rstrip(), highlight=False)
        state = i7.GameState()
        state.apply_turn(i7.analyze_turn(banner, win_config=win_config))
        state.turn = 0

    try:
        interp.launch(game_path, seed=seed)
    except InterpreterError as e:
        c.print(f"[red]error:[/red] {e}")
        return 2

    try:
        banner = interp.read_until_prompt()
    except (InterpreterError, PromptTimeout) as e:
        c.print(f"[red]error reading banner:[/red] {e}")
        interp.close()
        return 2
    transcript.append(banner)
    c.print(banner.rstrip(), highlight=False)
    state.apply_turn(i7.analyze_turn(banner, win_config=win_config))
    state.turn = 0

    while True:
        try:
            line = c.input("[cyan]>[/cyan] ")
        except (EOFError, KeyboardInterrupt):
            c.print()
            break

        line = line.strip()
        if not line:
            continue

        # Slash-commands
        if line.startswith("/"):
            handled = _handle_slash(line, c, state, transcript, restart)
            if handled is False:
                break
            continue

        transcript.append(f"> {line}")
        try:
            interp.send_command(line)
            response = interp.read_until_prompt()
        except PromptTimeout as e:
            c.print(f"[red]timeout:[/red] {e}")
            break
        except InterpreterError as e:
            c.print(f"[red]error:[/red] {e}")
            break

        transcript.append(response)
        c.print(response.rstrip(), highlight=False)

        analysis = i7.analyze_turn(response, win_config=win_config)
        state.apply_turn(analysis)

        if state.ended:
            outcome = "WIN" if state.won else ("LOSE" if state.lost else "END")
            c.print(f"[bold]{outcome}[/bold]")
            break

    interp.close()
    return 0


def _handle_slash(
    line: str,
    c: Console,
    state: i7.GameState,
    transcript: list[str],
    restart,
) -> Optional[bool]:
    """Returns False to exit the REPL, anything else to continue."""
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/q", "/quit", "/exit"):
        return False

    if cmd == "/help":
        c.print(SLASH_HELP)
        return True

    if cmd == "/seed":
        new_seed = arg.strip() or None
        try:
            restart(new_seed)
        except InterpreterError as e:
            c.print(f"[red]restart failed:[/red] {e}")
        return True

    if cmd == "/save":
        if not arg:
            c.print("[yellow]usage:[/yellow] /save <filename>")
            return True
        path = Path(arg).expanduser()
        path.write_text("\n".join(transcript) + "\n", encoding="utf-8")
        c.print(f"[green]saved[/green] {path}")
        return True

    if cmd == "/room":
        if state.room:
            c.print(f"@ {state.room.label}  [dim]fp={state.room.fingerprint}[/dim]")
        else:
            c.print("(no room detected)")
        return True

    if cmd == "/score":
        score = f"{state.score}/{state.score_max}" if state.score_max else f"{state.score}"
        c.print(f"$ {score}  T:{state.turn}")
        return True

    c.print(f"[yellow]unknown slash-command:[/yellow] {cmd}  (try /help)")
    return True
