"""Tests for Org Chart CRUD methods in the database service layer.

Tests the 15 new CRUD methods (5 each for departments, roles, skills)
plus audit trail verification. Uses SQLite in-memory (async with aiosqlite)
as the test database, matching the pattern from test_service.py.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import service
from src.models.schema import Base


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
# Helper to get audit log entries for org chart changes
# ============================================================


async def _get_org_chart_audit_entries(session):
    """Return all audit log entries with event_type='org_chart_change'."""
    return await service.get_audit_trail(session, "system", event_type="org_chart_change")


# ============================================================
# Department Tests
# ============================================================


class TestCreateOrgDepartment:
    async def test_create_happy_path(self, db_session):
        """Creating a department stores all fields correctly."""
        dept = await service.create_org_department(
            db_session,
            dept_id="engineering",
            name="Engineering",
            description="Software engineering department",
            context="Build robust systems.",
            context_text="Pre-rendered engineering context.",
            created_by="admin_user",
        )

        assert dept.id is not None
        assert dept.dept_id == "engineering"
        assert dept.name == "Engineering"
        assert dept.description == "Software engineering department"
        assert dept.context == "Build robust systems."
        assert dept.context_text == "Pre-rendered engineering context."
        assert dept.created_by == "admin_user"
        assert dept.is_active is True

    async def test_create_minimal_fields(self, db_session):
        """Creating a department with only required fields uses defaults."""
        dept = await service.create_org_department(
            db_session,
            dept_id="sales",
            name="Sales",
            description="Sales department",
        )

        assert dept.dept_id == "sales"
        assert dept.context == ""
        assert dept.context_text == ""
        assert dept.created_by == ""
        assert dept.is_active is True

    async def test_create_duplicate_dept_id_raises(self, db_session):
        """Creating two departments with the same dept_id raises IntegrityError."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Marketing department",
        )

        with pytest.raises(IntegrityError):
            await service.create_org_department(
                db_session,
                dept_id="marketing",
                name="Marketing V2",
                description="Duplicate marketing department",
            )


class TestUpdateOrgDepartment:
    async def test_update_happy_path(self, db_session):
        """Updating a department changes the specified fields."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Old description",
        )

        updated = await service.update_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing & Growth",
            description="Updated description",
            context="New context text.",
        )

        assert updated is not None
        assert updated.name == "Marketing & Growth"
        assert updated.description == "Updated description"
        assert updated.context == "New context text."

    async def test_update_returns_none_for_nonexistent(self, db_session):
        """Updating a nonexistent department returns None."""
        result = await service.update_org_department(
            db_session,
            dept_id="nonexistent_dept",
            name="New Name",
        )
        assert result is None

    async def test_update_does_not_change_protected_fields(self, db_session):
        """Update ignores protected fields like id and dept_id."""
        dept = await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Description",
        )
        original_id = dept.id

        updated = await service.update_org_department(
            db_session,
            dept_id="marketing",
            id=9999,
            dept_id_new="hacked",  # not a real field
            name="Updated Marketing",
        )

        assert updated is not None
        assert updated.id == original_id
        assert updated.dept_id == "marketing"
        assert updated.name == "Updated Marketing"


class TestDeleteOrgDepartment:
    async def test_soft_delete_sets_inactive(self, db_session):
        """Soft-deleting sets is_active to False."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Marketing department",
        )

        deleted = await service.delete_org_department(db_session, "marketing")
        assert deleted is not None
        assert deleted.is_active is False

    async def test_delete_returns_none_for_nonexistent(self, db_session):
        """Deleting a nonexistent department returns None."""
        result = await service.delete_org_department(db_session, "nonexistent_dept")
        assert result is None

    async def test_soft_deleted_dept_still_exists(self, db_session):
        """Soft-deleted department can still be fetched by get_org_department."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Marketing department",
        )
        await service.delete_org_department(db_session, "marketing")

        # get_org_department does not filter by is_active
        fetched = await service.get_org_department(db_session, "marketing")
        assert fetched is not None
        assert fetched.is_active is False


class TestGetOrgDepartment:
    async def test_get_happy_path(self, db_session):
        """Fetching an existing department returns it."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Marketing department",
        )

        dept = await service.get_org_department(db_session, "marketing")
        assert dept is not None
        assert dept.dept_id == "marketing"
        assert dept.name == "Marketing"

    async def test_get_returns_none_for_nonexistent(self, db_session):
        """Fetching a nonexistent department returns None."""
        result = await service.get_org_department(db_session, "nonexistent")
        assert result is None


