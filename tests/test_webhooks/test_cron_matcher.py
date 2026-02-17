"""Tests for the enhanced _cron_matches_now function."""

from __future__ import annotations

from datetime import datetime

from src.workflows.daily_briefing import _cron_matches_now


def _dt(minute=0, hour=0, day=1, month=1, year=2026, weekday=None):
    """Helper to build datetime with desired weekday.

    weekday: 0=Mon, 6=Sun. If None, uses the natural weekday.
    Note: We can't force a weekday directly, but we pick known dates.
    """
    # 2026-02-02 is a Monday (weekday=0)
    # 2026-02-03 is a Tuesday (weekday=1)
    # 2026-02-06 is a Friday (weekday=4)
    # 2026-02-07 is a Saturday (weekday=5)
    # 2026-02-08 is a Sunday (weekday=6)
    return datetime(year, month, day, hour, minute)


class TestCronMatchesNow:
    # --- Basic matching ---

    def test_all_wildcards(self):
        assert _cron_matches_now("* * * * *", _dt()) is True

    def test_exact_match(self):
        assert _cron_matches_now("0 9 1 1 *", _dt(minute=0, hour=9)) is True

    def test_exact_no_match(self):
        assert _cron_matches_now("30 9 1 1 *", _dt(minute=0, hour=9)) is False

    def test_none_expr(self):
        assert _cron_matches_now(None, _dt()) is False

    def test_empty_expr(self):
        assert _cron_matches_now("", _dt()) is False

    def test_wrong_field_count(self):
        assert _cron_matches_now("* * *", _dt()) is False

    # --- Step expressions (*/N) ---

    def test_step_every_15_minutes_match(self):
        assert _cron_matches_now("*/15 * * * *", _dt(minute=0)) is True
        assert _cron_matches_now("*/15 * * * *", _dt(minute=15)) is True
        assert _cron_matches_now("*/15 * * * *", _dt(minute=30)) is True
        assert _cron_matches_now("*/15 * * * *", _dt(minute=45)) is True

    def test_step_every_15_minutes_no_match(self):
        assert _cron_matches_now("*/15 * * * *", _dt(minute=7)) is False
        assert _cron_matches_now("*/15 * * * *", _dt(minute=22)) is False

    def test_step_every_5_minutes(self):
        assert _cron_matches_now("*/5 * * * *", _dt(minute=0)) is True
        assert _cron_matches_now("*/5 * * * *", _dt(minute=5)) is True
        assert _cron_matches_now("*/5 * * * *", _dt(minute=3)) is False

    def test_step_every_30_minutes(self):
        assert _cron_matches_now("*/30 * * * *", _dt(minute=0)) is True
        assert _cron_matches_now("*/30 * * * *", _dt(minute=30)) is True
        assert _cron_matches_now("*/30 * * * *", _dt(minute=15)) is False

    # --- Range expressions (A-B) ---

    def test_hour_range(self):
        assert _cron_matches_now("0 7-18 * * *", _dt(minute=0, hour=7)) is True
        assert _cron_matches_now("0 7-18 * * *", _dt(minute=0, hour=12)) is True
        assert _cron_matches_now("0 7-18 * * *", _dt(minute=0, hour=18)) is True
        assert _cron_matches_now("0 7-18 * * *", _dt(minute=0, hour=6)) is False
        assert _cron_matches_now("0 7-18 * * *", _dt(minute=0, hour=19)) is False

    def test_weekday_range_numeric(self):
        # 2026-02-02 is Monday (0), 2026-02-06 is Friday (4)
        mon = datetime(2026, 2, 2, 9, 0)
        fri = datetime(2026, 2, 6, 9, 0)
        sat = datetime(2026, 2, 7, 9, 0)
        assert _cron_matches_now("0 9 * * 0-4", mon) is True
        assert _cron_matches_now("0 9 * * 0-4", fri) is True
        assert _cron_matches_now("0 9 * * 0-4", sat) is False

    # --- Comma-separated lists ---

    def test_comma_minutes(self):
        assert _cron_matches_now("0,15,30,45 * * * *", _dt(minute=0)) is True
        assert _cron_matches_now("0,15,30,45 * * * *", _dt(minute=15)) is True
        assert _cron_matches_now("0,15,30,45 * * * *", _dt(minute=10)) is False

    def test_comma_hours(self):
        assert _cron_matches_now("0 8,12,17 * * *", _dt(minute=0, hour=8)) is True
        assert _cron_matches_now("0 8,12,17 * * *", _dt(minute=0, hour=10)) is False

    # --- Day names ---

    def test_day_name(self):
        mon = datetime(2026, 2, 2, 9, 0)  # Monday
        assert _cron_matches_now("0 9 * * MON", mon) is True
        assert _cron_matches_now("0 9 * * TUE", mon) is False

    # --- Combined expressions ---

    def test_business_hours(self):
        """*/30 7-18 * * 1-5 = every 30 min during business hours Mon-Fri."""
        # Monday 9:30 AM
        mon_930 = datetime(2026, 2, 2, 9, 30)
        assert _cron_matches_now("*/30 7-18 * * 0-4", mon_930) is True

        # Monday 9:15 AM — not on 30-min mark
        mon_915 = datetime(2026, 2, 2, 9, 15)
        assert _cron_matches_now("*/30 7-18 * * 0-4", mon_915) is False

        # Saturday 9:30 — outside Mon-Fri
        sat_930 = datetime(2026, 2, 7, 9, 30)
        assert _cron_matches_now("*/30 7-18 * * 0-4", sat_930) is False

    def test_real_head_of_it_schedule(self):
        """*/15 * * * * = every 15 minutes, all day, all week."""
        assert _cron_matches_now("*/15 * * * *", _dt(minute=0)) is True
        assert _cron_matches_now("*/15 * * * *", _dt(minute=15)) is True
        assert _cron_matches_now("*/15 * * * *", _dt(minute=7)) is False

    def test_daily_7am_weekdays(self):
        """0 7 * * 1-5 = daily briefing at 7am Mon-Fri."""
        # Using weekday range 0-4 (Mon-Fri in our system)
        mon_7am = datetime(2026, 2, 2, 7, 0)
        assert _cron_matches_now("0 7 * * 0-4", mon_7am) is True

        sat_7am = datetime(2026, 2, 7, 7, 0)
        assert _cron_matches_now("0 7 * * 0-4", sat_7am) is False

    # --- Range with step ---

    def test_range_with_step(self):
        """1-5/2 = values 1, 3, 5 in weekday field."""
        tue = datetime(2026, 2, 3, 0, 0)  # Tuesday = 1
        wed = datetime(2026, 2, 4, 0, 0)  # Wednesday = 2
        thu = datetime(2026, 2, 5, 0, 0)  # Thursday = 3
        assert _cron_matches_now("0 0 * * 1-5/2", tue) is True
        assert _cron_matches_now("0 0 * * 1-5/2", wed) is False
        assert _cron_matches_now("0 0 * * 1-5/2", thu) is True

    # --- Edge cases ---

    def test_step_zero_returns_empty(self):
        # */0 is technically invalid, should not match anything
        assert _cron_matches_now("*/0 * * * *", _dt(minute=0)) is False

    def test_negative_step_returns_empty(self):
        assert _cron_matches_now("*/-1 * * * *", _dt(minute=0)) is False
