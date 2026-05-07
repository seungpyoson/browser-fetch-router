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