class TestListOrgDepartments:
    async def test_list_active_only(self, db_session):
        """active_only=True filters out soft-deleted departments."""
        await service.create_org_department(
            db_session, dept_id="dept_a", name="Dept A", description="Active"
        )
        await service.create_org_department(
            db_session, dept_id="dept_b", name="Dept B", description="To delete"
        )
        await service.delete_org_department(db_session, "dept_b")

        active = await service.list_org_departments(db_session, active_only=True)
        assert len(active) == 1
        assert active[0].dept_id == "dept_a"

    async def test_list_includes_inactive(self, db_session):
        """active_only=False includes soft-deleted departments."""
        await service.create_org_department(
            db_session, dept_id="dept_a", name="Dept A", description="Active"
        )
        await service.create_org_department(
            db_session, dept_id="dept_b", name="Dept B", description="To delete"
        )
        await service.delete_org_department(db_session, "dept_b")

        all_depts = await service.list_org_departments(db_session, active_only=False)
        assert len(all_depts) == 2

    async def test_list_sorted_by_dept_id(self, db_session):
        """Results are sorted alphabetically by dept_id."""
        await service.create_org_department(
            db_session, dept_id="zebra", name="Zebra", description="Z dept"
        )
        await service.create_org_department(
            db_session, dept_id="alpha", name="Alpha", description="A dept"
        )

        depts = await service.list_org_departments(db_session)
        assert depts[0].dept_id == "alpha"
        assert depts[1].dept_id == "zebra"

    async def test_list_empty(self, db_session):
        """Listing departments from an empty table returns empty list."""
        result = await service.list_org_departments(db_session)
        assert result == []


# ============================================================
# Role Tests
# ============================================================


class TestCreateOrgRole:
    async def test_create_happy_path(self, db_session):
        """Creating a role stores all fields correctly."""
        role = await service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
            persona="You are a media buying expert.",
            connectors=["google_ads", "meta"],
            briefing_skills=["budget_analysis", "creative_review"],
            schedule="0 8 * * *",
            context_text="Role context here.",
            manages=["junior_buyer"],
            delegation_model="fast",
            synthesis_prompt="Synthesize results.",
            created_by="admin",
        )

        assert role.id is not None
        assert role.role_id == "media_buyer"
        assert role.name == "Media Buyer"
        assert role.department_id == "marketing"
        assert role.description == "Buys media"
        assert role.persona == "You are a media buying expert."
        assert role.connectors == ["google_ads", "meta"]
        assert role.briefing_skills == ["budget_analysis", "creative_review"]
        assert role.schedule == "0 8 * * *"
        assert role.context_text == "Role context here."
        assert role.manages == ["junior_buyer"]
        assert role.delegation_model == "fast"
        assert role.synthesis_prompt == "Synthesize results."
        assert role.is_active is True

    async def test_create_minimal_fields(self, db_session):
        """Creating a role with minimal fields uses defaults."""
        role = await service.create_org_role(
            db_session,
            role_id="analyst",
            name="Analyst",
            department_id="marketing",
            description="Analyzes data",
        )

        assert role.connectors == []
        assert role.briefing_skills == []
        assert role.schedule is None
        assert role.manages == []
        assert role.delegation_model == "standard"
        assert role.synthesis_prompt == ""

    async def test_create_duplicate_role_id_raises(self, db_session):
        """Creating two roles with the same role_id raises IntegrityError."""
        await service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Buys media",
        )

        with pytest.raises(IntegrityError):
            await service.create_org_role(
                db_session,
                role_id="media_buyer",
                name="Media Buyer V2",
                department_id="marketing",
                description="Duplicate",
            )


class TestUpdateOrgRole:
    async def test_update_happy_path(self, db_session):
        """Updating a role changes the specified fields."""
        await service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Original description",
        )

        updated = await service.update_org_role(
            db_session,
            role_id="media_buyer",
            name="Senior Media Buyer",
            description="Updated description",
            persona="Updated persona.",
            manages=["junior_buyer"],
        )

        assert updated is not None
        assert updated.name == "Senior Media Buyer"
        assert updated.description == "Updated description"
        assert updated.persona == "Updated persona."
        assert updated.manages == ["junior_buyer"]

    async def test_update_returns_none_for_nonexistent(self, db_session):
        """Updating a nonexistent role returns None."""
        result = await service.update_org_role(
            db_session,
            role_id="nonexistent_role",
            name="New Name",
        )
        assert result is None


