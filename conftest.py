"""
conftest.py - Pytest fixtures and test environment setup.
Sets a compliant, non-placeholder test User-Agent for SEC EDGAR tests
so that offline and mocked unit tests execute cleanly without violating
Fair Access validation requirements.
"""

import os
import pytest


@pytest.fixture(autouse=True)
def setup_test_sec_user_agent(monkeypatch):
    """
    Ensures an audited, compliant test User-Agent is present for offline/mocked tests,
    unless explicitly set or tested.
    """
    if "SEC_EDGAR_USER_AGENT" not in os.environ:
        monkeypatch.setenv(
            "SEC_EDGAR_USER_AGENT",
            "USStockScreenerAudit/1.0 (auditor@screener-audit.org)"
        )
