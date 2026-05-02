"""I7-specific intelligence: parse game output, track evolving state,
fingerprint rooms, classify outcomes.

Glulxe runs in cheap/dumb mode (`-q`) which suppresses the status grid
window, so all structural info comes from inline text:

  * Room change — first non-blank line of a turn's response, when it
    matches a "looks like a room title" heuristic (Title Case, no
    sentence-ending punctuation, followed by description text)
  * Score change — `[Your score has just gone up by N point(s).]`
  * End game — `*** The End ***`, `*** You have won ***`,
    `*** You have died ***`, etc. (configurable per project)
  * Final score line — `you scored N out of a possible M, in K turns`
  * Parser errors — `I only understood`, `That's not a verb I recognise`, etc.

For room disambiguation, a "fingerprint" is the SHA-1 of (name + description
text). Different fingerprint with same name → different physical room → next
free `#N` in visit order.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional


# ─── Patterns ────────────────────────────────────────────────────────

# A "room title" line: one or two short Title Case lines, no sentence
# punctuation, possibly followed by a description paragraph. We look at
# the FIRST non-blank line of a response; if it matches this, it's
# probably a room name.
ROOM_TITLE_RE = re.compile(
    r"^[A-Z][A-Za-z0-9][A-Za-z0-9 ',\-]{0,60}$"
)

# Inline score change announcements (I7 default phrasing).
# I7 prints English cardinals for small values ("by one point") and
# digits for larger ones, so accept either form.
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUM_PATTERN = r"(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)"

SCORE_UP_RE = re.compile(
    rf"\[Your score has (?:just )?gone up(?: by {_NUM_PATTERN} points?)?\.\]",
    re.IGNORECASE,
)
SCORE_DOWN_RE = re.compile(
    rf"\[Your score has (?:just )?gone down(?: by {_NUM_PATTERN} points?)?\.\]",
    re.IGNORECASE,
)


def _parse_num(s: Optional[str]) -> int:
    if s is None:
        return 1  # I7 sometimes omits the magnitude entirely
    if s.isdigit():
        return int(s)
    return _NUM_WORDS.get(s.lower(), 1)

# End-of-game banner — *** something ***
END_BANNER_RE = re.compile(r"\*\*\*\s+([^*]+?)\s+\*\*\*")

# Default win/lose phrases. Override per-project via WinConfig.
DEFAULT_WIN_PATTERNS = [
    r"\*\*\* You have won \*\*\*",
    r"\*\*\* The End \*\*\*",
    r"\*\*\* You have succeeded \*\*\*",
]
DEFAULT_LOSE_PATTERNS = [
    r"\*\*\* You have died \*\*\*",
    r"\*\*\* You have lost \*\*\*",
    r"\*\*\* Game Over \*\*\*",
]

# Final score line
FINAL_SCORE_RE = re.compile(
    r"you scored (\d+) out of a possible (\d+),?\s*in (\d+) turns?",
    re.IGNORECASE,
)

# Common parser-error phrases (I7 default messages)
PARSER_ERROR_PATTERNS = [
    r"I only understood you as far as",
    r"That's not a verb I recognise",
    r"You can't see any such thing",
    r"You can't go that way",
    r"That noun did not make sense in this context",
    r"I didn't understand that sentence",
]
PARSER_ERROR_RE = re.compile("|".join(PARSER_ERROR_PATTERNS), re.IGNORECASE)

# End-game restart prompt — appears after *** *** banner
RESTART_PROMPT_RE = re.compile(
    r"Would you like to RESTART, RESTORE",
    re.IGNORECASE,
)


# ─── Config ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WinConfig:
    """How to detect win/lose for a particular game.

    Defaults work for most stock Inform 7 stories. Override per project
    when a game uses non-standard end banners.
    """
    win_patterns: tuple[str, ...] = tuple(DEFAULT_WIN_PATTERNS)
    lose_patterns: tuple[str, ...] = tuple(DEFAULT_LOSE_PATTERNS)


# ─── Room identity ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RoomIdentity:
    """A unique physical room: printed name + description fingerprint.

    Two visits to rooms with the same printed name but different
    descriptions get different identities. Two visits to the same
    physical room get the same identity.
    """
    name: str
    fingerprint: str
    instance: int  # 1-based visit-order index per name

    @property
    def label(self) -> str:
        return self.name if self.instance == 1 else f"{self.name} #{self.instance}"


_FIRST_SENTENCE_RE = re.compile(r"(.+?)(?:[.!?](?:\s|$))", re.DOTALL)


def _fingerprint(name: str, description: str) -> str:
    """SHA-1 hash of normalized name + first sentence of description.

    Using only the first sentence stabilises identity across visits
    where the room's full description embeds dynamic state. Zork's
    Living Room, for example, lists the trophy case + the trap door's
    state ("open"/"closed") in later clauses, so hashing the whole
    paragraph creates a new fingerprint on every state change. Most
    *distinct* rooms have distinct opening sentences, so this keeps
    disambiguation power for the common case.

    Limitation: rooms with identical opening sentences (mazes — "This
    is part of a maze of twisty little passages, all alike.") still
    collapse. That's a fundamental cheap-glk limitation; the runner
    surfaces it as a single fingerprint with many visits, and authors
    can use `~force-new` in a `.test` to override.
    """
    norm_name = name.strip().lower()
    norm_desc = " ".join(description.split())
    m = _FIRST_SENTENCE_RE.match(norm_desc)
    first_sentence = (m.group(1) if m else norm_desc).lower().strip()
    norm = norm_name + "\n" + first_sentence
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


class RoomRegistry:
    """Tracks every distinct room observed and assigns stable #N labels.

    Same name + same fingerprint  → same identity (revisit).
    Same name + different fingerprint → new instance, next #N.
    Different name → independent counter.
    """

    def __init__(self) -> None:
        self._by_fingerprint: dict[str, RoomIdentity] = {}
        self._counts_by_name: dict[str, int] = {}

    def resolve(
        self,
        name: str,
        description: str,
        *,
        force_new: bool = False,
    ) -> RoomIdentity:
        fp = _fingerprint(name, description)
        if not force_new and fp in self._by_fingerprint:
            return self._by_fingerprint[fp]

        instance = self._counts_by_name.get(name, 0) + 1
        self._counts_by_name[name] = instance
        identity = RoomIdentity(name=name, fingerprint=fp, instance=instance)
        # Only record by fingerprint if not forced — forced-new rooms
        # are intentionally indistinguishable so we don't want future
        # matches to collapse into the forced one.
        if not force_new:
            self._by_fingerprint[fp] = identity
        return identity


# ─── Per-turn parsing ────────────────────────────────────────────────


@dataclass
class TurnAnalysis:
    """Structural info extracted from one turn's response text."""
    room_name: Optional[str] = None
    room_description: Optional[str] = None  # for fingerprinting
    score_delta: int = 0
    end_banner: Optional[str] = None  # text inside *** ... ***
    won: bool = False
    lost: bool = False
    final_score: Optional[tuple[int, int, int]] = None  # (got, max, turns)
    parser_errors: list[str] = field(default_factory=list)


def analyze_turn(
    response: str,
    *,
    win_config: WinConfig = WinConfig(),
) -> TurnAnalysis:
    """Extract structural facts from one turn's response text."""
    a = TurnAnalysis()

    # Room name: scan early lines for one that looks like a room title
    # (Title Case, no terminal punctuation). Skip leading narrative —
    # I7 prints things like "The trap door crashes shut." or "(first
    # taking it off)" BEFORE the new room title when location changes.
    # We skip:
    #   - blank lines
    #   - lines starting with `(` or `[` (parenthetical/bracketed narrative)
    #   - lines ending in sentence punctuation `.!?`
    #   - lines that don't match the room-title shape
    lines = response.splitlines()
    title_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if not stripped:
            continue
        if stripped[0] in "([":
            continue  # parenthetical or bracketed narrative
        if stripped[-1] in ".!?":
            continue  # full sentence — narrative, not a title
        if ROOM_TITLE_RE.match(stripped):
            title_idx = i
            break

    if title_idx is not None:
        a.room_name = lines[title_idx].strip()
        # Description = subsequent non-blank lines until a blank gap
        desc_lines: list[str] = []
        for ln in lines[title_idx + 1:]:
            if not ln.strip():
                if desc_lines:
                    break
                continue
            desc_lines.append(ln.strip())
        a.room_description = " ".join(desc_lines) if desc_lines else ""

    # Score deltas (I7 phrasing: "[Your score has just gone up by one point.]")
    for m in SCORE_UP_RE.finditer(response):
        a.score_delta += _parse_num(m.group("n"))
    for m in SCORE_DOWN_RE.finditer(response):
        a.score_delta -= _parse_num(m.group("n"))

    # End banner: capture the *** text *** content for display
    banner_match = END_BANNER_RE.search(response)
    if banner_match:
        a.end_banner = banner_match.group(1).strip()

    # Win / lose detection
    for pat in win_config.win_patterns:
        if re.search(pat, response, re.IGNORECASE):
            a.won = True
            break
    for pat in win_config.lose_patterns:
        if re.search(pat, response, re.IGNORECASE):
            a.lost = True
            break

    # Final score line. The game only prints this when the run is over
    # (win, lose, or quit). If the player scored max, treat as a win even
    # if no `*** You have won ***` banner appeared — many games (e.g.
    # Zork I) end with a tribute scene rather than a stock banner.
    fs = FINAL_SCORE_RE.search(response)
    if fs:
        got, mx, turns = int(fs.group(1)), int(fs.group(2)), int(fs.group(3))
        a.final_score = (got, mx, turns)
        if got >= mx and mx > 0 and not a.lost:
            a.won = True

    # Parser errors
    a.parser_errors = [m.group(0) for m in PARSER_ERROR_RE.finditer(response)]

    return a


# ─── Game state evolution ────────────────────────────────────────────


@dataclass
class GameState:
    """Evolving snapshot maintained across turns."""
    room: Optional[RoomIdentity] = None
    previous_room: Optional[RoomIdentity] = None
    score: int = 0
    score_max: Optional[int] = None
    turn: int = 0  # incremented per command sent
    ended: bool = False
    won: bool = False
    lost: bool = False
    rooms: RoomRegistry = field(default_factory=RoomRegistry)

    def apply_turn(
        self,
        analysis: TurnAnalysis,
        *,
        force_new_room: bool = False,
    ) -> None:
        self.turn += 1

        if analysis.room_name is not None:
            self.previous_room = self.room
            self.room = self.rooms.resolve(
                analysis.room_name,
                analysis.room_description or "",
                force_new=force_new_room,
            )
        # else: room title not reprinted → still in same room

        self.score += analysis.score_delta
        if analysis.final_score:
            got, mx, _turns = analysis.final_score
            self.score = got
            self.score_max = mx

        if analysis.won:
            self.won = True
            self.ended = True
        if analysis.lost:
            self.lost = True
            self.ended = True


# ─── Debug-mode probe (for L4 enrichment, optional) ─────────────────


SHOWME_YOURSELF_FINGERPRINT_RE = re.compile(
    r"\bSHOWME (yourself|self)\b|\byou\b.*\(\d+\)|\(yourself\)",
    re.IGNORECASE,
)


def looks_like_debug_build(showme_response: str) -> bool:
    """Heuristic: did `SHOWME yourself` produce debug-style output?

    Inform 7 with `Use DEBUG.` exposes SHOWME, RULES, ACTIONS, TREE.
    Without DEBUG, those verbs report 'I only understood you as far as'
    or similar parser errors.
    """
    if PARSER_ERROR_RE.search(showme_response):
        return False
    # Real SHOWME output has structure: location, contents, with parens
    return "yourself" in showme_response.lower() or "(" in showme_response
