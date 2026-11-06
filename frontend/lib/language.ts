// Phase B (Arabic support): mirrors agent/language.py's Unicode-range
// detection exactly, so the frontend and backend agree on what counts as
// Arabic without a second source of truth (no shared package between them,
// so the ranges are kept in sync by hand — small, stable set, unlikely to
// drift).
const ARABIC_RE = /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/;

export function isArabicText(text: string): boolean {
  return ARABIC_RE.test(text);
}
