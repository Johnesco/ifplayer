"""Parser + emitter for the ifPlayer .test format.

The format interlaces input commands with recorded game output and
per-turn metadata. See the plan for the full spec; the short version:

    test: name
    game: path/to/game.ulx
    seed: 42
    ---

    > command
    @ Room Label                   # or @? Room   for assertion
    $ N/M  T:K                     # or $? for assertion
                                   # blank line
      indented recorded output
      (any number of lines)
                                   # blank line
    ? assertion text               # substring (default)
    ? /regex/                      # regex
    ?! negative assertion          # must NOT be present

Header is YAML-ish (simple `key: value`). Body turns start with `>`.
Within a turn, lines are classified by leading sigil:
  '>'              command (starts a new turn)
  '@', '@?'        room marker (label / assertion)
  '$', '$?'        score marker
  'T:'             turn-count label
  '? ', '?! '      assertion lines
  indented (2+ spaces or tab)  recorded output
  '#'              comment (ignored)
  '←', '(+N)', 'WIN' etc.       display-only — silently consumed on parse
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


# ─── Data model ──────────────────────────────────────────────────────


AssertKind = Literal["contains", "regex", "not_contains"]


@dataclass
class Assertion:
    kind: AssertKind
    text: str  # substring for contains/not_contains; pattern source for regex
    raw_line: str  # exact line as written, for error display
    line_no: int


@dataclass
class RoomMarker:
    name: str
    instance: Optional[int] = None  # the #N suffix, if author wrote one
    is_assertion: bool = False  # True for @?, False for @
    force_new: bool = False  # ~force-new modifier


@dataclass
class ScoreMarker:
    score: int
    score_max: int
    is_assertion: bool = False  # True for $?, False for $


@dataclass
class Turn:
    command: str
    line_no: int
    room: Optional[RoomMarker] = None
    score: Optional[ScoreMarker] = None
    turn_label: Optional[int] = None  # T:N
    recorded_output: str = ""  # captured text for soft-diff
    assertions: list[Assertion] = field(default_factory=list)


@dataclass
class Header:
    test: str = ""
    game: Optional[str] = None  # path relative to .test file
    seed: Optional[str] = None  # kept as string to preserve hex/etc.
    before: list[str] = field(default_factory=list)  # paths to .before files
    extras: dict[str, str] = field(default_factory=dict)


@dataclass
class BeforeFile:
    """A `.before` file — setup commands only, no assertions allowed.

    By design `.before` files cannot contain `?`/`@?`/`$?`/`?!` lines so
    they cannot be misused as tests. They get the player into a state;
    they do not verify it. The parent .test asserts whatever invariants
    matter on its first body turn.
    """
    commands: list[str]
    path: Optional[Path] = None
    raw_text: str = ""


@dataclass
class TestFile:
    header: Header
    turns: list[Turn]
    path: Optional[Path] = None
    raw_text: str = ""


class TestFormatError(ValueError):
    """Raised on malformed .test files. Includes line number when known."""

    def __init__(self, message: str, line_no: Optional[int] = None) -> None:
        if line_no is not None:
            super().__init__(f"line {line_no}: {message}")
        else:
            super().__init__(message)
        self.line_no = line_no


# ─── Patterns ────────────────────────────────────────────────────────

_HEADER_KV_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")
_HEADER_SEP_RE = re.compile(r"^---+\s*$")

_COMMAND_RE = re.compile(r"^>\s?(.*)$")

# @ Foyer Bar              → label
# @? Foyer Bar             → assertion
# @ Forest #2              → label with disambiguator
# @? Forest #2             → assertion with disambiguator
# @ Foyer Bar ~force-new   → label, force new identity
_ROOM_RE = re.compile(
    r"^@(\?)?\s+"
    r"(?P<name>[^#~]+?)"
    r"(?:\s+#(?P<inst>\d+))?"
    r"(?:\s+~force-new)?"
    r"\s*$"
)

# $ 0/2     → label
# $? 0/2    → assertion
_SCORE_RE = re.compile(r"^\$(\?)?\s+(?P<got>-?\d+)\s*/\s*(?P<max>\d+)\s*$")

# T:4
_TURN_RE = re.compile(r"^T:\s*(\d+)\s*$")

# ?! text     → negative assertion
# ? /regex/   → regex assertion
# ? text      → substring assertion
_NEG_ASSERT_RE = re.compile(r"^\?!\s*(.+?)\s*$")
_REGEX_ASSERT_RE = re.compile(r"^\?\s*/(?P<pat>.+)/\s*$")
_POS_ASSERT_RE = re.compile(r"^\?\s*(.+?)\s*$")

# Display-only lines we accept and ignore on parse
_DISPLAY_ONLY_RE = re.compile(
    r"^(?:←\s+.+|\(\s*[+\-]\d+\s*\)|WIN|LOSE|END)$"
)


# ─── Parser ──────────────────────────────────────────────────────────


def parse(text: str, path: Optional[Path] = None) -> TestFile:
    """Parse raw test-file text into a TestFile."""
    lines = text.splitlines()
    header, body_start = _parse_header(lines)
    turns = _parse_turns(lines, body_start)
    return TestFile(header=header, turns=turns, path=path, raw_text=text)


def parse_file(path: Path) -> TestFile:
    return parse(path.read_text(encoding="utf-8"), path=path)


def parse_before(text: str, path: Optional[Path] = None) -> BeforeFile:
    """Parse a .before file. Only `>` commands and `#` comments allowed.

    Any line that looks like an assertion or metadata marker raises
    TestFormatError — `.before` files are physically incapable of being
    tests, by design.
    """
    commands: list[str] = []
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(">"):
            m = _COMMAND_RE.match(line)
            if m:
                commands.append(m.group(1).strip())
                continue
        # Reject any other line type (assertions, room/score markers, etc.)
        raise TestFormatError(
            f".before files may only contain `>` commands and `#` "
            f"comments; got: {line.rstrip()!r}",
            line_no=i + 1,
        )
    return BeforeFile(commands=commands, path=path, raw_text=text)


def parse_before_file(path: Path) -> BeforeFile:
    return parse_before(path.read_text(encoding="utf-8"), path=path)


def _parse_header(lines: list[str]) -> tuple[Header, int]:
    """Pull `key: value` lines until `---` separator. Returns header + index of first body line."""
    header = Header()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if _HEADER_SEP_RE.match(stripped):
            i += 1
            return header, i
        m = _HEADER_KV_RE.match(line)
        if not m:
            raise TestFormatError(
                f"expected `key: value` in header, got: {line!r}", line_no=i + 1
            )
        key, value = m.group(1).lower(), m.group(2)
        if key == "test":
            header.test = value
        elif key == "game":
            header.game = value
        elif key == "seed":
            header.seed = value
        elif key == "before":
            # `before:` is repeatable — each line names one .before file
            if value:
                header.before.append(value)
        else:
            header.extras[key] = value
        i += 1
    # No --- found: header-only files are valid (a stub)
    return header, i


def _parse_turns(lines: list[str], start: int) -> list[Turn]:
    turns: list[Turn] = []
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip blank lines and comments between turns
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        cmd_match = _COMMAND_RE.match(line)
        if not cmd_match:
            raise TestFormatError(
                f"expected `> command` to start a turn, got: {line!r}",
                line_no=i + 1,
            )

        turn = Turn(command=cmd_match.group(1).strip(), line_no=i + 1)
        i += 1
        i = _parse_turn_body(lines, i, turn)
        turns.append(turn)
    return turns


def _parse_turn_body(lines: list[str], start: int, turn: Turn) -> int:
    """Consume lines belonging to a single turn. Returns index of next-turn start."""
    i = start
    output_buffer: list[str] = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Next turn starts here
        if line.startswith(">"):
            break

        # Comment
        if stripped.startswith("#"):
            i += 1
            continue

        # Blank line — could be inside output block, or just spacing
        if not stripped:
            output_buffer.append("")
            i += 1
            continue

        # Recorded output: 2+ spaces or tab indent
        if line.startswith("  ") or line.startswith("\t"):
            output_buffer.append(line.rstrip())
            i += 1
            continue

        # @  room marker
        room_match = _ROOM_RE.match(stripped)
        if stripped.startswith("@") and room_match:
            if turn.room is not None:
                raise TestFormatError(
                    "duplicate @ marker in turn", line_no=i + 1
                )
            turn.room = RoomMarker(
                name=room_match.group("name").strip(),
                instance=int(room_match.group("inst")) if room_match.group("inst") else None,
                is_assertion=room_match.group(1) == "?",
                force_new="~force-new" in line,
            )
            i += 1
            continue

        # $  score marker
        score_match = _SCORE_RE.match(stripped)
        if stripped.startswith("$") and score_match:
            if turn.score is not None:
                raise TestFormatError(
                    "duplicate $ marker in turn", line_no=i + 1
                )
            turn.score = ScoreMarker(
                score=int(score_match.group("got")),
                score_max=int(score_match.group("max")),
                is_assertion=score_match.group(1) == "?",
            )
            i += 1
            continue

        # T: turn label (we may also see `$ 0/2  T:2` on one line — handled below)
        turn_match = _TURN_RE.match(stripped)
        if turn_match:
            turn.turn_label = int(turn_match.group(1))
            i += 1
            continue

        # Combined `$ 0/2  T:2` on one line — split heuristically
        if stripped.startswith("$"):
            parts = stripped.split()
            # Try to find a T:N token among the parts
            t_idx = next(
                (j for j, p in enumerate(parts) if _TURN_RE.match(p.strip())),
                None,
            )
            if t_idx is not None:
                # Re-parse the score portion (everything before the T: token)
                score_part = " ".join(parts[:t_idx])
                m2 = _SCORE_RE.match(score_part)
                if m2:
                    if turn.score is not None:
                        raise TestFormatError(
                            "duplicate $ marker in turn", line_no=i + 1
                        )
                    turn.score = ScoreMarker(
                        score=int(m2.group("got")),
                        score_max=int(m2.group("max")),
                        is_assertion=m2.group(1) == "?",
                    )
                    t_match = _TURN_RE.match(parts[t_idx])
                    if t_match:
                        turn.turn_label = int(t_match.group(1))
                    i += 1
                    continue

        # Negative assertion (must come before _POS_ASSERT — `?!` starts with `?`)
        m = _NEG_ASSERT_RE.match(stripped)
        if m:
            turn.assertions.append(
                Assertion(kind="not_contains", text=m.group(1),
                          raw_line=line, line_no=i + 1)
            )
            i += 1
            continue

        # Regex assertion (must come before substring — `? /...//` starts with `? `)
        m = _REGEX_ASSERT_RE.match(stripped)
        if m:
            turn.assertions.append(
                Assertion(kind="regex", text=m.group("pat"),
                          raw_line=line, line_no=i + 1)
            )
            i += 1
            continue

        # Positive substring assertion
        m = _POS_ASSERT_RE.match(stripped)
        if m:
            turn.assertions.append(
                Assertion(kind="contains", text=m.group(1),
                          raw_line=line, line_no=i + 1)
            )
            i += 1
            continue

        # Display-only annotations — accept and skip
        if _DISPLAY_ONLY_RE.match(stripped):
            i += 1
            continue

        raise TestFormatError(
            f"unrecognized line in turn body: {line!r}", line_no=i + 1
        )

    # Trim leading/trailing blank lines from output buffer; keep interior structure
    while output_buffer and not output_buffer[0].strip():
        output_buffer.pop(0)
    while output_buffer and not output_buffer[-1].strip():
        output_buffer.pop()
    if output_buffer:
        # Strip the common 2-space indent from each non-blank line
        dedented = []
        for ln in output_buffer:
            if ln.startswith("  "):
                dedented.append(ln[2:])
            elif ln.startswith("\t"):
                dedented.append(ln[1:])
            else:
                dedented.append(ln)
        turn.recorded_output = "\n".join(dedented)
    return i


# ─── Emitter ─────────────────────────────────────────────────────────


def emit(test: TestFile) -> str:
    """Render a TestFile back to canonical .test text.

    Used by `ifplayer update` and `ifplayer new`. Emits a deterministic
    formatting so re-running update on an unchanged test is a no-op diff.
    """
    out: list[str] = []

    # Header
    if test.header.test:
        out.append(f"test: {test.header.test}")
    if test.header.game:
        out.append(f"game: {test.header.game}")
    if test.header.seed is not None:
        out.append(f"seed: {test.header.seed}")
    for path in test.header.before:
        out.append(f"before: {path}")
    for k, v in test.header.extras.items():
        out.append(f"{k}: {v}")
    out.append("---")
    out.append("")

    # Turns
    for turn in test.turns:
        out.append(f"> {turn.command}")
        if turn.room is not None:
            out.append(_render_room(turn.room))
        if turn.score is not None or turn.turn_label is not None:
            out.append(_render_score_turn(turn.score, turn.turn_label))
        if turn.recorded_output:
            out.append("")
            for ln in turn.recorded_output.splitlines():
                out.append(f"  {ln}" if ln else "")
            out.append("")
        for a in turn.assertions:
            out.append(_render_assertion(a))
        out.append("")  # blank between turns

    return "\n".join(out).rstrip() + "\n"


def _render_room(r: RoomMarker) -> str:
    sigil = "@?" if r.is_assertion else "@"
    parts = [sigil, r.name]
    if r.instance is not None and r.instance > 1:
        parts.append(f"#{r.instance}")
    line = " ".join(parts)
    if r.force_new:
        line += " ~force-new"
    return line


def _render_score_turn(s: Optional[ScoreMarker], t: Optional[int]) -> str:
    bits: list[str] = []
    if s is not None:
        sigil = "$?" if s.is_assertion else "$"
        bits.append(f"{sigil} {s.score}/{s.score_max}")
    if t is not None:
        bits.append(f"T:{t}")
    return "  ".join(bits)


def _render_assertion(a: Assertion) -> str:
    if a.kind == "not_contains":
        return f"?! {a.text}"
    if a.kind == "regex":
        return f"? /{a.text}/"
    return f"? {a.text}"
