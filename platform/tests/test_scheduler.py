"""
tests/test_scheduler.py - Tests for Market Scheduler and Jobs.
"""

from app.scheduler.market_scheduler import create_market_scheduler, job_pre_market
from config.settings import TIMEZONE_NAME


def test_scheduler_jobs_configured():
    sched = create_market_scheduler()
    jobs = sched.get_jobs()
    job_ids = {j.id for j in jobs}

    assert "pre_market_job" in job_ids
    assert "midday_check_job" in job_ids
    assert "market_close_job" in job_ids
    assert "weekly_maintenance_job" in job_ids

    for j in jobs:
        assert str(j.trigger.timezone) == TIMEZONE_NAME


def test_pre_market_job_execution():
    job_pre_market()
