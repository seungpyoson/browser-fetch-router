from __future__ import annotations

import re

LOGIN_MARKERS = ("log in", "sign in", "please log in", "login required", "sign up to read")
STRONG_LOGIN_MARKERS = (
    "please log in",
    "login required",
    "sign up to read",
    "subscribe to read",
    "register to read",
    "create a free account",
    "members only",
)
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

SHORT_VALID_MIN_WORDS = 20
SHORT_VALID_MIN_CHARS = 120


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _visible_text(raw_html: str) -> str:
    without_script_style = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(r"<[^>]+>", " ", without_script_style)


def _contains_phrase_marker(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        phrase = r"\s+".join(re.escape(part) for part in marker.split())
        if re.search(rf"(?<!\w){phrase}(?!\w)", text):
            return True
    return False


def assess_quality(extracted_text: str, raw_html: str) -> dict[str, object]:
    lower_text = extracted_text.lower()
    lower_html = raw_html.lower()
    visible_html = _visible_text(raw_html)
    lower_visible_html = visible_html.lower()
    blocked: list[str] = []
    if (
        _contains_phrase_marker(lower_text[:500], LOGIN_MARKERS)
        or _contains_phrase_marker(lower_text, STRONG_LOGIN_MARKERS)
        or _contains_phrase_marker(lower_visible_html, STRONG_LOGIN_MARKERS)
    ):
        blocked.append("login_wall")
    if any(marker in lower_html or marker in lower_text for marker in CAPTCHA_MARKERS):
        blocked.append("captcha_required")
    if any(
        marker in lower_html or marker in lower_text for marker in JS_CHALLENGE_MARKERS
    ):
        blocked.append("js_challenge")
    words = _word_count(extracted_text)
    has_semantic_main = any(marker in lower_html for marker in SEMANTIC_MAIN_MARKERS)
    is_short_valid_content = bool(
        words >= SHORT_VALID_MIN_WORDS
        and len(extracted_text.strip()) >= SHORT_VALID_MIN_CHARS
        and not blocked
    )
    has_main_content = bool(
        not blocked
        and words > 0
        and (has_semantic_main or words >= 80 or is_short_valid_content)
    )
    raw_visible_chars = max(len(visible_html), 1)
    boilerplate_score = max(0.0, min(1.0, 1.0 - (len(extracted_text) / raw_visible_chars)))
    passes_quality_gate = bool(
        (words >= 80 or is_short_valid_content)
        and has_main_content
        and boilerplate_score <= 0.70
        and not blocked
    )
    return {
        "word_count": words,
        "has_main_content": has_main_content,
        "is_short_valid_content": is_short_valid_content,
        "boilerplate_score": boilerplate_score,
        "blocked_signals": blocked,
        "passes_quality_gate": passes_quality_gate,
    }