class TestDeleteOrgRole:
    async def test_soft_delete_sets_inactive(self, db_session):
        """Soft-deleting sets is_active to False."""
        await service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Description",
        )

        deleted = await service.delete_org_role(db_session, "media_buyer")
        assert deleted is not None
        assert deleted.is_active is False

    async def test_delete_returns_none_for_nonexistent(self, db_session):
        """Deleting a nonexistent role returns None."""
        result = await service.delete_org_role(db_session, "nonexistent_role")
        assert result is None


class TestGetOrgRole:
    async def test_get_happy_path(self, db_session):
        """Fetching an existing role returns it."""
        await service.create_org_role(
            db_session,
            role_id="media_buyer",
            name="Media Buyer",
            department_id="marketing",
            description="Description",
        )

        role = await service.get_org_role(db_session, "media_buyer")
        assert role is not None
        assert role.role_id == "media_buyer"
        assert role.name == "Media Buyer"

    async def test_get_returns_none_for_nonexistent(self, db_session):
        """Fetching a nonexistent role returns None."""
        result = await service.get_org_role(db_session, "nonexistent")
        assert result is None


class TestListOrgRoles:
    async def test_list_active_only(self, db_session):
        """active_only=True filters out soft-deleted roles."""
        await service.create_org_role(
            db_session,
            role_id="role_a",
            name="A",
            department_id="marketing",
            description="Active",
        )
        await service.create_org_role(
            db_session,
            role_id="role_b",
            name="B",
            department_id="marketing",
            description="To delete",
        )
        await service.delete_org_role(db_session, "role_b")

        active = await service.list_org_roles(db_session, active_only=True)
        assert len(active) == 1
        assert active[0].role_id == "role_a"

    async def test_list_includes_inactive(self, db_session):
        """active_only=False includes soft-deleted roles."""
        await service.create_org_role(
            db_session,
            role_id="role_a",
            name="A",
            department_id="marketing",
            description="Active",
        )
        await service.create_org_role(
            db_session,
            role_id="role_b",
            name="B",
            department_id="marketing",
            description="To delete",
        )
        await service.delete_org_role(db_session, "role_b")

        all_roles = await service.list_org_roles(db_session, active_only=False)
        assert len(all_roles) == 2

    async def test_list_filtered_by_department_id(self, db_session):
        """department_id filter returns only roles in that department."""
        await service.create_org_role(
            db_session,
            role_id="mkt_buyer",
            name="Buyer",
            department_id="marketing",
            description="Marketing buyer",
        )
        await service.create_org_role(
            db_session,
            role_id="eng_dev",
            name="Dev",
            department_id="engineering",
            description="Engineer",
        )

        mkt_roles = await service.list_org_roles(
            db_session,
            department_id="marketing",
        )
        assert len(mkt_roles) == 1
        assert mkt_roles[0].role_id == "mkt_buyer"

        eng_roles = await service.list_org_roles(
            db_session,
            department_id="engineering",
        )
        assert len(eng_roles) == 1
        assert eng_roles[0].role_id == "eng_dev"

    async def test_list_sorted_by_role_id(self, db_session):
        """Results are sorted alphabetically by role_id."""
        await service.create_org_role(
            db_session,
            role_id="zebra_role",
            name="Zebra",
            department_id="marketing",
            description="Z",
        )
        await service.create_org_role(
            db_session,
            role_id="alpha_role",
            name="Alpha",
            department_id="marketing",
            description="A",
        )

        roles = await service.list_org_roles(db_session)
        assert roles[0].role_id == "alpha_role"
        assert roles[1].role_id == "zebra_role"

    async def test_list_empty(self, db_session):
        """Listing roles from an empty table returns empty list."""
        result = await service.list_org_roles(db_session)
        assert result == []


# ============================================================
# Skill Tests
# ============================================================


