"""Tests for the database service layer.

Uses SQLite in-memory (async with aiosqlite) as the test database.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service
from src.models.schema import (
    ActionType,
    ApprovalStatus,
    Base,
    Platform,
)


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
# Helper to create prerequisite rows
# ============================================================


async def _create_account(session, user_id="user_1", platform=Platform.GOOGLE_ADS):
    """Create and return a basic account for use in other tests."""
    account = await service.upsert_account(
        session,
        user_id=user_id,
        platform=platform,
        platform_account_id="123-456-7890",
        account_name="Test Account",
    )
    return account


async def _create_analysis(session, user_id="user_1"):
    """Create and return a basic analysis result."""
    return await service.save_analysis_result(
        session,
        user_id=user_id,
        run_date=date(2025, 1, 15),
        briefing_content="Daily briefing content",
        recommendations=[{"action": "increase_budget", "campaign": "C1"}],
        cost_info={"llm_cost_usd": Decimal("1.50"), "llm_input_tokens": 5000},
        accounts_analyzed=[1, 2],
    )


async def _create_campaign(session, account_id):
    """Create and return a basic campaign."""
    return await service.upsert_campaign(
        session,
        account_id=account_id,
        platform=Platform.GOOGLE_ADS,
        platform_campaign_id="camp_001",
        campaign_name="Search - Brand",
        campaign_type="search",
        status="enabled",
    )


# ============================================================
# Account Tests
# ============================================================


class TestAccounts:
    async def test_create_account(self, db_session):
        account = await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.GOOGLE_ADS,
            platform_account_id="123-456-7890",
            account_name="My Google Ads",
        )
        assert account.id is not None
        assert account.user_id == "user_1"
        assert account.platform == Platform.GOOGLE_ADS
        assert account.platform_account_id == "123-456-7890"
        assert account.account_name == "My Google Ads"

    async def test_upsert_existing_account(self, db_session):
        account1 = await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.GOOGLE_ADS,
            platform_account_id="123-456-7890",
            account_name="Original Name",
        )
        original_id = account1.id

        account2 = await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.GOOGLE_ADS,
            platform_account_id="123-456-7890",
            account_name="Updated Name",
        )

        assert account2.id == original_id
        assert account2.account_name == "Updated Name"

    async def test_get_accounts_for_user(self, db_session):
        await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.GOOGLE_ADS,
            platform_account_id="gads_1",
        )
        await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.META,
            platform_account_id="meta_1",
        )
        await service.upsert_account(
            db_session,
            user_id="user_2",
            platform=Platform.GOOGLE_ADS,
            platform_account_id="gads_2",
        )

        user1_accounts = await service.get_accounts_for_user(db_session, "user_1")
        assert len(user1_accounts) == 2

        user2_accounts = await service.get_accounts_for_user(db_session, "user_2")
        assert len(user2_accounts) == 1

    async def test_get_accounts_for_user_no_results(self, db_session):
        accounts = await service.get_accounts_for_user(db_session, "nonexistent")
        assert accounts == []

    async def test_get_account_by_platform_id(self, db_session):
        await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.META,
            platform_account_id="act_12345",
            account_name="Meta Account",
        )

        found = await service.get_account_by_platform_id(
            db_session, "user_1", Platform.META, "act_12345"
        )
        assert found is not None
        assert found.account_name == "Meta Account"

        not_found = await service.get_account_by_platform_id(
            db_session, "user_1", Platform.META, "act_99999"
        )
        assert not_found is None

    async def test_update_account_tokens(self, db_session):
        account = await _create_account(db_session)
        expires = datetime(2025, 6, 1, tzinfo=timezone.utc)

        await service.update_account_tokens(
            db_session,
            account_id=account.id,
            access_token="new_access_token",
            refresh_token="new_refresh_token",
            expires_at=expires,
        )

        # Re-fetch to verify
        found = await service.get_account_by_platform_id(
            db_session, "user_1", Platform.GOOGLE_ADS, "123-456-7890"
        )
        assert found.oauth_access_token == "new_access_token"
        assert found.oauth_refresh_token == "new_refresh_token"
        assert found.token_expires_at == expires

    async def test_update_account_tokens_partial(self, db_session):
        account = await _create_account(db_session)

        await service.update_account_tokens(
            db_session,
            account_id=account.id,
            access_token="only_access",
        )

        found = await service.get_account_by_platform_id(
            db_session, "user_1", Platform.GOOGLE_ADS, "123-456-7890"
        )
        assert found.oauth_access_token == "only_access"
        assert found.oauth_refresh_token is None

    async def test_update_account_tokens_not_found(self, db_session):
        # Should not raise, just log warning
        await service.update_account_tokens(
            db_session,
            account_id=9999,
            access_token="token",
        )


# ============================================================
# Analysis Results Tests
# ============================================================


class TestAnalysisResults:
    async def test_save_analysis_result(self, db_session):
        analysis = await _create_analysis(db_session)

        assert analysis.id is not None
        assert analysis.user_id == "user_1"
        assert analysis.run_date == date(2025, 1, 15)
        assert analysis.briefing_content == "Daily briefing content"
        assert analysis.recommendations == [{"action": "increase_budget", "campaign": "C1"}]
        assert analysis.accounts_analyzed == [1, 2]
        assert analysis.llm_cost_usd == Decimal("1.50")
        assert analysis.llm_input_tokens == 5000

    async def test_get_latest_analysis(self, db_session):
        await service.save_analysis_result(
            db_session,
            user_id="user_1",
            run_date=date(2025, 1, 10),
            briefing_content="Old briefing",
        )
        await service.save_analysis_result(
            db_session,
            user_id="user_1",
            run_date=date(2025, 1, 15),
            briefing_content="Latest briefing",
        )

        latest = await service.get_latest_analysis(db_session, "user_1")
        assert latest is not None
        assert latest.briefing_content == "Latest briefing"
        assert latest.run_date == date(2025, 1, 15)

    async def test_get_latest_analysis_no_results(self, db_session):
        result = await service.get_latest_analysis(db_session, "nonexistent")
        assert result is None

    async def test_get_analyses_for_period(self, db_session):
        for day in range(10, 20):
            await service.save_analysis_result(
                db_session,
                user_id="user_1",
                run_date=date(2025, 1, day),
                briefing_content=f"Briefing for Jan {day}",
            )

        results = await service.get_analyses_for_period(
            db_session,
            user_id="user_1",
            start_date=date(2025, 1, 12),
            end_date=date(2025, 1, 16),
        )
        assert len(results) == 5
        assert results[0].run_date == date(2025, 1, 12)
        assert results[-1].run_date == date(2025, 1, 16)

    async def test_get_analyses_for_period_no_results(self, db_session):
        results = await service.get_analyses_for_period(
            db_session,
            user_id="user_1",
            start_date=date(2030, 1, 1),
            end_date=date(2030, 12, 31),
        )
        assert results == []


# ============================================================
# Approval Queue Tests
# ============================================================


class TestApprovalQueue:
    async def _setup_approval(self, db_session):
        """Create prerequisite account + analysis, then return an approval."""
        account = await _create_account(db_session)
        analysis = await _create_analysis(db_session)
        approval = await service.create_approval(
            db_session,
            analysis_id=analysis.id,
            user_id="user_1",
            action_type=ActionType.BUDGET_CHANGE,
            account_id=account.id,
            description="Increase Search campaign budget by 20%",
            reasoning="CPA is well below target, headroom to scale.",
            action_params={"campaign_id": "camp_001", "new_budget": 1200.00},
            projected_impact="Estimated +15% conversions at similar CPA",
            risk_assessment="Moderate — watch for diminishing returns after 48h",
        )
        return account, analysis, approval

    async def test_create_approval(self, db_session):
        _, _, approval = await self._setup_approval(db_session)

        assert approval.id is not None
        assert approval.status == ApprovalStatus.PENDING
        assert approval.description == "Increase Search campaign budget by 20%"
        assert approval.action_params == {
            "campaign_id": "camp_001",
            "new_budget": 1200.00,
        }

    async def test_approve_item(self, db_session):
        _, _, approval = await self._setup_approval(db_session)

        updated = await service.update_approval_status(
            db_session,
            approval_id=approval.id,
            status=ApprovalStatus.APPROVED,
            decided_by="slack_user_abc",
        )

        assert updated is not None
        assert updated.status == ApprovalStatus.APPROVED
        assert updated.decided_by == "slack_user_abc"
        assert updated.decided_at is not None

    async def test_reject_item(self, db_session):
        _, _, approval = await self._setup_approval(db_session)

        updated = await service.update_approval_status(
            db_session,
            approval_id=approval.id,
            status=ApprovalStatus.REJECTED,
            decided_by="manager_1",
            rejection_reason="Too risky this week",
        )

        assert updated is not None
        assert updated.status == ApprovalStatus.REJECTED
        assert updated.rejection_reason == "Too risky this week"

    async def test_update_approval_not_found(self, db_session):
        result = await service.update_approval_status(
            db_session,
            approval_id=9999,
            status=ApprovalStatus.APPROVED,
            decided_by="nobody",
        )
        assert result is None

    async def test_get_pending_approvals(self, db_session):
        account = await _create_account(db_session)
        analysis = await _create_analysis(db_session)

        # Create 3 approvals
        for i in range(3):
            await service.create_approval(
                db_session,
                analysis_id=analysis.id,
                user_id="user_1",
                action_type=ActionType.BUDGET_CHANGE,
                account_id=account.id,
                description=f"Action {i}",
                reasoning="Test",
                action_params={"idx": i},
            )

        # Approve one of them
        approvals = await service.get_pending_approvals(db_session, "user_1")
        assert len(approvals) == 3

        await service.update_approval_status(
            db_session,
            approval_id=approvals[0].id,
            status=ApprovalStatus.APPROVED,
            decided_by="tester",
        )

        pending = await service.get_pending_approvals(db_session, "user_1")
        assert len(pending) == 2

    async def test_get_approval_by_id(self, db_session):
        _, _, approval = await self._setup_approval(db_session)

        found = await service.get_approval_by_id(db_session, approval.id)
        assert found is not None
        assert found.id == approval.id

        not_found = await service.get_approval_by_id(db_session, 9999)
        assert not_found is None

    async def test_expire_old_approvals(self, db_session):
        account = await _create_account(db_session)
        analysis = await _create_analysis(db_session)

        # Create an approval with a created_at in the past
        old_approval = await service.create_approval(
            db_session,
            analysis_id=analysis.id,
            user_id="user_1",
            action_type=ActionType.PAUSE_CAMPAIGN,
            account_id=account.id,
            description="Old action",
            reasoning="Test",
            action_params={},
        )
        # Manually backdate created_at
        old_approval.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

        # Create a recent approval
        await service.create_approval(
            db_session,
            analysis_id=analysis.id,
            user_id="user_1",
            action_type=ActionType.BID_CHANGE,
            account_id=account.id,
            description="Recent action",
            reasoning="Test",
            action_params={},
        )

        await db_session.flush()

        expired_count = await service.expire_old_approvals(db_session, hours=24)
        assert expired_count == 1

        # Verify the old one is expired
        found_old = await service.get_approval_by_id(db_session, old_approval.id)
        assert found_old.status == ApprovalStatus.EXPIRED

    async def test_expire_no_old_approvals(self, db_session):
        count = await service.expire_old_approvals(db_session, hours=24)
        assert count == 0


# ============================================================
# Audit Log Tests
# ============================================================


class TestAuditLog:
    async def test_log_event(self, db_session):
        entry = await service.log_event(
            db_session,
            user_id="user_1",
            event_type="analysis_run",
            event_data={"accounts": [1, 2], "duration": 12.5},
            source="daily_briefing",
            agent_model="claude-sonnet-4-5-20250929",
        )

        assert entry.id is not None
        assert entry.user_id == "user_1"
        assert entry.event_type == "analysis_run"
        assert entry.event_data == {"accounts": [1, 2], "duration": 12.5}
        assert entry.source == "daily_briefing"
        assert entry.agent_model == "claude-sonnet-4-5-20250929"
        assert entry.required_approval is False

    async def test_log_event_with_approval(self, db_session):
        account = await _create_account(db_session)
        entry = await service.log_event(
            db_session,
            user_id="user_1",
            event_type="action_executed",
            account_id=account.id,
            required_approval=True,
            approval_status="approved",
            approved_by="slack_user_xyz",
        )

        assert entry.required_approval is True
        assert entry.approval_status == "approved"
        assert entry.approved_by == "slack_user_xyz"
        assert entry.account_id == account.id

    async def test_get_audit_trail(self, db_session):
        for i in range(5):
            await service.log_event(
                db_session,
                user_id="user_1",
                event_type="analysis_run" if i % 2 == 0 else "recommendation",
                event_data={"idx": i},
            )

        trail = await service.get_audit_trail(db_session, "user_1")
        assert len(trail) == 5

    async def test_get_audit_trail_with_limit(self, db_session):
        for i in range(10):
            await service.log_event(
                db_session,
                user_id="user_1",
                event_type="analysis_run",
            )

        trail = await service.get_audit_trail(db_session, "user_1", limit=3)
        assert len(trail) == 3

    async def test_get_audit_trail_filter_by_event_type(self, db_session):
        for i in range(5):
            await service.log_event(
                db_session,
                user_id="user_1",
                event_type="analysis_run" if i % 2 == 0 else "recommendation",
            )

        analysis_trail = await service.get_audit_trail(
            db_session, "user_1", event_type="analysis_run"
        )
        assert len(analysis_trail) == 3  # indices 0, 2, 4

        rec_trail = await service.get_audit_trail(db_session, "user_1", event_type="recommendation")
        assert len(rec_trail) == 2  # indices 1, 3

    async def test_get_audit_trail_no_results(self, db_session):
        trail = await service.get_audit_trail(db_session, "nonexistent")
        assert trail == []


# ============================================================
# Cost Tracking Tests
# ============================================================


class TestCostTracking:
    async def test_record_cost(self, db_session):
        entry = await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=date(2025, 1, 15),
            model="claude-sonnet-4-5-20250929",
            cost_usd=Decimal("0.0350"),
            input_tokens=10000,
            output_tokens=2000,
            operation="daily_analysis",
        )

        assert entry.id is not None
        assert entry.cost_usd == Decimal("0.0350")
        assert entry.model == "claude-sonnet-4-5-20250929"
        assert entry.input_tokens == 10000
        assert entry.output_tokens == 2000
        assert entry.operation == "daily_analysis"

    async def test_get_daily_cost(self, db_session):
        today = date(2025, 1, 15)

        await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=today,
            model="claude-haiku-4-5-20251001",
            cost_usd=Decimal("0.0100"),
        )
        await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=today,
            model="claude-sonnet-4-5-20250929",
            cost_usd=Decimal("0.0500"),
        )
        await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=today,
            model="claude-opus-4-6",
            cost_usd=Decimal("0.2000"),
        )

        total = await service.get_daily_cost(db_session, "user_1", today)
        assert total == Decimal("0.2600")

    async def test_get_daily_cost_no_entries(self, db_session):
        total = await service.get_daily_cost(db_session, "user_1", date(2030, 1, 1))
        assert total == Decimal("0")

    async def test_get_daily_cost_all_users(self, db_session):
        today = date(2025, 1, 15)

        await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=today,
            model="claude-sonnet-4-5-20250929",
            cost_usd=Decimal("1.0000"),
        )
        await service.record_cost(
            db_session,
            user_id="user_2",
            run_date=today,
            model="claude-sonnet-4-5-20250929",
            cost_usd=Decimal("2.5000"),
        )
        await service.record_cost(
            db_session,
            user_id="user_3",
            run_date=today,
            model="claude-haiku-4-5-20251001",
            cost_usd=Decimal("0.5000"),
        )

        total = await service.get_daily_cost_all_users(db_session, today)
        assert total == Decimal("4.0000")

    async def test_get_daily_cost_all_users_no_entries(self, db_session):
        total = await service.get_daily_cost_all_users(db_session, date(2030, 1, 1))
        assert total == Decimal("0")

    async def test_cost_isolation_between_dates(self, db_session):
        await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=date(2025, 1, 15),
            model="claude-sonnet-4-5-20250929",
            cost_usd=Decimal("1.0000"),
        )
        await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=date(2025, 1, 16),
            model="claude-sonnet-4-5-20250929",
            cost_usd=Decimal("2.0000"),
        )

        day1 = await service.get_daily_cost(db_session, "user_1", date(2025, 1, 15))
        day2 = await service.get_daily_cost(db_session, "user_1", date(2025, 1, 16))
        assert day1 == Decimal("1.0000")
        assert day2 == Decimal("2.0000")


# ============================================================
# Campaign & Metrics Tests
# ============================================================


class TestCampaignsAndMetrics:
    async def test_upsert_campaign_create(self, db_session):
        account = await _create_account(db_session)
        campaign = await service.upsert_campaign(
            db_session,
            account_id=account.id,
            platform=Platform.GOOGLE_ADS,
            platform_campaign_id="camp_001",
            campaign_name="Search - Brand",
            campaign_type="search",
            status="enabled",
            daily_budget=Decimal("100.00"),
        )

        assert campaign.id is not None
        assert campaign.campaign_name == "Search - Brand"
        assert campaign.status == "enabled"

    async def test_upsert_campaign_update(self, db_session):
        account = await _create_account(db_session)

        campaign1 = await service.upsert_campaign(
            db_session,
            account_id=account.id,
            platform=Platform.GOOGLE_ADS,
            platform_campaign_id="camp_001",
            campaign_name="Original Name",
            status="enabled",
        )
        original_id = campaign1.id

        campaign2 = await service.upsert_campaign(
            db_session,
            account_id=account.id,
            platform=Platform.GOOGLE_ADS,
            platform_campaign_id="camp_001",
            campaign_name="Updated Name",
            status="paused",
        )

        assert campaign2.id == original_id
        assert campaign2.campaign_name == "Updated Name"
        assert campaign2.status == "paused"

    async def test_save_daily_metrics_create(self, db_session):
        account = await _create_account(db_session)
        campaign = await _create_campaign(db_session, account.id)

        metric = await service.save_daily_metrics(
            db_session,
            campaign_id=campaign.id,
            metric_date=date(2025, 1, 15),
            metrics_dict={
                "impressions": 10000,
                "clicks": 500,
                "cost": Decimal("250.00"),
                "conversions": 25.0,
                "conversion_value": Decimal("2500.00"),
                "ctr": 0.05,
                "cpc": Decimal("0.50"),
                "cpa": Decimal("10.00"),
                "roas": 10.0,
            },
        )

        assert metric.id is not None
        assert metric.impressions == 10000
        assert metric.clicks == 500
        assert metric.roas == 10.0

    async def test_save_daily_metrics_upsert(self, db_session):
        account = await _create_account(db_session)
        campaign = await _create_campaign(db_session, account.id)

        metric1 = await service.save_daily_metrics(
            db_session,
            campaign_id=campaign.id,
            metric_date=date(2025, 1, 15),
            metrics_dict={"impressions": 10000, "clicks": 500},
        )
        original_id = metric1.id

        metric2 = await service.save_daily_metrics(
            db_session,
            campaign_id=campaign.id,
            metric_date=date(2025, 1, 15),
            metrics_dict={"impressions": 12000, "clicks": 600},
        )

        assert metric2.id == original_id
        assert metric2.impressions == 12000
        assert metric2.clicks == 600


# ============================================================
# Edge Case / Integration Tests
# ============================================================


class TestEdgeCases:
    async def test_multiple_platforms_same_user(self, db_session):
        await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.GOOGLE_ADS,
            platform_account_id="gads_1",
        )
        await service.upsert_account(
            db_session,
            user_id="user_1",
            platform=Platform.META,
            platform_account_id="meta_1",
        )

        accounts = await service.get_accounts_for_user(db_session, "user_1")
        assert len(accounts) == 2
        platforms = {a.platform for a in accounts}
        assert platforms == {Platform.GOOGLE_ADS, Platform.META}

    async def test_full_approval_lifecycle(self, db_session):
        """Test the complete lifecycle: create -> query pending -> approve -> verify."""
        account = await _create_account(db_session)
        analysis = await _create_analysis(db_session)

        # 1. Create
        approval = await service.create_approval(
            db_session,
            analysis_id=analysis.id,
            user_id="user_1",
            action_type=ActionType.BUDGET_CHANGE,
            account_id=account.id,
            description="Scale budget",
            reasoning="Good performance",
            action_params={"new_budget": 1500},
        )
        assert approval.status == ApprovalStatus.PENDING

        # 2. Query pending
        pending = await service.get_pending_approvals(db_session, "user_1")
        assert len(pending) == 1

        # 3. Approve
        updated = await service.update_approval_status(
            db_session,
            approval_id=approval.id,
            status=ApprovalStatus.APPROVED,
            decided_by="manager",
        )
        assert updated.status == ApprovalStatus.APPROVED

        # 4. No more pending
        pending_after = await service.get_pending_approvals(db_session, "user_1")
        assert len(pending_after) == 0

        # 5. Log the action
        log_entry = await service.log_event(
            db_session,
            user_id="user_1",
            event_type="action_executed",
            event_data={"approval_id": approval.id, "action": "budget_change"},
            required_approval=True,
            approval_status="approved",
            approved_by="manager",
        )
        assert log_entry.required_approval is True

    async def test_cost_with_account_id(self, db_session):
        account = await _create_account(db_session)
        entry = await service.record_cost(
            db_session,
            user_id="user_1",
            run_date=date(2025, 1, 15),
            model="claude-sonnet-4-5-20250929",
            cost_usd=Decimal("0.50"),
            account_id=account.id,
            operation="budget_optimization",
        )
        assert entry.account_id == account.id
        assert entry.operation == "budget_optimization"
