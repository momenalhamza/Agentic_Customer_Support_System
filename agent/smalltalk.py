"""Cheap, pre-Groq detection of pure greetings/small-talk/disengagement
(M9 fix — ARCHITECTURE.md's §8 routing flow never accounted for
conversational input with no real intent at all; a bare "hi" was reaching
intent classification and retrieval, both of which correctly find nothing
relevant, and then incorrectly escalating).

Matches only when the ENTIRE message (after stripping trailing punctuation
and surrounding whitespace) is one of a small, curated set of known
phrases — never a substring match. "hi, can you cancel my order?" is a
real request that happens to start with a greeting and must fall through
to real routing, not be swallowed here.

Deliberately excludes ambiguous single-word acknowledgments ("ok", "yes",
"no") — these are common, legitimate replies mid-conversation (e.g. to a
clarifying question) and treating them as small talk would incorrectly
short-circuit a real in-progress exchange.

Zero Groq calls, zero retrieval calls, zero latency cost beyond a regex —
this is checked before ANY per-turn work happens, so it's essentially
free even at high message volume.

Priority against an active pending state (M9 follow-up fix, found via live
testing): "disengagement" phrases ("nevermind") always win, since their
meaning is unambiguous regardless of context. "greeting"/"thanks"/"farewell"
are social filler that could double as a (nonsensical) reply to an active
clarification/slot/contact question — e.g. sending "hi" as an answer to
"could you tell me more about what you're trying to do?" should be treated
as the customer's (still unclear) reply, not silently reset the
conversation to a bare greeting. See agent.py's handle_message() for where
this split is applied.
"""

from __future__ import annotations

import re
from typing import Optional

from agent.language import detect_language

# Arabic trailing punctuation ("؟" question mark, "،" comma) alongside the
# Latin set, so an Arabic phrase's trailing punctuation strips the same way.
_TRAILING_PUNCT_RE = re.compile(r"[!?.,;:؟،]+$")

_GREETINGS = {
    "hi", "hello", "hey", "hiya", "yo", "howdy",
    "hi there", "hello there", "hey there",
    "good morning", "good afternoon", "good evening",
}
_THANKS = {
    "thanks", "thank you", "thanks a lot", "thank you so much",
    "many thanks", "appreciate it", "thanks so much", "great thanks",
    "perfect thanks", "ok thanks", "okay thanks", "ok thank you", "okay thank you",
}
_FAREWELLS = {
    "bye", "goodbye", "see you", "see ya", "take care", "later",
    "talk later", "have a good one", "have a good day",
}
_DISENGAGEMENTS = {
    "nevermind", "never mind", "forget it", "never mind then",
    "it's fine", "its fine", "don't worry about it", "dont worry about it",
    "that's all", "thats all", "that's all thanks", "no that's all",
}

# Phase B (Arabic support, see ANALYSIS.md): a curated set of common Arabic
# phrases, same spirit as the English sets above — not exhaustive, just the
# common cases, since this is a cheap pre-Groq check, not a classifier.
_GREETINGS_AR = {
    "مرحبا", "مرحباً", "أهلا", "أهلاً", "اهلا", "السلام عليكم",
    "صباح الخير", "مساء الخير", "هلا", "هاي",
}
_THANKS_AR = {
    "شكرا", "شكراً", "شكرا لك", "شكراً لك", "شكرا جزيلا", "شكراً جزيلاً",
    "تسلم", "تسلمين", "يعطيك العافية",
}
_FAREWELLS_AR = {
    "مع السلامة", "وداعا", "وداعاً", "إلى اللقاء", "الى اللقاء", "باي",
}
_DISENGAGEMENTS_AR = {
    "لا يهم", "انسى الأمر", "انسى الامر", "لا بأس", "لا باس",
    "هذا كل شيء", "هذا كل شيء شكرا",
}

_REPLY_BY_CATEGORY = {
    "en": {
        "greeting": "Hi there! How can I help you today?",
        "thanks": "You're welcome! Let me know if there's anything else I can help with.",
        "farewell": "Take care! Feel free to reach out anytime you need help.",
        "disengagement": "No worries at all — I'm here if you need anything else.",
    },
    "ar": {
        "greeting": "مرحبًا! كيف يمكنني مساعدتك اليوم؟",
        "thanks": "على الرحب والسعة! أخبرني إذا كان هناك أي شيء آخر يمكنني مساعدتك به.",
        "farewell": "اعتنِ بنفسك! لا تتردد في التواصل معنا في أي وقت تحتاج فيه للمساعدة.",
        "disengagement": "لا مشكلة على الإطلاق — أنا هنا إذا احتجت أي شيء آخر.",
    },
}

_CATEGORY_BY_PHRASE = {
    **{phrase: "greeting" for phrase in _GREETINGS | _GREETINGS_AR},
    **{phrase: "thanks" for phrase in _THANKS | _THANKS_AR},
    **{phrase: "farewell" for phrase in _FAREWELLS | _FAREWELLS_AR},
    **{phrase: "disengagement" for phrase in _DISENGAGEMENTS | _DISENGAGEMENTS_AR},
}


def classify_smalltalk(query: str) -> Optional[tuple]:
    """Returns (category, reply) if `query`, in its entirety, is a known
    greeting/thanks/farewell/disengagement phrase — None otherwise. Exposes
    the category (not just the reply) so callers can decide priority
    against an active pending state (see agent.py's handle_message). The
    reply comes back in whichever language the phrase itself was written
    in (Phase B)."""
    normalized = _TRAILING_PUNCT_RE.sub("", query.strip().lower())
    category = _CATEGORY_BY_PHRASE.get(normalized)
    if category is None:
        return None
    return category, _REPLY_BY_CATEGORY[detect_language(query)][category]


def detect_smalltalk(query: str) -> Optional[str]:
    """Returns a canned reply if `query`, in its entirety, is a known
    greeting/thanks/farewell/disengagement phrase — None otherwise."""
    result = classify_smalltalk(query)
    return result[1] if result else None