class TestCreateOrgSkill:
    async def test_create_happy_path(self, db_session):
        """Creating a skill stores all fields correctly."""
        skill = await service.create_org_skill(
            db_session,
            skill_id="budget_analysis",
            name="Budget Analysis",
            description="Analyzes budget allocation",
            category="analysis",
            system_supplement="Analyze budget carefully.",
            prompt_template="Review budget for {account}.",
            output_format="## Budget Analysis\nResults here.",
            business_guidance="Follow ROI guidelines.",
            platforms=["google_ads", "meta"],
            tags=["budget", "analysis"],
            tools_required=["get_google_ads_performance"],
            model="sonnet",
            max_turns=15,
            context_text="Extra budget context.",
            schedule="0 9 * * *",
            chain_after="data_pull",
            requires_approval=False,
            department_id="marketing",
            role_id="media_buyer",
            author="test_author",
            created_by="admin",
        )

        assert skill.id is not None
        assert skill.skill_id == "budget_analysis"
        assert skill.name == "Budget Analysis"
        assert skill.description == "Analyzes budget allocation"
        assert skill.category == "analysis"
        assert skill.platforms == ["google_ads", "meta"]
        assert skill.tags == ["budget", "analysis"]
        assert skill.tools_required == ["get_google_ads_performance"]
        assert skill.model == "sonnet"
        assert skill.max_turns == 15
        assert skill.context_text == "Extra budget context."
        assert skill.schedule == "0 9 * * *"
        assert skill.chain_after == "data_pull"
        assert skill.requires_approval is False
        assert skill.department_id == "marketing"
        assert skill.role_id == "media_buyer"
        assert skill.author == "test_author"
        assert skill.is_active is True

    async def test_create_minimal_fields(self, db_session):
        """Creating a skill with only required fields uses defaults."""
        skill = await service.create_org_skill(
            db_session,
            skill_id="simple_skill",
            name="Simple",
            description="Simple skill",
            category="analysis",
            system_supplement="System supplement.",
            prompt_template="Run analysis.",
            output_format="Show results.",
            business_guidance="Be careful.",
        )

        assert skill.platforms == []
        assert skill.tags == []
        assert skill.tools_required == []
        assert skill.model == "sonnet"
        assert skill.max_turns == 20
        assert skill.schedule is None
        assert skill.chain_after is None
        assert skill.requires_approval is True
        assert skill.department_id == ""
        assert skill.role_id == ""

    async def test_create_duplicate_skill_id_raises(self, db_session):
        """Creating two skills with the same skill_id raises IntegrityError."""
        await service.create_org_skill(
            db_session,
            skill_id="budget_analysis",
            name="Budget Analysis",
            description="Description",
            category="analysis",
            system_supplement="Supplement.",
            prompt_template="Template.",
            output_format="Format.",
            business_guidance="Guidance.",
        )

        with pytest.raises(IntegrityError):
            await service.create_org_skill(
                db_session,
                skill_id="budget_analysis",
                name="Budget Analysis V2",
                description="Duplicate",
                category="analysis",
                system_supplement="Supplement.",
                prompt_template="Template.",
                output_format="Format.",
                business_guidance="Guidance.",
            )


class TestUpdateOrgSkill:
    async def test_update_happy_path(self, db_session):
        """Updating a skill changes the specified fields."""
        await service.create_org_skill(
            db_session,
            skill_id="budget_analysis",
            name="Budget Analysis",
            description="Original",
            category="analysis",
            system_supplement="Original supplement.",
            prompt_template="Original template.",
            output_format="Original format.",
            business_guidance="Original guidance.",
        )

        updated = await service.update_org_skill(
            db_session,
            skill_id="budget_analysis",
            name="Budget Analysis V2",
            description="Updated description",
            model="opus",
            max_turns=30,
        )

        assert updated is not None
        assert updated.name == "Budget Analysis V2"
        assert updated.description == "Updated description"
        assert updated.model == "opus"
        assert updated.max_turns == 30

    async def test_update_returns_none_for_nonexistent(self, db_session):
        """Updating a nonexistent skill returns None."""
        result = await service.update_org_skill(
            db_session,
            skill_id="nonexistent_skill",
            name="New Name",
        )
        assert result is None


class TestDeleteOrgSkill:
    async def test_soft_delete_sets_inactive(self, db_session):
        """Soft-deleting sets is_active to False."""
        await service.create_org_skill(
            db_session,
            skill_id="budget_analysis",
            name="Budget Analysis",
            description="Description",
            category="analysis",
            system_supplement="Supplement.",
            prompt_template="Template.",
            output_format="Format.",
            business_guidance="Guidance.",
        )

        deleted = await service.delete_org_skill(db_session, "budget_analysis")
        assert deleted is not None
        assert deleted.is_active is False

    async def test_delete_returns_none_for_nonexistent(self, db_session):
        """Deleting a nonexistent skill returns None."""
        result = await service.delete_org_skill(db_session, "nonexistent_skill")
        assert result is None


