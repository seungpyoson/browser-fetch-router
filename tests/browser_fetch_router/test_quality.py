from browser_fetch_router.quality import assess_quality


def test_login_wall_is_blocked_signal():
    quality = assess_quality(
        "Please log in to continue", "<html>Please log in to continue</html>"
    )
    assert "login_wall" in quality["blocked_signals"]
    assert quality["has_main_content"] is False
    assert quality["passes_quality_gate"] is False


def test_article_content_has_main_content():
    html = "<article><p>" + ("word " * 100) + "</p></article>"
    quality = assess_quality("word " * 100, html)
    assert quality["word_count"] == 100
    assert quality["has_main_content"] is True
    assert quality["passes_quality_gate"] is True


def test_captcha_signal_blocks():
    html = '<div class="g-recaptcha"></div>'
    quality = assess_quality("Verify you are human", html)
    assert "captcha_required" in quality["blocked_signals"]
    assert quality["passes_quality_gate"] is False


def test_short_content_fails_gate():
    quality = assess_quality("Hi there", "<p>Hi there</p>")
    assert quality["word_count"] < 80
    assert quality["passes_quality_gate"] is False


def test_short_valid_content_passes_gate_at_documented_boundary():
    text = " ".join(f"word{i}" for i in range(20))
    assert len(text) >= 120
    quality = assess_quality(text, f"<article><p>{text}</p></article>")
    assert quality["word_count"] == 20
    assert quality["is_short_valid_content"] is True
    assert quality["passes_quality_gate"] is True


def test_short_valid_content_rejects_below_word_boundary():
    text = " ".join(f"word{i}" for i in range(19))
    quality = assess_quality(text, f"<article><p>{text}</p></article>")
    assert quality["word_count"] == 19
    assert quality["is_short_valid_content"] is False
    assert quality["passes_quality_gate"] is False


def test_short_valid_content_rejects_below_character_boundary():
    text = " ".join(f"w{i}" for i in range(20))
    assert len(text) < 120
    quality = assess_quality(text, f"<article><p>{text}</p></article>")
    assert quality["word_count"] == 20
    assert quality["is_short_valid_content"] is False
    assert quality["passes_quality_gate"] is False


def test_blocked_semantic_page_is_not_main_content():
    text = "Please log in to continue. " + ("article words " * 20)
    quality = assess_quality(text, f"<article><p>{text}</p></article>")
    assert "login_wall" in quality["blocked_signals"]
    assert quality["has_main_content"] is False
    assert quality["is_short_valid_content"] is False
    assert quality["passes_quality_gate"] is False


def test_strong_login_prompt_in_visible_html_blocks():
    text = (
        "Short public looking content has enough words and characters to otherwise "
        "pass the quality gate safely before the visible login prompt is detected."
    )
    html = f"<article><p>{text}</p><p>Login required to continue.</p></article>"
    quality = assess_quality(text, html)
    assert "login_wall" in quality["blocked_signals"]
    assert quality["is_short_valid_content"] is False
    assert quality["passes_quality_gate"] is False


def test_login_marker_does_not_match_inside_words():
    text = (
        "This design internal note has enough words and characters to pass as short "
        "public content for a reliable browser fetch router quality gate example."
    )
    html = f"<article><p>{text}</p></article>"
    quality = assess_quality(text, html)
    assert "login_wall" not in quality["blocked_signals"]
    assert quality["passes_quality_gate"] is True


def test_login_marker_does_not_match_across_word_internals():
    text = (
        "This assign incoming example has enough public words and characters to pass "
        "the short valid quality gate without a false login marker."
    )
    html = f"<article><p>{text}</p></article>"
    quality = assess_quality(text, html)
    assert "login_wall" not in quality["blocked_signals"]
    assert quality["passes_quality_gate"] is True


def test_js_challenge_signal_blocks():
    text = "Short public looking content has enough words and characters to pass the gate safely."
    html = f"<article><p>{text}</p><p>Checking your browser before access.</p></article>"
    quality = assess_quality(text, html)
    assert "js_challenge" in quality["blocked_signals"]
    assert quality["passes_quality_gate"] is False


def test_empty_semantic_page_is_not_main_content():
    quality = assess_quality("", "<article></article>")
    assert quality["word_count"] == 0
    assert quality["has_main_content"] is False
    assert quality["passes_quality_gate"] is False


def test_boilerplate_heavy_short_content_fails_gate():
    text = (
        "Short public looking content has enough words and characters to otherwise "
        "pass the quality gate safely before boilerplate dominates the page."
    )
    html = f"<article><p>{text}</p></article>" + (" navigation chrome " * 1000)
    quality = assess_quality(text, html)
    assert quality["boilerplate_score"] > 0.70
    assert quality["passes_quality_gate"] is False


def test_login_prompt_inside_script_is_not_visible_content():
    text = (
        "Short public looking content has enough words and characters to pass the "
        "quality gate safely without treating script text as visible page text."
    )
    html = f"""
    <article><p>{text}</p></article>
    <script>const message = "Login required to continue";</script>
    """
    quality = assess_quality(text, html)
    assert "login_wall" not in quality["blocked_signals"]
    assert quality["passes_quality_gate"] is True


def test_script_content_does_not_inflate_boilerplate_score():
    text = (
        "Short public looking content has enough words and characters to pass the "
        "quality gate safely even when inline scripts are large."
    )
    html = f"<article><p>{text}</p></article><script>{'x' * 20000}</script>"
    quality = assess_quality(text, html)
    assert quality["boilerplate_score"] <= 0.70
    assert quality["passes_quality_gate"] is True
