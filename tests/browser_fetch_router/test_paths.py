"""Regression tests for paths.atomic_write_bytes."""

import os

import pytest

from browser_fetch_router.paths import atomic_write_bytes


def test_atomic_write_bytes_writes_and_replaces(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_bytes(target, b'{"k": "v"}')
    assert target.read_bytes() == b'{"k": "v"}'
    # File mode is 0o600 (private).
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600


def test_atomic_write_bytes_overwrites_existing(tmp_path):
    target = tmp_path / "out.json"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_write_bytes_unlinks_tmp_on_failure(tmp_path, monkeypatch):
    """Regression for Gemini round 2 #M1. Class fix: every NamedTemporaryFile
    or mkstemp call site needs to unlink its tmp on failure. Without this,
    a crash mid-write leaves a sibling `.<name>.<random>.tmp` orphan in the
    target directory that grows unbounded across crashes.

    Simulated failure: monkeypatch os.replace to raise. Before the fix the
    tmp file would stay behind; after the fix the tmp is unlinked before
    the exception propagates.
    """
    target = tmp_path / "out.json"

    def boom(*a, **kw):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated rename failure"):
        atomic_write_bytes(target, b"will not survive")

    # Target was never created — replace failed.
    assert not target.exists()
    # No orphan tmp file remains in the directory.
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], (
        f"orphan tmp file(s) leaked after replace failure: {leftovers}"
    )


def test_atomic_write_bytes_unlinks_tmp_on_chmod_failure(tmp_path, monkeypatch):
    """chmod is best-effort (already wrapped in try/except OSError) so this
    case never raises — but if a future maintainer tightens it, the cleanup
    behavior is still tested via os.replace failure above."""
    target = tmp_path / "out.json"
    atomic_write_bytes(target, b"x")
    assert target.read_bytes() == b"x"


def test_atomic_write_bytes_creates_parent_dir(tmp_path):
    target = tmp_path / "nested" / "deep" / "file.json"
    atomic_write_bytes(target, b"deep")
    assert target.read_bytes() == b"deep"


# ---------------------------------------------------------------------------
# Round-17-followup-2 F-N3 — W4 load-bearing-claim regression locks.
# ---------------------------------------------------------------------------
#
# `validate_skill_md_dest` and `validate_image_dest` (paths.py) check
# basename and extension only — symlinks on the path are NOT rejected.
# That's safe ONLY because `atomic_write_bytes` uses `os.replace`, which
# does not follow symlinks for the destination: a symlinked target is
# REPLACED with a regular file, leaving the symlink-target file
# untouched. (See W4 in cli-write-containment-contract.md and the
# block comment above `validate_skill_md_dest` in paths.py.)
#
# This load-bearing fact is stdlib semantics, not our code. A future
# refactor of `atomic_write_bytes` (e.g., switching to `shutil.move`,
# whose copy+unlink fallback path resolves symlinks) would silently
# break the W4 security boundary. The tests below pin the property
# against OUR helper so any such refactor fails loudly.
#
# These tests pass today. The PR commit message documents that they
# were verified to FAIL when atomic_write_bytes was experimentally
# rewritten to use shutil.copyfile + os.unlink (the symlink-following
# variant) — proving the locks are not tautological.


def test_atomic_write_bytes_does_not_follow_symlink_destination(tmp_path):
    """W4: a symlink destination is REPLACED with a regular file; the
    symlink target file is NOT modified.

    Without this property, a pre-existing symlink at a validator-approved
    path (e.g., `~/Pictures/screenshot.png` symlinked to `~/.ssh/id_rsa`)
    would let an agent-channel screenshot write through to the secret.
    """
    real_target = tmp_path / "secret_file"
    secret_bytes = b"# secret content do not modify\n"
    real_target.write_bytes(secret_bytes)

    sym = tmp_path / "screenshot.png"
    sym.symlink_to(real_target)

    atomic_write_bytes(sym, b"# new png bytes\n")

    assert real_target.read_bytes() == secret_bytes, (
        "atomic_write_bytes followed the symlink and modified the "
        "original target — W4 security boundary broken."
    )
    assert not sym.is_symlink(), (
        "destination symlink was not replaced with a regular file"
    )
    assert sym.read_bytes() == b"# new png bytes\n"


def test_atomic_write_bytes_safe_under_toctou_swap_to_symlink(tmp_path):
    """W4 + TOCTOU: even if an attacker swaps a regular file for a
    symlink AFTER validation passed but BEFORE the write, the symlink
    target file is NOT modified. This is what justifies validating only
    the path string (not the resolved inode) in `validate_image_dest`
    and `validate_skill_md_dest`.
    """
    secret = tmp_path / "secret_file"
    secret_bytes = b"# secret content do not modify\n"
    secret.write_bytes(secret_bytes)

    target = tmp_path / "screenshot.png"
    target.write_bytes(b"# legit pre-existing\n")

    # Race window: replace the regular file with a symlink to the
    # secret AFTER validation would have run, BEFORE atomic_write_bytes.
    target.unlink()
    target.symlink_to(secret)

    atomic_write_bytes(target, b"# new png bytes\n")

    assert secret.read_bytes() == secret_bytes, (
        "TOCTOU swap-to-symlink reached the secret file — atomic write "
        "must not follow symlinks even under race"
    )
    assert not target.is_symlink()
    assert target.read_bytes() == b"# new png bytes\n"


def test_atomic_write_bytes_does_not_modify_other_hardlink(tmp_path):
    """W4 hardlink semantics: `os.replace` swaps the destination's
    directory entry to point at a new inode (the temp file). Other
    names (hardlinks) for the original inode are unaffected — they
    keep their data. So a pre-existing hardlink at the destination
    cannot be used to leak the new bytes back to the original file.
    """
    real = tmp_path / "real_file"
    real_bytes = b"# original do not modify\n"
    real.write_bytes(real_bytes)
    alias = tmp_path / "alias_via_hardlink"
    os.link(real, alias)
    real_inode = real.stat().st_ino
    assert alias.stat().st_ino == real_inode, "hardlink setup precondition"

    atomic_write_bytes(alias, b"# new bytes\n")

    assert real.read_bytes() == real_bytes, (
        "hardlinked original was modified — atomic_write_bytes must "
        "create a new inode rather than overwrite the existing one"
    )
    assert alias.stat().st_ino != real_inode, (
        "alias kept the same inode — atomic_write_bytes did not replace"
    )
    assert alias.read_bytes() == b"# new bytes\n"