class TestGetOrgSkill:
    async def test_get_happy_path(self, db_session):
        """Fetching an existing skill returns it."""
        await service.create_org_skill(
            db_session,
            skill_id="budget_analysis",
            name="Budget Analysis",
            description="Description",
            category="analysis",
            system_supplement="Supplement.",
            prompt_template="Template.",
            output_format="Format.",
            business_guidance="Guidance.",
        )

        skill = await service.get_org_skill(db_session, "budget_analysis")
        assert skill is not None
        assert skill.skill_id == "budget_analysis"
        assert skill.name == "Budget Analysis"

    async def test_get_returns_none_for_nonexistent(self, db_session):
        """Fetching a nonexistent skill returns None."""
        result = await service.get_org_skill(db_session, "nonexistent")
        assert result is None


class TestListOrgSkills:
    async def test_list_active_only(self, db_session):
        """active_only=True filters out soft-deleted skills."""
        await service.create_org_skill(
            db_session,
            skill_id="skill_a",
            name="A",
            description="Active",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
        )
        await service.create_org_skill(
            db_session,
            skill_id="skill_b",
            name="B",
            description="To delete",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
        )
        await service.delete_org_skill(db_session, "skill_b")

        active = await service.list_org_skills(db_session, active_only=True)
        assert len(active) == 1
        assert active[0].skill_id == "skill_a"

    async def test_list_includes_inactive(self, db_session):
        """active_only=False includes soft-deleted skills."""
        await service.create_org_skill(
            db_session,
            skill_id="skill_a",
            name="A",
            description="Active",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
        )
        await service.create_org_skill(
            db_session,
            skill_id="skill_b",
            name="B",
            description="To delete",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
        )
        await service.delete_org_skill(db_session, "skill_b")

        all_skills = await service.list_org_skills(db_session, active_only=False)
        assert len(all_skills) == 2

    async def test_list_filtered_by_role_id(self, db_session):
        """role_id filter returns only skills belonging to that role."""
        await service.create_org_skill(
            db_session,
            skill_id="skill_buyer",
            name="Buyer Skill",
            description="For buyer",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            role_id="media_buyer",
        )
        await service.create_org_skill(
            db_session,
            skill_id="skill_analyst",
            name="Analyst Skill",
            description="For analyst",
            category="reporting",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            role_id="reporting_analyst",
        )

        buyer_skills = await service.list_org_skills(
            db_session,
            role_id="media_buyer",
        )
        assert len(buyer_skills) == 1
        assert buyer_skills[0].skill_id == "skill_buyer"

    async def test_list_filtered_by_department_id(self, db_session):
        """department_id filter returns only skills in that department."""
        await service.create_org_skill(
            db_session,
            skill_id="mkt_skill",
            name="Mkt Skill",
            description="Marketing skill",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            department_id="marketing",
        )
        await service.create_org_skill(
            db_session,
            skill_id="eng_skill",
            name="Eng Skill",
            description="Engineering skill",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            department_id="engineering",
        )

        mkt_skills = await service.list_org_skills(
            db_session,
            department_id="marketing",
        )
        assert len(mkt_skills) == 1
        assert mkt_skills[0].skill_id == "mkt_skill"

    async def test_list_sorted_by_skill_id(self, db_session):
        """Results are sorted alphabetically by skill_id."""
        await service.create_org_skill(
            db_session,
            skill_id="zebra_skill",
            name="Zebra",
            description="Z",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
        )
        await service.create_org_skill(
            db_session,
            skill_id="alpha_skill",
            name="Alpha",
            description="A",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
        )

        skills = await service.list_org_skills(db_session)
        assert skills[0].skill_id == "alpha_skill"
        assert skills[1].skill_id == "zebra_skill"

    async def test_list_empty(self, db_session):
        """Listing skills from an empty table returns empty list."""
        result = await service.list_org_skills(db_session)
        assert result == []

    async def test_list_combined_filters(self, db_session):
        """Using both role_id and department_id filters together."""
        await service.create_org_skill(
            db_session,
            skill_id="s1",
            name="S1",
            description="Desc",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            department_id="marketing",
            role_id="buyer",
        )
        await service.create_org_skill(
            db_session,
            skill_id="s2",
            name="S2",
            description="Desc",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            department_id="marketing",
            role_id="analyst",
        )
        await service.create_org_skill(
            db_session,
            skill_id="s3",
            name="S3",
            description="Desc",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            department_id="engineering",
            role_id="buyer",
        )

        result = await service.list_org_skills(
            db_session,
            department_id="marketing",
            role_id="buyer",
        )
        assert len(result) == 1
        assert result[0].skill_id == "s1"


