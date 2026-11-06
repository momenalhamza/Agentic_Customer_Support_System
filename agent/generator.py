"""Groq answer generation from retrieved chunks (ARCHITECTURE.md §8) — an
actual synthesized answer, not a raw chunk dump or template concatenation.
"""

from __future__ import annotations

from agent.llm_client import LLMClient

_SYSTEM_PROMPT_EN = (
    "You are a customer support assistant for Meridian Retail. Answer the "
    "customer's question using ONLY the information in the provided policy "
    "excerpts below. Be concise and direct. If the excerpts don't actually "
    "answer the question, say so plainly instead of guessing."
)

# Phase B (Arabic support, see ANALYSIS.md): the corpus itself is never
# translated — these excerpts are always English — but the customer wrote
# in Arabic, so the answer must come back in Arabic too. One generation
# call directly in the target language, not generate-then-translate, for
# both latency and fidelity.
_SYSTEM_PROMPT_AR = (
    "You are a customer support assistant for Meridian Retail. The customer "
    "wrote in Arabic. The policy excerpts below are in English — use ONLY "
    "the information in them as your factual source, translating the "
    "relevant facts rather than just repeating them in English. Write your "
    "entire answer in fluent Arabic. Be concise and direct. If the excerpts "
    "don't actually answer the question, say so plainly (in Arabic) instead "
    "of guessing."
)


def generate_answer(llm: LLMClient, query: str, chunks: list, language: str = "en", model: str = None) -> str:
    context = "\n\n".join(f"[{c.category}/{c.heading}]\n{c.text}" for c in chunks)
    system_prompt = _SYSTEM_PROMPT_AR if language == "ar" else _SYSTEM_PROMPT_EN
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Policy excerpts:\n{context}\n\nCustomer question: {query}"},
    ]
    completion = llm.complete(messages=messages, model=model, temperature=0.2)
    return completion.choices[0].message.content.strip()
