"""Lightweight language detection for Arabic/English customer messages
(Phase B: Arabic support — see ANALYSIS.md's Phase B entry).

Unicode-range detection only — no LLM call, no external dependency — so
it's free to run on every turn, same reasoning as agent/smalltalk.py's
pre-Groq detection.
"""

from __future__ import annotations

import re

# Arabic, Arabic Supplement, Arabic Extended-A, Arabic Presentation Forms A/B.
_ARABIC_RE = re.compile(
    "[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]"
)


def detect_language(text: str) -> str:
    """Returns "ar" if `text` contains any Arabic-script character, "en"
    otherwise. Presence, not majority, decides it — a mostly-Latin message
    with one Arabic word (or vice versa) still means the customer is
    writing in Arabic."""
    return "ar" if _ARABIC_RE.search(text) else "en"