# ============================================================
# Audit Trail Tests
# ============================================================


class TestOrgChartAuditTrail:
    async def test_create_department_logs_audit(self, db_session):
        """Creating a department writes an org_chart_change audit entry."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Marketing department",
            created_by="admin",
        )

        await _get_org_chart_audit_entries(db_session)
        # created_by="admin" but audit logged as user_id="admin"
        # _log_org_chart_change uses created_by or "system"
        # We need to query by "admin" since created_by="admin"
        entries_admin = await service.get_audit_trail(
            db_session, "admin", event_type="org_chart_change"
        )
        assert len(entries_admin) == 1
        entry = entries_admin[0]
        assert entry.event_type == "org_chart_change"
        assert entry.event_data["operation"] == "create"
        assert entry.event_data["entity_type"] == "department"
        assert entry.event_data["entity_id"] == "marketing"
        assert entry.source == "org_chart"

    async def test_update_department_logs_audit(self, db_session):
        """Updating a department writes an org_chart_change audit entry."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Description",
        )

        await service.update_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing V2",
        )

        entries = await service.get_audit_trail(db_session, "system", event_type="org_chart_change")
        # Both create and update are logged as "system" when no created_by
        assert len(entries) == 2
        # Most recent first
        update_entry = entries[0]
        assert update_entry.event_data["operation"] == "update"
        assert "name" in update_entry.event_data["changes"]

    async def test_delete_department_logs_audit(self, db_session):
        """Deleting a department writes an org_chart_change audit entry."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Description",
        )

        await service.delete_org_department(db_session, "marketing")

        entries = await service.get_audit_trail(db_session, "system", event_type="org_chart_change")
        assert len(entries) == 2  # create + delete
        delete_entry = entries[0]
        assert delete_entry.event_data["operation"] == "delete"
        assert delete_entry.event_data["entity_type"] == "department"

    async def test_create_role_logs_audit(self, db_session):
        """Creating a role writes an org_chart_change audit entry."""
        await service.create_org_role(
            db_session,
            role_id="buyer",
            name="Buyer",
            department_id="marketing",
            description="Buys media",
            created_by="operator",
        )

        entries = await service.get_audit_trail(
            db_session, "operator", event_type="org_chart_change"
        )
        assert len(entries) == 1
        assert entries[0].event_data["operation"] == "create"
        assert entries[0].event_data["entity_type"] == "role"
        assert entries[0].event_data["entity_id"] == "buyer"

    async def test_create_skill_logs_audit(self, db_session):
        """Creating a skill writes an org_chart_change audit entry."""
        await service.create_org_skill(
            db_session,
            skill_id="budget_skill",
            name="Budget Skill",
            description="Analyzes budgets",
            category="analysis",
            system_supplement="S.",
            prompt_template="T.",
            output_format="F.",
            business_guidance="G.",
            created_by="admin",
        )

        entries = await service.get_audit_trail(db_session, "admin", event_type="org_chart_change")
        assert len(entries) == 1
        assert entries[0].event_data["operation"] == "create"
        assert entries[0].event_data["entity_type"] == "skill"
        assert entries[0].event_data["entity_id"] == "budget_skill"

    async def test_update_records_old_and_new_values(self, db_session):
        """Update audit entry includes old and new values for changed fields."""
        await service.create_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing",
            description="Original description",
        )

        await service.update_org_department(
            db_session,
            dept_id="marketing",
            name="Marketing V2",
        )

        entries = await service.get_audit_trail(db_session, "system", event_type="org_chart_change")
        update_entry = entries[0]
        changes = update_entry.event_data["changes"]
        assert "name" in changes
        assert changes["name"]["old"] == "Marketing"
        assert changes["name"]["new"] == "Marketing V2"
