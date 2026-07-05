"""
Tiny shared text-normalization helper.

Both ``core.matching`` (parameter <-> phrase matching) and
``core.pdf_extractor`` (series/model/unit-configuration query scoping) need
the same "lowercase, strip punctuation/unit noise, collapse whitespace"
normalization. It lives here, dependency-free, specifically so that
importing it never creates a cycle between those two modules.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation/units noise, collapse whitespace."""
    text = text.lower()
    tokens = _WORD_RE.findall(text)
    return " ".join(tokens)
