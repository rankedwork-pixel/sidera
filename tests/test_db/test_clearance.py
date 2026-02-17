"""Tests for clearance-related DB service methods.

Covers:
- get_user_clearance
- update_user_clearance
- create_user with clearance
- get_agent_relationship_memories
- save_memory with source_role_id
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================
# get_user_clearance
# ============================================================


class TestGetUserClearance:
    @pytest.mark.asyncio
    async def test_returns_clearance_when_user_exists(self):
        from src.db.service import get_user_clearance

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "confidential"
        mock_session.execute.return_value = mock_result

        result = await get_user_clearance(mock_session, "U_CEO")
        assert result == "confidential"

    @pytest.mark.asyncio
    async def test_returns_none_when_user_not_found(self):
        from src.db.service import get_user_clearance

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await get_user_clearance(mock_session, "U_NOBODY")
        assert result is None


# ============================================================
# update_user_clearance
# ============================================================


class TestUpdateUserClearance:
    @pytest.mark.asyncio
    async def test_update_existing_user(self):
        from src.db.service import update_user_clearance

        mock_user = MagicMock()
        mock_user.clearance_level = MagicMock()
        mock_user.clearance_level.value = "public"

        mock_session = AsyncMock()

        with patch(
            "src.db.service.get_user",
            new_callable=AsyncMock,
            return_value=mock_user,
        ):
            result = await update_user_clearance(
                mock_session, "U1", "restricted", changed_by="admin"
            )
            assert result is not None
            assert result == mock_user

    @pytest.mark.asyncio
    async def test_update_returns_none_for_missing_user(self):
        from src.db.service import update_user_clearance

        mock_session = AsyncMock()

        with patch(
            "src.db.service.get_user",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await update_user_clearance(
                mock_session, "U_MISSING", "restricted", changed_by="admin"
            )
            assert result is None


# ============================================================
# create_user with clearance
# ============================================================


class TestCreateUserWithClearance:
    @pytest.mark.asyncio
    async def test_create_user_accepts_clearance(self):
        """create_user should accept and use the clearance_level parameter."""
        from src.db.service import create_user

        mock_session = AsyncMock()

        # Patch _log_org_chart_change to avoid side effects
        with patch("src.db.service._log_org_chart_change", new_callable=AsyncMock):
            await create_user(
                mock_session,
                "U_NEW",
                display_name="Test User",
                role="admin",
                created_by="system",
                clearance_level="restricted",
            )

        # Verify the user was created (session.add was called)
        mock_session.add.assert_called_once()
        added_user = mock_session.add.call_args[0][0]
        # Check clearance was set
        cl = added_user.clearance_level
        cl_val = cl.value if hasattr(cl, "value") else str(cl)
        assert cl_val == "restricted"


# ============================================================
# get_agent_relationship_memories
# ============================================================


class TestGetAgentRelationshipMemories:
    @pytest.mark.asyncio
    async def test_returns_memories_with_source_role_id(self):
        from src.db.service import get_agent_relationship_memories

        fake_mem1 = MagicMock()
        fake_mem1.source_role_id = "performance_media_buyer"
        fake_mem1.role_id = "head_of_marketing"
        fake_mem1.memory_type = "relationship"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [fake_mem1]
        mock_session.execute.return_value = mock_result

        result = await get_agent_relationship_memories(mock_session, "head_of_marketing", limit=5)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_memories(self):
        from src.db.service import get_agent_relationship_memories

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await get_agent_relationship_memories(mock_session, "head_of_it", limit=5)
        assert result == []


# ============================================================
# save_memory with source_role_id
# ============================================================


class TestSaveMemoryWithSourceRoleId:
    @pytest.mark.asyncio
    async def test_save_memory_passes_source_role_id(self):
        """save_memory should set source_role_id on the RoleMemory object."""
        from src.db.service import save_memory

        mock_session = AsyncMock()

        await save_memory(
            session=mock_session,
            user_id="__system__",
            role_id="head_of_marketing",
            department_id="marketing",
            memory_type="relationship",
            title="Delegated budget analysis to media buyer",
            content="Sent delegation task to performance_media_buyer",
            confidence=0.6,
            source_role_id="performance_media_buyer",
        )

        # Verify session.add was called
        mock_session.add.assert_called_once()
        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.source_role_id == "performance_media_buyer"

    @pytest.mark.asyncio
    async def test_save_memory_without_source_role_id(self):
        """save_memory without source_role_id should default to None."""
        from src.db.service import save_memory

        mock_session = AsyncMock()

        await save_memory(
            session=mock_session,
            user_id="user1",
            role_id="head_of_it",
            department_id="it",
            memory_type="insight",
            title="System is healthy",
            content="All checks passed",
            confidence=0.9,
        )

        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.source_role_id is None
