from __future__ import annotations

import re

LOGIN_MARKERS = ("log in", "sign in", "please log in", "login required", "sign up to read")
CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "g-recaptcha",
    "turnstile",
    "cf-challenge",
    "verify you are human",
)
JS_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "enable javascript and cookies",
    "this challenge page was issued",
)
SEMANTIC_MAIN_MARKERS = (
    "<main",
    "<article",
    'role="main"',
    "entry-content",
    "content-body",
    "post-content",
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def assess_quality(extracted_text: str, raw_html: str) -> dict[str, object]:
    lower_text = extracted_text.lower()
    lower_html = raw_html.lower()
    blocked: list[str] = []
    if any(marker in lower_text[:500] for marker in LOGIN_MARKERS):
        blocked.append("login_wall")
    if any(marker in lower_html or marker in lower_text for marker in CAPTCHA_MARKERS):
        blocked.append("captcha_required")
    if any(
        marker in lower_html or marker in lower_text for marker in JS_CHALLENGE_MARKERS
    ):
        blocked.append("js_challenge")
    words = _word_count(extracted_text)
    has_semantic_main = any(marker in lower_html for marker in SEMANTIC_MAIN_MARKERS)
    has_main_content = has_semantic_main or (words >= 80 and not blocked)
    raw_visible_chars = max(len(re.sub(r"<[^>]+>", " ", raw_html)), 1)
    boilerplate_score = max(0.0, min(1.0, 1.0 - (len(extracted_text) / raw_visible_chars)))
    passes_quality_gate = bool(
        words >= 80
        and has_main_content
        and boilerplate_score <= 0.70
        and not blocked
    )
    return {
        "word_count": words,
        "has_main_content": has_main_content,
        "boilerplate_score": boilerplate_score,
        "blocked_signals": blocked,
        "passes_quality_gate": passes_quality_gate,
    }
