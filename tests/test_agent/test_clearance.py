"""Tests for clearance and time awareness in agent prompts.

Covers:
- CLEARANCE_SUPPLEMENT constant exists and contains all levels
- build_clearance_context() returns context for non-restricted users
- build_clearance_context() returns empty for restricted (max level)
- Clearance context is injected into run_conversation_turn
- get_timestamp_context() returns a formatted time string
- get_base_system_prompt() includes timestamp + BASE_SYSTEM_PROMPT
- get_system_prompt() includes timestamp + DAILY_BRIEFING_SUPPLEMENT
"""

from src.agent.prompts import (
    BASE_SYSTEM_PROMPT,
    CLEARANCE_SUPPLEMENT,
    build_clearance_context,
    get_base_system_prompt,
    get_system_prompt,
    get_timestamp_context,
)

# ============================================================
# CLEARANCE_SUPPLEMENT
# ============================================================


class TestClearanceSupplement:
    def test_supplement_exists(self):
        assert CLEARANCE_SUPPLEMENT is not None
        assert len(CLEARANCE_SUPPLEMENT) > 100

    def test_supplement_mentions_all_levels(self):
        for level in ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"):
            assert level in CLEARANCE_SUPPLEMENT

    def test_supplement_has_placeholder(self):
        assert "{clearance_level}" in CLEARANCE_SUPPLEMENT


# ============================================================
# build_clearance_context()
# ============================================================


class TestBuildClearanceContext:
    def test_public_gets_context(self):
        ctx = build_clearance_context("public")
        assert ctx != ""
        assert "PUBLIC" in ctx

    def test_internal_gets_context(self):
        ctx = build_clearance_context("internal")
        assert ctx != ""
        assert "INTERNAL" in ctx

    def test_confidential_gets_context(self):
        ctx = build_clearance_context("confidential")
        assert ctx != ""
        assert "CONFIDENTIAL" in ctx

    def test_restricted_gets_empty(self):
        """Restricted is max clearance — no filtering needed."""
        ctx = build_clearance_context("restricted")
        assert ctx == ""

    def test_empty_clearance_returns_empty(self):
        ctx = build_clearance_context("")
        assert ctx == ""

    def test_context_contains_rules(self):
        ctx = build_clearance_context("public")
        # Should mention information filtering
        assert "share" in ctx.lower() or "filter" in ctx.lower() or "level" in ctx.lower()

    def test_context_includes_level_name(self):
        """The formatted context should include the user's clearance level."""
        ctx = build_clearance_context("internal")
        assert "INTERNAL" in ctx

    def test_context_different_for_each_level(self):
        """Different clearance levels should produce different context strings."""
        public_ctx = build_clearance_context("public")
        internal_ctx = build_clearance_context("internal")
        confidential_ctx = build_clearance_context("confidential")

        # They should all be non-empty (except restricted)
        assert public_ctx != ""
        assert internal_ctx != ""
        assert confidential_ctx != ""

        # They should differ because the level name is different
        assert public_ctx != internal_ctx
        assert internal_ctx != confidential_ctx


# ============================================================
# Time Awareness
# ============================================================


class TestGetTimestampContext:
    def test_returns_string(self):
        result = get_timestamp_context()
        assert isinstance(result, str)
        assert len(result) > 10

    def test_contains_current_time_label(self):
        result = get_timestamp_context()
        assert "Current Time" in result

    def test_contains_day_of_week(self):
        result = get_timestamp_context()
        days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        assert any(day in result for day in days)

    def test_contains_year(self):
        from datetime import datetime, timezone

        result = get_timestamp_context()
        current_year = str(datetime.now(timezone.utc).year)
        assert current_year in result

    def test_contains_am_or_pm(self):
        result = get_timestamp_context()
        assert "AM" in result or "PM" in result


class TestGetBaseSystemPrompt:
    def test_starts_with_timestamp(self):
        result = get_base_system_prompt()
        assert result.startswith("**Current Time:**")

    def test_contains_base_prompt(self):
        result = get_base_system_prompt()
        assert "Sidera" in result
        assert BASE_SYSTEM_PROMPT in result

    def test_timestamp_before_base(self):
        result = get_base_system_prompt()
        ts_pos = result.index("Current Time")
        base_pos = result.index("You are **Sidera**")
        assert ts_pos < base_pos


class TestGetSystemPrompt:
    def test_includes_timestamp(self):
        result = get_system_prompt()
        assert "Current Time" in result

    def test_includes_daily_briefing(self):
        result = get_system_prompt()
        assert "Analysis Framework" in result

    def test_different_from_base(self):
        base = get_base_system_prompt()
        full = get_system_prompt()
        assert len(full) > len(base)
