"""
test_alerts.py - Alerts moduli va deduplikatsiya testlari.
"""
import pytest
from alerts import AlertManager


@pytest.fixture
def am(tmp_path):
    db_file = str(tmp_path / "test_screener.db")
    log_file = str(tmp_path / "alerts.log")
    return AlertManager(db_path=db_file, log_file=log_file)


def test_alert_sent_and_deduplicated(am):
    alert_id = "NVDA_2026-10-10_BUY_V2"
    ok1, msg1 = am.send_alert(alert_id, "NVDA", "BUY_SIGNAL", "INFO", "Breakout signali tasdiqlandi")
    assert ok1 is True
    assert msg1 in ("SENT", "LOGGED_ONLY")

    # Bir xil alert_id qayta yuborilganda bloklanishi shart
    ok2, msg2 = am.send_alert(alert_id, "NVDA", "BUY_SIGNAL", "INFO", "Breakout signali tasdiqlandi")
    assert ok2 is False
    assert "DUPLICATE_SUPPRESSED" in msg2


def test_alert_severity_logging(am):
    ok, _ = am.send_alert("ERR_01", "SYSTEM", "DATA_ERROR", "CRITICAL", "SPY kotirovkasi topilmadi")
    assert ok is True
    with open(am.log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "[CRITICAL]" in content
    assert "SPY kotirovkasi topilmadi" in content
