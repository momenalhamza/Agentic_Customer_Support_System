"""Groq translation used ONLY to bridge the retrieval embedding model's
English-only training (bge-large-en-v1.5 locally / bge-small-en-v1.5 in the
cloud deployment — neither is trained for cross-lingual retrieval; see
ANALYSIS.md's Phase B entry for the retrieval-quality evidence).

This translation is never shown to the customer and never used as the
source of the final answer's facts or wording — it exists purely so
HybridRetriever.retrieve() searches with an English query against the
English corpus. The customer's own message and the generated answer
(agent/generator.py) both stay in the language the customer actually used.

Deliberately uses `llm.upgrade_model`, not the default 8B model: live
testing (ANALYSIS.md's Phase B entry) found the 8B model unreliable here —
it sometimes echoed the Arabic text back unchanged, or prefixed the
translation with a "Translation:" label despite being told not to. This is
one occasional extra call only on non-English turns, not every turn, so the
larger model's cost is worth the reliability.
"""

from __future__ import annotations

from agent.language import detect_language
from agent.llm_client import LLMClient

_SYSTEM_PROMPT = (
    "Translate the customer support message to English. Respond with ONLY "
    "the English translation itself — no preamble, no label like "
    "'Translation:', no quotes, no explanation. If it's already in "
    "English, return it unchanged."
)

_RETRY_REMINDER = (
    "Your previous reply wasn't a plain English translation. Try again: "
    "output ONLY the English translation, nothing else — no label, no "
    "quotes, no original text."
)


def _strip_label(text: str) -> str:
    """Defense in depth against the model adding a preamble despite being
    told not to (same spirit as agent/intent_classifier.py's
    _grounded_slots) — strips a leading "Translation:"-style label if
    present."""
    stripped = text.strip().strip('"')
    for label in ("translation:", "english translation:", "translation -"):
        if stripped.lower().startswith(label):
            return stripped[len(label):].strip().strip('"')
    return stripped


def translate_to_english(llm: LLMClient, text: str) -> str:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    for attempt in range(2):
        completion = llm.complete(messages=messages, model=llm.upgrade_model, temperature=0.0)
        raw = completion.choices[0].message.content
        candidate = _strip_label(raw)
        if detect_language(candidate) == "en":
            return candidate
        if attempt == 0:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": _RETRY_REMINDER})
    # Both attempts still contain Arabic script — degrade to the original
    # text rather than silently retrieving on a mangled/partial
    # translation. Retrieval quality is then whatever it is for the
    # untranslated query (see ANALYSIS.md's Phase B entry) — worse, but a
    # known, bounded failure mode rather than a garbled one.
    return text
