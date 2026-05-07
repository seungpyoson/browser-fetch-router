"""Shared test fixtures for browser_fetch_router tests."""
import sys
from pathlib import Path

# Project root is two directories up from this conftest.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
