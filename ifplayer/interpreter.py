"""Glulxe subprocess wrapper with Windows-safe threaded reader.

Drives `glulxe.exe -q` (cheap/dumb mode) over stdin/stdout. The threaded
byte-by-byte reader is portable verbatim from i7/tools/run_tests.py, which
itself was distilled from the Windows-forked RegTest v1.13 vendored at
ifhub/tools/regtest.py. On Windows, select() doesn't work on pipes, so a
daemon thread reads one byte at a time until the configured prompt marker
appears at the buffer tail.

This module knows nothing about I7 specifics (status lines, win patterns,
room fingerprinting). All that lives in i7.py.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional


DEFAULT_PROMPT = b"\n>"
DEFAULT_TIMEOUT_SECS = 10.0


class InterpreterError(RuntimeError):
    """Raised for any launch or I/O failure."""


class PromptTimeout(InterpreterError):
    """Raised when the interpreter doesn't reach the prompt in time."""


def find_glulxe() -> Optional[Path]:
    """Locate glulxe.exe — bundled copy first, then PATH."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        for name in ("glulxe.exe", "glulxe"):
            candidate = ancestor / "i7" / "tools" / "interpreters" / name
            if candidate.is_file():
                return candidate
    on_path = shutil.which("glulxe")
    return Path(on_path) if on_path else None


class GlulxeInterpreter:
    """Subprocess driver for `glulxe -q <game>`.

    Lifecycle: launch() -> repeated send_command() / read_until_prompt()
    -> close(). Use as a context manager to guarantee cleanup.
    """

    def __init__(
        self,
        *,
        interpreter: Optional[Path] = None,
        prompt: bytes = DEFAULT_PROMPT,
        seed_flag: str = "--rngseed",
    ) -> None:
        self._interpreter = interpreter or find_glulxe()
        self._prompt = prompt
        self._seed_flag = seed_flag
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._game_path: Optional[Path] = None

    def launch(self, game_path: Path, *, seed: Optional[str] = None) -> None:
        if self._proc is not None:
            raise InterpreterError("interpreter already launched")
        if self._interpreter is None:
            raise InterpreterError(
                "glulxe interpreter not found. Place glulxe.exe at "
                "<repo>/i7/tools/interpreters/ or install it on PATH."
            )
        if not game_path.is_file():
            raise InterpreterError(f"game file not found: {game_path}")

        cmd = [str(self._interpreter), "-q"]
        if seed:
            cmd.extend([self._seed_flag, str(seed)])
        cmd.append(str(game_path))

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as e:
            raise InterpreterError(
                f"failed to launch glulxe: {e} (cmd: {' '.join(cmd)})"
            ) from e
        self._game_path = game_path

    def send_command(self, command: str) -> None:
        proc = self._require_proc()
        try:
            proc.stdin.write((command + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, BrokenPipeError) as e:
            raise InterpreterError(f"failed to send command: {e}") from e

    def read_until_prompt(self, timeout: float = DEFAULT_TIMEOUT_SECS) -> str:
        """Read stdout until the prompt or `timeout` elapses.

        Returns text with the trailing prompt marker stripped. Strips '\\r'
        so native Windows interpreters that emit CRLF look the same as Unix.
        Raises PromptTimeout on timeout, InterpreterError on read failure.
        """
        proc = self._require_proc()
        output = bytearray()
        deadline = time.time() + timeout
        read_error: list[Optional[BaseException]] = [None]
        prompt = self._prompt
        prompt_len = len(prompt)

        def _reader() -> None:
            try:
                while True:
                    ch = proc.stdout.read(1)
                    if ch == b"":
                        break
                    if ch == b"\r":
                        continue
                    output.extend(ch)
                    if output[-prompt_len:] == prompt:
                        break
            except BaseException as e:  # noqa: BLE001
                read_error[0] = e

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        remaining = max(deadline - time.time(), 0.05)
        t.join(timeout=remaining)

        if read_error[0] is not None:
            raise InterpreterError(f"read failed: {read_error[0]}") from read_error[0]
        if t.is_alive():
            raise PromptTimeout(f"no prompt within {timeout:.1f}s")

        text = output.decode("utf-8", errors="replace")
        prompt_str = prompt.decode("utf-8", errors="replace")
        if text.endswith(prompt_str):
            text = text[: -len(prompt_str)]
        return text

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None

    def __enter__(self) -> "GlulxeInterpreter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def returncode(self) -> Optional[int]:
        return self._proc.returncode if self._proc else None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def interpreter_path(self) -> Optional[Path]:
        return self._interpreter

    @property
    def game_path(self) -> Optional[Path]:
        return self._game_path

    def _require_proc(self) -> subprocess.Popen[bytes]:
        if self._proc is None:
            raise InterpreterError("interpreter not launched")
        return self._proc
