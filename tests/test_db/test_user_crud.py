"""Tests for User CRUD operations in the DB service layer.

Covers:
- create_user with different roles
- get_user and get_user_role lookups
- update_user_role
- deactivate_user (soft delete)
- list_users with filters
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.schema import User, UserRole


class TestCreateUser:
    """Test user creation."""

    @pytest.mark.asyncio
    async def test_creates_user_with_defaults(self):
        from src.db.service import create_user

        session = AsyncMock()

        await create_user(session, "U123")

        # Verify session.add was called with a User
        session.add.assert_called_once()
        added_user = session.add.call_args[0][0]
        assert isinstance(added_user, User)
        assert added_user.user_id == "U123"
        assert added_user.role == UserRole.APPROVER
        assert added_user.is_active is True
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_creates_admin_user(self):
        from src.db.service import create_user

        session = AsyncMock()

        await create_user(
            session,
            "U_ADMIN",
            display_name="Admin User",
            email="admin@example.com",
            role="admin",
            created_by="U_SETUP",
        )

        added_user = session.add.call_args[0][0]
        assert added_user.role == UserRole.ADMIN
        assert added_user.display_name == "Admin User"
        assert added_user.email == "admin@example.com"
        assert added_user.created_by == "U_SETUP"


class TestGetUser:
    """Test user lookups."""

    @pytest.mark.asyncio
    async def test_get_existing_user(self):
        from src.db.service import get_user

        mock_user = MagicMock(spec=User)
        mock_user.user_id = "U123"

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_user
        session.execute.return_value = mock_result

        result = await get_user(session, "U123")
        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_missing_user_returns_none(self):
        from src.db.service import get_user

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        session.execute.return_value = mock_result

        result = await get_user(session, "U_NONEXISTENT")
        assert result is None


class TestGetUserRole:
    """Test fast-path role lookup."""

    @pytest.mark.asyncio
    async def test_returns_role_string(self):
        from src.db.service import get_user_role

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = UserRole.ADMIN
        session.execute.return_value = mock_result

        role = await get_user_role(session, "U_ADMIN")
        assert role == "admin"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_user(self):
        from src.db.service import get_user_role

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        role = await get_user_role(session, "U_MISSING")
        assert role is None


class TestUpdateUserRole:
    """Test role updates."""

    @pytest.mark.asyncio
    async def test_updates_existing_user(self):
        from src.db.service import update_user_role

        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.VIEWER

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_user
        session.execute.return_value = mock_result

        result = await update_user_role(session, "U123", "admin")
        assert result == mock_user
        assert mock_user.role == UserRole.ADMIN
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_user(self):
        from src.db.service import update_user_role

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        session.execute.return_value = mock_result

        result = await update_user_role(session, "U_MISSING", "admin")
        assert result is None


class TestDeactivateUser:
    """Test user deactivation (soft delete)."""

    @pytest.mark.asyncio
    async def test_deactivates_existing_user(self):
        from src.db.service import deactivate_user

        mock_user = MagicMock(spec=User)
        mock_user.is_active = True

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_user
        session.execute.return_value = mock_result

        result = await deactivate_user(session, "U123")
        assert result is True
        assert mock_user.is_active is False
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_for_missing_user(self):
        from src.db.service import deactivate_user

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        session.execute.return_value = mock_result

        result = await deactivate_user(session, "U_MISSING")
        assert result is False


class TestListUsers:
    """Test user listing with filters."""

    @pytest.mark.asyncio
    async def test_list_all_active_users(self):
        from src.db.service import list_users

        user1 = MagicMock(spec=User)
        user2 = MagicMock(spec=User)

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [user1, user2]
        session.execute.return_value = mock_result

        result = await list_users(session)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_by_role(self):
        from src.db.service import list_users

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result

        # Should not raise even with role filter
        result = await list_users(session, role="admin")
        assert result == []
