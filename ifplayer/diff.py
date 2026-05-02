"""Word-level diff for soft-drift display.

Returns a list of DiffChunk segments classifying each span of words as
'equal', 'insert' (in observed but not recorded), or 'delete' (in
recorded but not observed). Whitespace-only differences (line wrapping,
trailing spaces) produce an empty list — i.e. "no drift" — because the
runner compares word sequences, not raw byte strings.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Literal


DiffKind = Literal["equal", "insert", "delete"]


@dataclass
class DiffChunk:
    kind: DiffKind
    text: str


_WORD_RE = re.compile(r"\S+")


def word_diff(recorded: str, observed: str) -> list[DiffChunk]:
    """Return diff chunks comparing recorded → observed at word granularity.

    Empty list = identical word sequences (whitespace may differ).
    Otherwise: a sequence of equal/delete/insert segments suitable for
    coloured rendering.
    """
    a_words = _WORD_RE.findall(recorded)
    b_words = _WORD_RE.findall(observed)
    if a_words == b_words:
        return []

    sm = difflib.SequenceMatcher(None, a_words, b_words, autojunk=False)
    chunks: list[DiffChunk] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            chunks.append(DiffChunk("equal", " ".join(a_words[i1:i2])))
        elif tag == "delete":
            chunks.append(DiffChunk("delete", " ".join(a_words[i1:i2])))
        elif tag == "insert":
            chunks.append(DiffChunk("insert", " ".join(b_words[j1:j2])))
        elif tag == "replace":
            chunks.append(DiffChunk("delete", " ".join(a_words[i1:i2])))
            chunks.append(DiffChunk("insert", " ".join(b_words[j1:j2])))
    return chunks


def has_drift(chunks: list[DiffChunk]) -> bool:
    """True if any non-equal chunk is present."""
    return any(c.kind != "equal" for c in chunks)
