from browser_fetch_router.default_deny import is_default_denied, is_default_denied_hostname


def test_password_manager_is_default_denied():
    assert is_default_denied("https://my.1password.com/vault")


def test_github_settings_path_is_default_denied():
    assert is_default_denied("https://github.com/settings/profile")


def test_gmail_is_hostname_sensitive():
    assert is_default_denied_hostname("mail.google.com")


def test_neutral_url_not_denied():
    assert not is_default_denied("https://example.com/article")


def test_github_public_repo_not_denied():
    # Settings paths denied; public repo paths are not.
    assert not is_default_denied("https://github.com/torvalds/linux")
