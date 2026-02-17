"""Tests for the role / department hierarchy DB service methods.

Covers:
- save_role_result() — persists AnalysisResult with role_id, department_id, skill_id
- get_role_history() — retrieves role results filtered by user_id + role_id
- log_role_event() — persists AuditLog with role_id + department_id
- AnalysisResult model has department_id and role_id columns
- AuditLog model has department_id and role_id columns
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service
from src.models.schema import AnalysisResult, AuditLog, Base


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database and yield a session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ============================================================
# Schema Column Tests
# ============================================================


class TestSchemaColumns:
    """Verify that the AnalysisResult and AuditLog models expose the
    hierarchy columns (department_id, role_id) at the ORM level."""

    def test_analysis_result_has_department_id_column(self):
        column_names = [c.name for c in AnalysisResult.__table__.columns]
        assert "department_id" in column_names

    def test_analysis_result_has_role_id_column(self):
        column_names = [c.name for c in AnalysisResult.__table__.columns]
        assert "role_id" in column_names

    def test_audit_log_has_department_id_column(self):
        column_names = [c.name for c in AuditLog.__table__.columns]
        assert "department_id" in column_names

    def test_audit_log_has_role_id_column(self):
        column_names = [c.name for c in AuditLog.__table__.columns]
        assert "role_id" in column_names

    def test_analysis_result_department_id_is_nullable(self):
        col = AnalysisResult.__table__.columns["department_id"]
        assert col.nullable is True

    def test_analysis_result_role_id_is_nullable(self):
        col = AnalysisResult.__table__.columns["role_id"]
        assert col.nullable is True

    def test_audit_log_department_id_is_nullable(self):
        col = AuditLog.__table__.columns["department_id"]
        assert col.nullable is True

    def test_audit_log_role_id_is_nullable(self):
        col = AuditLog.__table__.columns["role_id"]
        assert col.nullable is True


# ============================================================
# save_role_result Tests
# ============================================================


class TestSaveRoleResult:
    async def test_basic_save(self, db_session):
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="paid_media_analyst",
            department_id="marketing",
            run_date=date(2025, 2, 10),
            briefing_content="Role briefing content",
        )

        assert result.id is not None
        assert result.user_id == "user_1"
        assert result.role_id == "paid_media_analyst"
        assert result.department_id == "marketing"
        assert result.run_date == date(2025, 2, 10)
        assert result.briefing_content == "Role briefing content"

    async def test_skill_id_convention(self, db_session):
        """skill_id should be set to 'role:<role_id>' by convention."""
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="campaign_optimizer",
            department_id="performance",
            run_date=date(2025, 3, 1),
        )

        assert result.skill_id == "role:campaign_optimizer"

    async def test_with_recommendations(self, db_session):
        recs = [
            {"action": "pause_campaign", "campaign": "C1"},
            {"action": "increase_budget", "campaign": "C2"},
        ]
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 2, 15),
            recommendations=recs,
        )

        assert result.recommendations == recs

    async def test_with_cost_info(self, db_session):
        cost_info = {"total_cost_usd": Decimal("0.52")}
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 2, 15),
            cost_info=cost_info,
        )

        assert result.llm_cost_usd == Decimal("0.52")

    async def test_with_no_cost_info(self, db_session):
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 2, 15),
            cost_info=None,
        )

        assert result.llm_cost_usd == 0

    async def test_with_accounts_analyzed(self, db_session):
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 2, 15),
            accounts_analyzed=[1, 2, 3],
        )

        assert result.accounts_analyzed == [1, 2, 3]

    async def test_defaults_for_optional_lists(self, db_session):
        """When recommendations and accounts_analyzed are omitted, they default to []."""
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 2, 15),
        )

        assert result.recommendations == []
        assert result.accounts_analyzed == []

    async def test_empty_briefing_content_default(self, db_session):
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 2, 15),
        )

        assert result.briefing_content == ""


# ============================================================
# get_role_history Tests
# ============================================================


class TestGetRoleHistory:
    async def test_returns_results_for_user_and_role(self, db_session):
        for day in range(1, 4):
            await service.save_role_result(
                db_session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                run_date=date(2025, 3, day),
                briefing_content=f"Briefing for day {day}",
            )

        results = await service.get_role_history(db_session, user_id="user_1", role_id="analyst")

        assert len(results) == 3

    async def test_ordered_newest_first(self, db_session):
        for day in [1, 5, 3]:
            await service.save_role_result(
                db_session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                run_date=date(2025, 3, day),
                briefing_content=f"Day {day}",
            )

        results = await service.get_role_history(db_session, user_id="user_1", role_id="analyst")

        # created_at ordering: most recently inserted first
        assert len(results) == 3

    async def test_filters_by_role_id(self, db_session):
        await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 3, 1),
        )
        await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="optimizer",
            department_id="marketing",
            run_date=date(2025, 3, 2),
        )

        analyst_results = await service.get_role_history(
            db_session, user_id="user_1", role_id="analyst"
        )
        optimizer_results = await service.get_role_history(
            db_session, user_id="user_1", role_id="optimizer"
        )

        assert len(analyst_results) == 1
        assert len(optimizer_results) == 1

    async def test_filters_by_user_id(self, db_session):
        await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 3, 1),
        )
        await service.save_role_result(
            db_session,
            user_id="user_2",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 3, 1),
        )

        user1_results = await service.get_role_history(
            db_session, user_id="user_1", role_id="analyst"
        )
        user2_results = await service.get_role_history(
            db_session, user_id="user_2", role_id="analyst"
        )

        assert len(user1_results) == 1
        assert len(user2_results) == 1

    async def test_respects_limit(self, db_session):
        for day in range(1, 8):
            await service.save_role_result(
                db_session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                run_date=date(2025, 3, day),
            )

        results = await service.get_role_history(
            db_session, user_id="user_1", role_id="analyst", limit=3
        )

        assert len(results) == 3

    async def test_default_limit_is_ten(self, db_session):
        for day in range(1, 16):
            await service.save_role_result(
                db_session,
                user_id="user_1",
                role_id="analyst",
                department_id="marketing",
                run_date=date(2025, 3, day),
            )

        results = await service.get_role_history(db_session, user_id="user_1", role_id="analyst")

        assert len(results) == 10

    async def test_no_results(self, db_session):
        results = await service.get_role_history(
            db_session, user_id="nonexistent", role_id="no_such_role"
        )

        assert results == []

    async def test_does_not_return_non_role_results(self, db_session):
        """Ensure regular analysis results (without role_id) are excluded."""
        await service.save_analysis_result(
            db_session,
            user_id="user_1",
            run_date=date(2025, 3, 1),
            briefing_content="Regular briefing",
        )
        await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 3, 1),
            briefing_content="Role briefing",
        )

        results = await service.get_role_history(db_session, user_id="user_1", role_id="analyst")

        assert len(results) == 1
        assert results[0].briefing_content == "Role briefing"


# ============================================================
# log_role_event Tests
# ============================================================


class TestLogRoleEvent:
    async def test_basic_log(self, db_session):
        entry = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="paid_media_analyst",
            department_id="marketing",
            event_type="role_run_started",
        )

        assert entry.id is not None
        assert entry.user_id == "user_1"
        assert entry.role_id == "paid_media_analyst"
        assert entry.department_id == "marketing"
        assert entry.event_type == "role_run_started"

    async def test_default_source(self, db_session):
        entry = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_completed",
        )

        assert entry.source == "role_runner"

    async def test_custom_source(self, db_session):
        entry = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_completed",
            source="custom_source",
        )

        assert entry.source == "custom_source"

    async def test_with_event_data(self, db_session):
        data = {"skills_run": 3, "total_duration": 12.5, "model": "claude-sonnet"}
        entry = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_completed",
            event_data=data,
        )

        assert entry.event_data == data

    async def test_no_event_data(self, db_session):
        entry = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_started",
        )

        assert entry.event_data is None

    async def test_created_at_is_set(self, db_session):
        entry = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_started",
        )

        assert entry.created_at is not None

    async def test_multiple_events_for_same_role(self, db_session):
        """Multiple events can be logged for the same role run."""
        await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_started",
        )
        await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_completed",
            event_data={"skills_run": 3},
        )

        # Verify via audit trail
        trail = await service.get_audit_trail(db_session, "user_1")
        role_events = [e for e in trail if e.role_id == "analyst"]
        assert len(role_events) == 2

    async def test_role_events_appear_in_audit_trail(self, db_session):
        """Role events should be visible in the general audit trail."""
        await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            event_type="role_run_started",
        )
        await service.log_event(
            db_session,
            user_id="user_1",
            event_type="analysis_run",
        )

        trail = await service.get_audit_trail(db_session, "user_1")
        assert len(trail) == 2


# ============================================================
# Integration / Edge Case Tests
# ============================================================


class TestHierarchyIntegration:
    async def test_role_result_and_event_lifecycle(self, db_session):
        """Full lifecycle: log start event, save result, log completion event."""
        # 1. Log start
        start_event = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="paid_media_analyst",
            department_id="marketing",
            event_type="role_run_started",
            event_data={"skills": ["budget_analysis", "keyword_check"]},
        )
        assert start_event.id is not None

        # 2. Save result
        result = await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="paid_media_analyst",
            department_id="marketing",
            run_date=date(2025, 3, 15),
            briefing_content="Budget is on track. Keywords performing well.",
            recommendations=[{"action": "no_change", "reason": "stable"}],
            cost_info={"total_cost_usd": Decimal("0.52")},
        )
        assert result.skill_id == "role:paid_media_analyst"
        assert result.llm_cost_usd == Decimal("0.52")

        # 3. Log completion
        end_event = await service.log_role_event(
            db_session,
            user_id="user_1",
            role_id="paid_media_analyst",
            department_id="marketing",
            event_type="role_run_completed",
            event_data={"analysis_id": result.id, "duration_s": 8.2},
        )
        assert end_event.id is not None

        # 4. Verify history
        history = await service.get_role_history(
            db_session, user_id="user_1", role_id="paid_media_analyst"
        )
        assert len(history) == 1
        assert history[0].id == result.id

    async def test_different_departments_same_role_id(self, db_session):
        """Different departments can share the same role_id string."""
        await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="marketing",
            run_date=date(2025, 3, 1),
        )
        await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="analyst",
            department_id="finance",
            run_date=date(2025, 3, 2),
        )

        # get_role_history filters by role_id, not department_id
        results = await service.get_role_history(db_session, user_id="user_1", role_id="analyst")
        assert len(results) == 2

    async def test_role_result_coexists_with_skill_result(self, db_session):
        """Role results and skill results are stored in the same table
        but can be distinguished by their skill_id prefix."""
        await service.save_skill_result(
            db_session,
            user_id="user_1",
            skill_id="budget_analysis",
            run_date=date(2025, 3, 1),
            briefing_content="Skill result",
        )
        await service.save_role_result(
            db_session,
            user_id="user_1",
            role_id="paid_media_analyst",
            department_id="marketing",
            run_date=date(2025, 3, 1),
            briefing_content="Role result",
        )

        # Skill history should not include the role result
        skill_history = await service.get_skill_history(
            db_session, user_id="user_1", skill_id="budget_analysis"
        )
        assert len(skill_history) == 1
        assert skill_history[0].briefing_content == "Skill result"

        # Role history should not include the skill result
        role_history = await service.get_role_history(
            db_session, user_id="user_1", role_id="paid_media_analyst"
        )
        assert len(role_history) == 1
        assert role_history[0].briefing_content == "Role result"
