"""Tests for src.skills.auto_execute — auto-execute rule engine.

Covers all data classes, YAML loading, validation, field resolution,
operator evaluation, condition evaluation, the main should_auto_execute()
decision function, and the budget cap safety check.

All DB operations are mocked; no database connection needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.skills.auto_execute import (
    _MISSING,
    AutoExecuteDecision,
    AutoExecuteRule,
    AutoExecuteRuleSet,
    RuleCondition,
    RuleConstraints,
    _evaluate_operator,
    _exceeds_budget_cap,
    _resolve_field,
    evaluate_conditions,
    load_rules_from_yaml,
    should_auto_execute,
    validate_rules,
)

# =====================================================================
# Helpers
# =====================================================================


def _default_settings(**overrides):
    """Return a SimpleNamespace mimicking Settings for auto-execute tests."""
    defaults = {
        "auto_execute_enabled": True,
        "auto_execute_max_per_day": 20,
        "max_budget_change_ratio": 1.5,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _write_yaml(tmp_path: Path, filename: str, data: dict) -> Path:
    """Write a dict as YAML to tmp_path/filename, return the Path."""
    p = tmp_path / filename
    p.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return p


def _minimal_ruleset_data(
    role_id: str = "media_buyer",
    rules: list[dict] | None = None,
) -> dict:
    """Return a minimal valid ruleset dict for YAML serialization."""
    if rules is None:
        rules = [
            {
                "id": "small_budget_up",
                "description": "Auto-approve small budget increases",
                "action_types": ["budget_change"],
                "conditions": [
                    {"field": "action_params.change_pct", "operator": "lte", "value": 20},
                ],
                "constraints": {
                    "max_daily_auto_executions": 5,
                    "cooldown_minutes": 30,
                    "platforms": ["google_ads", "meta"],
                },
            }
        ]
    return {"role_id": role_id, "rules": rules}


# =====================================================================
# TestRuleCondition
# =====================================================================


class TestRuleCondition:
    """Tests for the RuleCondition frozen dataclass."""

    def test_construction(self):
        """RuleCondition stores field, operator, and value."""
        cond = RuleCondition(field="spend", operator="gt", value=100)
        assert cond.field == "spend"
        assert cond.operator == "gt"
        assert cond.value == 100

    def test_frozen(self):
        """RuleCondition is immutable."""
        cond = RuleCondition(field="x", operator="eq", value=1)
        with pytest.raises(AttributeError):
            cond.field = "y"  # type: ignore[misc]

    def test_value_can_be_any_type(self):
        """The value field accepts any type (list, str, float, None)."""
        cond_list = RuleCondition(field="f", operator="in", value=["a", "b"])
        assert cond_list.value == ["a", "b"]

        cond_none = RuleCondition(field="f", operator="eq", value=None)
        assert cond_none.value is None


# =====================================================================
# TestAutoExecuteRule
# =====================================================================


class TestAutoExecuteRule:
    """Tests for the AutoExecuteRule frozen dataclass."""

    def test_construction_with_defaults(self):
        """AutoExecuteRule has sensible defaults for optional fields."""
        rule = AutoExecuteRule(id="r1", description="test rule")
        assert rule.id == "r1"
        assert rule.description == "test rule"
        assert rule.enabled is True
        assert rule.action_types == ()
        assert rule.conditions == ()
        assert isinstance(rule.constraints, RuleConstraints)

    def test_construction_full(self):
        """AutoExecuteRule stores all fields when provided."""
        cond = RuleCondition(field="spend", operator="lt", value=500)
        constraints = RuleConstraints(
            max_daily_auto_executions=3,
            cooldown_minutes=60,
            platforms=("google_ads",),
        )
        rule = AutoExecuteRule(
            id="r2",
            description="full rule",
            enabled=False,
            action_types=("budget_change",),
            conditions=(cond,),
            constraints=constraints,
        )
        assert rule.enabled is False
        assert rule.action_types == ("budget_change",)
        assert len(rule.conditions) == 1
        assert rule.constraints.cooldown_minutes == 60

    def test_frozen(self):
        """AutoExecuteRule is immutable."""
        rule = AutoExecuteRule(id="r1", description="test")
        with pytest.raises(AttributeError):
            rule.enabled = False  # type: ignore[misc]


# =====================================================================
# TestRuleConstraints
# =====================================================================


class TestRuleConstraints:
    """Tests for the RuleConstraints frozen dataclass."""

    def test_defaults(self):
        """RuleConstraints defaults: 10 daily, 0 cooldown, no platforms."""
        c = RuleConstraints()
        assert c.max_daily_auto_executions == 10
        assert c.cooldown_minutes == 0
        assert c.platforms == ()

    def test_custom_values(self):
        """RuleConstraints accepts custom values."""
        c = RuleConstraints(
            max_daily_auto_executions=5,
            cooldown_minutes=15,
            platforms=("meta",),
        )
        assert c.max_daily_auto_executions == 5
        assert c.cooldown_minutes == 15
        assert c.platforms == ("meta",)


# =====================================================================
# TestAutoExecuteRuleSet
# =====================================================================


class TestAutoExecuteRuleSet:
    """Tests for the AutoExecuteRuleSet frozen dataclass."""

    def test_construction(self):
        """AutoExecuteRuleSet stores role_id and rules tuple."""
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=())
        assert ruleset.role_id == "buyer"
        assert ruleset.rules == ()

    def test_with_rules(self):
        """AutoExecuteRuleSet stores multiple rules."""
        r1 = AutoExecuteRule(id="r1", description="rule 1")
        r2 = AutoExecuteRule(id="r2", description="rule 2")
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r1, r2))
        assert len(ruleset.rules) == 2
        assert ruleset.rules[0].id == "r1"


# =====================================================================
# TestAutoExecuteDecision
# =====================================================================


class TestAutoExecuteDecision:
    """Tests for the AutoExecuteDecision frozen dataclass."""

    def test_positive_decision(self):
        """AutoExecuteDecision captures a positive auto-execute result."""
        decision = AutoExecuteDecision(
            should_auto_execute=True,
            matched_rule_id="r1",
            reasons=("PASS: spend lt 500 (actual: 200)",),
            conditions_evaluated=1,
        )
        assert decision.should_auto_execute is True
        assert decision.matched_rule_id == "r1"
        assert len(decision.reasons) == 1
        assert decision.conditions_evaluated == 1

    def test_negative_decision_defaults(self):
        """AutoExecuteDecision defaults to negative with empty fields."""
        decision = AutoExecuteDecision(should_auto_execute=False)
        assert decision.should_auto_execute is False
        assert decision.matched_rule_id == ""
        assert decision.reasons == ()
        assert decision.conditions_evaluated == 0


# =====================================================================
# TestLoadRulesFromYaml
# =====================================================================


class TestLoadRulesFromYaml:
    """Tests for load_rules_from_yaml() — YAML parsing and conversion."""

    def test_valid_yaml(self, tmp_path):
        """Loads a well-formed _rules.yaml into an AutoExecuteRuleSet."""
        path = _write_yaml(tmp_path, "_rules.yaml", _minimal_ruleset_data())
        ruleset = load_rules_from_yaml(path)
        assert ruleset.role_id == "media_buyer"
        assert len(ruleset.rules) == 1
        rule = ruleset.rules[0]
        assert rule.id == "small_budget_up"
        assert "budget_change" in rule.action_types
        assert len(rule.conditions) == 1
        assert rule.conditions[0].operator == "lte"
        assert rule.constraints.max_daily_auto_executions == 5
        assert rule.constraints.cooldown_minutes == 30
        assert "google_ads" in rule.constraints.platforms

    def test_missing_role_id(self, tmp_path):
        """Raises ValueError when role_id is missing."""
        data = _minimal_ruleset_data()
        del data["role_id"]
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        with pytest.raises(ValueError, match="Missing role_id"):
            load_rules_from_yaml(path)

    def test_empty_role_id(self, tmp_path):
        """Raises ValueError when role_id is empty string."""
        data = _minimal_ruleset_data(role_id="")
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        with pytest.raises(ValueError, match="Missing role_id"):
            load_rules_from_yaml(path)

    def test_invalid_yaml_syntax(self, tmp_path):
        """Raises ValueError on unparseable YAML."""
        p = tmp_path / "_rules.yaml"
        p.write_text("{{invalid yaml: [", encoding="utf-8")
        with pytest.raises(ValueError, match="Failed to parse"):
            load_rules_from_yaml(p)

    def test_bad_top_level_structure(self, tmp_path):
        """Raises ValueError when top level is not a dict."""
        p = tmp_path / "_rules.yaml"
        p.write_text("- just_a_list\n- not_a_dict\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Expected dict"):
            load_rules_from_yaml(p)

    def test_rules_not_a_list(self, tmp_path):
        """Raises ValueError when rules key is not a list."""
        data = {"role_id": "buyer", "rules": "not_a_list"}
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        with pytest.raises(ValueError, match="Expected list"):
            load_rules_from_yaml(path)

    def test_rule_missing_id(self, tmp_path):
        """Raises ValueError when a rule has no id."""
        data = _minimal_ruleset_data(
            rules=[{"description": "no id", "action_types": ["budget_change"]}]
        )
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        with pytest.raises(ValueError, match="missing 'id'"):
            load_rules_from_yaml(path)

    def test_invalid_operator_in_condition(self, tmp_path):
        """Raises ValueError for an unknown operator."""
        data = _minimal_ruleset_data(
            rules=[
                {
                    "id": "r1",
                    "description": "bad op",
                    "action_types": ["budget_change"],
                    "conditions": [
                        {"field": "x", "operator": "BOGUS", "value": 1},
                    ],
                }
            ]
        )
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        with pytest.raises(ValueError, match="Invalid operator"):
            load_rules_from_yaml(path)

    def test_all_valid_operators(self, tmp_path):
        """Successfully loads conditions with every valid operator."""
        ops = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "regex"]
        conditions = [{"field": f"field_{op}", "operator": op, "value": 1} for op in ops]
        data = _minimal_ruleset_data(
            rules=[
                {
                    "id": "all_ops",
                    "description": "test every operator",
                    "action_types": ["budget_change"],
                    "conditions": conditions,
                }
            ]
        )
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        ruleset = load_rules_from_yaml(path)
        loaded_ops = {c.operator for c in ruleset.rules[0].conditions}
        assert loaded_ops == set(ops)

    def test_rule_defaults(self, tmp_path):
        """Rules default to enabled=True, empty conditions, default constraints."""
        data = _minimal_ruleset_data(
            rules=[
                {
                    "id": "minimal",
                    "description": "minimal rule",
                    "action_types": ["budget_change"],
                }
            ]
        )
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        ruleset = load_rules_from_yaml(path)
        rule = ruleset.rules[0]
        assert rule.enabled is True
        assert rule.conditions == ()
        assert rule.constraints.max_daily_auto_executions == 10
        assert rule.constraints.cooldown_minutes == 0

    def test_disabled_rule(self, tmp_path):
        """A rule with enabled: false loads correctly."""
        data = _minimal_ruleset_data(
            rules=[
                {
                    "id": "disabled_rule",
                    "description": "disabled",
                    "enabled": False,
                    "action_types": ["budget_change"],
                }
            ]
        )
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        ruleset = load_rules_from_yaml(path)
        assert ruleset.rules[0].enabled is False

    def test_multiple_rules(self, tmp_path):
        """Loads multiple rules from a single file."""
        data = _minimal_ruleset_data(
            rules=[
                {"id": "r1", "description": "rule 1", "action_types": ["budget_change"]},
                {"id": "r2", "description": "rule 2", "action_types": ["pause_campaign"]},
            ]
        )
        path = _write_yaml(tmp_path, "_rules.yaml", data)
        ruleset = load_rules_from_yaml(path)
        assert len(ruleset.rules) == 2
        assert ruleset.rules[0].id == "r1"
        assert ruleset.rules[1].id == "r2"


# =====================================================================
# TestValidateRules
# =====================================================================


class TestValidateRules:
    """Tests for validate_rules() — structural validation of a ruleset."""

    def test_valid_ruleset(self):
        """A well-formed ruleset returns no errors."""
        ruleset = AutoExecuteRuleSet(
            role_id="buyer",
            rules=(
                AutoExecuteRule(
                    id="r1",
                    description="desc",
                    action_types=("budget_change",),
                ),
            ),
        )
        errors = validate_rules(ruleset)
        assert errors == []

    def test_empty_role_id(self):
        """Flags empty role_id."""
        ruleset = AutoExecuteRuleSet(role_id="", rules=())
        errors = validate_rules(ruleset)
        assert any("role_id" in e for e in errors)

    def test_duplicate_rule_ids(self):
        """Flags duplicate rule IDs."""
        r = AutoExecuteRule(id="dupe", description="x", action_types=("budget_change",))
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r, r))
        errors = validate_rules(ruleset)
        assert any("Duplicate" in e for e in errors)

    def test_missing_action_types(self):
        """Flags rules with no action_types."""
        r = AutoExecuteRule(id="r1", description="x", action_types=())
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r,))
        errors = validate_rules(ruleset)
        assert any("action_type" in e for e in errors)

    def test_invalid_operator_in_condition(self):
        """Flags conditions with invalid operators."""
        cond = RuleCondition(field="x", operator="BOGUS", value=1)
        r = AutoExecuteRule(
            id="r1",
            description="x",
            action_types=("budget_change",),
            conditions=(cond,),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r,))
        errors = validate_rules(ruleset)
        assert any("invalid operator" in e.lower() for e in errors)

    def test_empty_field_in_condition(self):
        """Flags conditions with empty field string."""
        cond = RuleCondition(field="", operator="eq", value=1)
        r = AutoExecuteRule(
            id="r1",
            description="x",
            action_types=("budget_change",),
            conditions=(cond,),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r,))
        errors = validate_rules(ruleset)
        assert any("field" in e.lower() for e in errors)

    def test_bad_max_daily_auto_executions(self):
        """Flags max_daily_auto_executions < 1."""
        constraints = RuleConstraints(max_daily_auto_executions=0)
        r = AutoExecuteRule(
            id="r1",
            description="x",
            action_types=("budget_change",),
            constraints=constraints,
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r,))
        errors = validate_rules(ruleset)
        assert any("max_daily_auto_executions" in e for e in errors)

    def test_negative_cooldown_minutes(self):
        """Flags cooldown_minutes < 0."""
        constraints = RuleConstraints(cooldown_minutes=-1)
        r = AutoExecuteRule(
            id="r1",
            description="x",
            action_types=("budget_change",),
            constraints=constraints,
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r,))
        errors = validate_rules(ruleset)
        assert any("cooldown_minutes" in e for e in errors)

    def test_no_rules_is_valid(self):
        """A ruleset with no rules but valid role_id is valid."""
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=())
        errors = validate_rules(ruleset)
        assert errors == []


# =====================================================================
# TestResolveField
# =====================================================================


class TestResolveField:
    """Tests for _resolve_field() — dot-notation dict traversal."""

    def test_simple_key(self):
        """Resolves a top-level key."""
        assert _resolve_field("name", {"name": "test"}) == "test"

    def test_nested_key(self):
        """Resolves a nested dot-notation path."""
        data = {"action_params": {"metrics": {"roas": 3.5}}}
        assert _resolve_field("action_params.metrics.roas", data) == 3.5

    def test_missing_top_level(self):
        """Returns _MISSING sentinel for a missing top-level key."""
        result = _resolve_field("nonexistent", {"a": 1})
        assert isinstance(result, type(_MISSING))

    def test_missing_nested(self):
        """Returns _MISSING sentinel when an intermediate key is missing."""
        data = {"action_params": {"budget": 100}}
        result = _resolve_field("action_params.nonexistent.deep", data)
        assert isinstance(result, type(_MISSING))

    def test_non_dict_intermediate(self):
        """Returns _MISSING when traversal hits a non-dict value."""
        data = {"x": 42}
        result = _resolve_field("x.y", data)
        assert isinstance(result, type(_MISSING))

    def test_empty_dict(self):
        """Returns _MISSING for any path on an empty dict."""
        result = _resolve_field("any.path", {})
        assert isinstance(result, type(_MISSING))


# =====================================================================
# TestEvaluateOperator
# =====================================================================


class TestEvaluateOperator:
    """Tests for _evaluate_operator() — single operator evaluation."""

    def test_eq_true(self):
        """eq operator passes when values are equal."""
        assert _evaluate_operator("hello", "eq", "hello") is True

    def test_eq_false(self):
        """eq operator fails when values differ."""
        assert _evaluate_operator("hello", "eq", "world") is False

    def test_ne_true(self):
        """ne operator passes when values differ."""
        assert _evaluate_operator(1, "ne", 2) is True

    def test_ne_false(self):
        """ne operator fails when values are equal."""
        assert _evaluate_operator(1, "ne", 1) is False

    def test_gt_true(self):
        """gt operator passes when value > threshold."""
        assert _evaluate_operator(10, "gt", 5) is True

    def test_gt_false(self):
        """gt operator fails when value <= threshold."""
        assert _evaluate_operator(5, "gt", 10) is False

    def test_gt_equal_is_false(self):
        """gt returns False when values are equal."""
        assert _evaluate_operator(5, "gt", 5) is False

    def test_gte_true(self):
        """gte passes when value >= threshold."""
        assert _evaluate_operator(5, "gte", 5) is True

    def test_gte_false(self):
        """gte fails when value < threshold."""
        assert _evaluate_operator(4, "gte", 5) is False

    def test_lt_true(self):
        """lt passes when value < threshold."""
        assert _evaluate_operator(3, "lt", 5) is True

    def test_lt_false(self):
        """lt fails when value >= threshold."""
        assert _evaluate_operator(5, "lt", 5) is False

    def test_lte_true(self):
        """lte passes when value <= threshold."""
        assert _evaluate_operator(5, "lte", 5) is True

    def test_lte_false(self):
        """lte fails when value > threshold."""
        assert _evaluate_operator(6, "lte", 5) is False

    def test_in_true(self):
        """in operator passes when value is in the threshold list."""
        assert _evaluate_operator("google_ads", "in", ["google_ads", "meta"]) is True

    def test_in_false(self):
        """in operator fails when value is not in the threshold list."""
        assert _evaluate_operator("bing", "in", ["google_ads", "meta"]) is False

    def test_not_in_true(self):
        """not_in passes when value is not in the threshold list."""
        assert _evaluate_operator("bing", "not_in", ["google_ads", "meta"]) is True

    def test_not_in_false(self):
        """not_in fails when value is in the threshold list."""
        assert _evaluate_operator("meta", "not_in", ["google_ads", "meta"]) is False

    def test_contains_true(self):
        """contains passes when threshold string is found in value string."""
        assert _evaluate_operator("brand campaign", "contains", "brand") is True

    def test_contains_false(self):
        """contains fails when threshold string is not found."""
        assert _evaluate_operator("performance campaign", "contains", "brand") is False

    def test_regex_true(self):
        """regex passes when pattern matches."""
        assert _evaluate_operator("campaign_123", "regex", r"campaign_\d+") is True

    def test_regex_false(self):
        """regex fails when pattern does not match."""
        assert _evaluate_operator("campaign_abc", "regex", r"campaign_\d+$") is False

    def test_numeric_comparison_with_strings(self):
        """gt/lt operators coerce string values to float."""
        assert _evaluate_operator("10.5", "gt", "5.0") is True
        assert _evaluate_operator("3", "lt", "7") is True

    def test_type_error_returns_false(self):
        """Returns False when operator encounters a type error."""
        assert _evaluate_operator(None, "gt", 5) is False

    def test_unknown_operator_returns_false(self):
        """Returns False for an unknown operator string."""
        assert _evaluate_operator(1, "BOGUS", 1) is False


# =====================================================================
# TestEvaluateConditions
# =====================================================================


class TestEvaluateConditions:
    """Tests for evaluate_conditions() — AND logic over conditions."""

    def test_all_pass(self):
        """Returns True when all conditions pass."""
        conditions = (
            RuleCondition(field="spend", operator="lt", value=500),
            RuleCondition(field="platform", operator="eq", value="google_ads"),
        )
        rec = {"spend": 200, "platform": "google_ads"}
        passed, reasons = evaluate_conditions(conditions, rec)
        assert passed is True
        assert all("PASS" in r for r in reasons)
        assert len(reasons) == 2

    def test_first_condition_fails(self):
        """Returns False immediately on first failure (short circuit)."""
        conditions = (
            RuleCondition(field="spend", operator="lt", value=100),
            RuleCondition(field="platform", operator="eq", value="google_ads"),
        )
        rec = {"spend": 200, "platform": "google_ads"}
        passed, reasons = evaluate_conditions(conditions, rec)
        assert passed is False
        assert "FAIL" in reasons[-1]
        # Only 1 reason because it short-circuits
        assert len(reasons) == 1

    def test_second_condition_fails(self):
        """Returns False when second condition fails."""
        conditions = (
            RuleCondition(field="spend", operator="lt", value=500),
            RuleCondition(field="platform", operator="eq", value="meta"),
        )
        rec = {"spend": 200, "platform": "google_ads"}
        passed, reasons = evaluate_conditions(conditions, rec)
        assert passed is False
        assert "PASS" in reasons[0]
        assert "FAIL" in reasons[1]

    def test_missing_field(self):
        """Returns False with FAIL reason when field is not found."""
        conditions = (RuleCondition(field="nonexistent", operator="eq", value=1),)
        rec = {"spend": 200}
        passed, reasons = evaluate_conditions(conditions, rec)
        assert passed is False
        assert "not found" in reasons[0]

    def test_empty_conditions(self):
        """Empty conditions tuple means all pass (vacuous truth)."""
        passed, reasons = evaluate_conditions((), {"any": "data"})
        assert passed is True
        assert reasons == []

    def test_nested_field_evaluation(self):
        """Evaluates conditions using dot-notation field paths."""
        conditions = (
            RuleCondition(
                field="action_params.change_pct",
                operator="lte",
                value=20,
            ),
        )
        rec = {"action_params": {"change_pct": 15}}
        passed, reasons = evaluate_conditions(conditions, rec)
        assert passed is True


# =====================================================================
# TestShouldAutoExecute
# =====================================================================


class TestShouldAutoExecute:
    """Tests for should_auto_execute() — the main decision function."""

    @pytest.mark.asyncio
    async def test_global_kill_switch_off(self):
        """Returns False when auto_execute_enabled is False."""
        settings = _default_settings(auto_execute_enabled=False)
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=())
        decision = await should_auto_execute({}, "buyer", ruleset, settings)
        assert decision.should_auto_execute is False
        assert "disabled" in decision.reasons[0].lower()

    @pytest.mark.asyncio
    async def test_no_ruleset(self):
        """Returns False when ruleset is None."""
        settings = _default_settings()
        decision = await should_auto_execute({}, "buyer", None, settings)
        assert decision.should_auto_execute is False
        assert "No auto-execute rules" in decision.reasons[0]

    @pytest.mark.asyncio
    async def test_empty_rules(self):
        """Returns False when ruleset has no rules."""
        settings = _default_settings()
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=())
        decision = await should_auto_execute({}, "buyer", ruleset, settings)
        assert decision.should_auto_execute is False

    @pytest.mark.asyncio
    async def test_matching_rule_no_session(self):
        """Returns True when a rule matches and no session is provided."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            conditions=(
                RuleCondition(
                    field="action_params.change_pct",
                    operator="lte",
                    value=20,
                ),
            ),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {
            "action_type": "budget_change",
            "action_params": {"change_pct": 10},
        }
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is True
        assert decision.matched_rule_id == "r1"

    @pytest.mark.asyncio
    async def test_non_matching_action_type(self):
        """Returns False when recommendation action_type does not match any rule."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {"action_type": "pause_campaign"}
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is False
        assert "No matching" in decision.reasons[0]

    @pytest.mark.asyncio
    async def test_failed_conditions(self):
        """Returns False when conditions do not pass."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            conditions=(RuleCondition(field="action_params.change_pct", operator="lte", value=5),),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {
            "action_type": "budget_change",
            "action_params": {"change_pct": 25},
        }
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is False

    @pytest.mark.asyncio
    async def test_budget_cap_exceeded(self):
        """Returns False with BLOCKED reason when budget cap would be exceeded."""
        settings = _default_settings(max_budget_change_ratio=1.5)
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {
            "action_type": "budget_change",
            "action_params": {
                "current_budget": 100,
                "new_budget": 200,  # ratio 2.0 > 1.5
            },
        }
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is False
        assert any("BLOCKED" in r for r in decision.reasons)
        assert decision.matched_rule_id == "r1"

    @pytest.mark.asyncio
    async def test_platform_constraint_skips_rule(self):
        """Skips a rule when platform does not match constraints."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            constraints=RuleConstraints(platforms=("meta",)),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {
            "action_type": "budget_change",
            "platform": "google_ads",
        }
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is False

    @pytest.mark.asyncio
    async def test_disabled_rule_skipped(self):
        """Skips disabled rules."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            enabled=False,
            action_types=("budget_change",),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {"action_type": "budget_change"}
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is False

    @pytest.mark.asyncio
    async def test_daily_count_limit_reached(self):
        """Skips rule when daily count limit is reached via DB check."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            constraints=RuleConstraints(max_daily_auto_executions=3),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {"action_type": "budget_change", "user_id": "u1"}

        mock_session = AsyncMock()
        with (
            patch(
                "src.db.service.count_auto_executions_today",
                new_callable=AsyncMock,
                return_value=3,
            ),
            patch(
                "src.db.service.get_last_auto_execution_time",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            decision = await should_auto_execute(
                rec,
                "buyer",
                ruleset,
                settings,
                session=mock_session,
            )

        assert decision.should_auto_execute is False

    @pytest.mark.asyncio
    async def test_global_daily_cap_reached(self):
        """Returns False when global daily cap is reached."""
        settings = _default_settings(auto_execute_max_per_day=5)
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            constraints=RuleConstraints(max_daily_auto_executions=100),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {"action_type": "budget_change", "user_id": "u1"}

        mock_session = AsyncMock()

        async def count_side_effect(session, user_id, rule_id):
            """Return 0 for rule-specific, 5 for global."""
            if rule_id:
                return 0
            return 5

        with patch(
            "src.db.service.count_auto_executions_today",
            new_callable=AsyncMock,
            side_effect=count_side_effect,
        ):
            decision = await should_auto_execute(
                rec,
                "buyer",
                ruleset,
                settings,
                session=mock_session,
            )

        assert decision.should_auto_execute is False
        assert "Global daily" in decision.reasons[0]

    @pytest.mark.asyncio
    async def test_cooldown_not_elapsed(self):
        """Skips rule when cooldown period has not elapsed."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            constraints=RuleConstraints(cooldown_minutes=60),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {"action_type": "budget_change", "user_id": "u1"}

        mock_session = AsyncMock()
        recent_exec = datetime.now(timezone.utc) - timedelta(minutes=10)

        with (
            patch(
                "src.db.service.count_auto_executions_today",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "src.db.service.get_last_auto_execution_time",
                new_callable=AsyncMock,
                return_value=recent_exec,
            ),
        ):
            decision = await should_auto_execute(
                rec,
                "buyer",
                ruleset,
                settings,
                session=mock_session,
            )

        assert decision.should_auto_execute is False

    @pytest.mark.asyncio
    async def test_cooldown_elapsed(self):
        """Auto-executes when cooldown has fully elapsed."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            constraints=RuleConstraints(cooldown_minutes=60),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {"action_type": "budget_change", "user_id": "u1"}

        mock_session = AsyncMock()
        old_exec = datetime.now(timezone.utc) - timedelta(minutes=120)

        with (
            patch(
                "src.db.service.count_auto_executions_today",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "src.db.service.get_last_auto_execution_time",
                new_callable=AsyncMock,
                return_value=old_exec,
            ),
        ):
            decision = await should_auto_execute(
                rec,
                "buyer",
                ruleset,
                settings,
                session=mock_session,
            )

        assert decision.should_auto_execute is True
        assert decision.matched_rule_id == "r1"

    @pytest.mark.asyncio
    async def test_first_matching_rule_wins(self):
        """Returns on first matching rule, skipping subsequent rules."""
        settings = _default_settings()
        r1 = AutoExecuteRule(
            id="r1",
            description="first",
            action_types=("budget_change",),
        )
        r2 = AutoExecuteRule(
            id="r2",
            description="second",
            action_types=("budget_change",),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(r1, r2))
        rec = {"action_type": "budget_change"}
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is True
        assert decision.matched_rule_id == "r1"

    @pytest.mark.asyncio
    async def test_platform_from_action_params(self):
        """Reads platform from action_params.platform as fallback."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="r1",
            description="test",
            action_types=("budget_change",),
            constraints=RuleConstraints(platforms=("meta",)),
        )
        ruleset = AutoExecuteRuleSet(role_id="buyer", rules=(rule,))
        rec = {
            "action_type": "budget_change",
            "action_params": {"platform": "meta"},
        }
        decision = await should_auto_execute(rec, "buyer", ruleset, settings)
        assert decision.should_auto_execute is True

    @pytest.mark.asyncio
    async def test_missing_auto_execute_enabled_attribute(self):
        """Treats missing auto_execute_enabled as False (kill switch on)."""
        settings = SimpleNamespace()  # no auto_execute_enabled attribute
        decision = await should_auto_execute({}, "buyer", None, settings)
        assert decision.should_auto_execute is False
        assert "disabled" in decision.reasons[0].lower()


# =====================================================================
# TestExceedsBudgetCap
# =====================================================================


class TestExceedsBudgetCap:
    """Tests for _exceeds_budget_cap() — budget safety check."""

    def test_budget_within_cap(self):
        """Returns False when budget change is within the cap."""
        settings = _default_settings(max_budget_change_ratio=1.5)
        rec = {
            "action_type": "budget_change",
            "action_params": {"current_budget": 100, "new_budget": 140},
        }
        assert _exceeds_budget_cap(rec, settings) is False

    def test_budget_exactly_at_cap(self):
        """Returns False when ratio equals the cap exactly."""
        settings = _default_settings(max_budget_change_ratio=1.5)
        rec = {
            "action_type": "budget_change",
            "action_params": {"current_budget": 100, "new_budget": 150},
        }
        assert _exceeds_budget_cap(rec, settings) is False

    def test_budget_over_cap(self):
        """Returns True when budget ratio exceeds the cap."""
        settings = _default_settings(max_budget_change_ratio=1.5)
        rec = {
            "action_type": "budget_change",
            "action_params": {"current_budget": 100, "new_budget": 200},
        }
        assert _exceeds_budget_cap(rec, settings) is True

    def test_non_budget_action(self):
        """Returns False for non-budget action types (passes through)."""
        settings = _default_settings()
        rec = {"action_type": "pause_campaign", "action_params": {}}
        assert _exceeds_budget_cap(rec, settings) is False

    def test_missing_budget_data(self):
        """Returns False when current_budget or new_budget is missing."""
        settings = _default_settings()
        rec = {
            "action_type": "budget_change",
            "action_params": {},
        }
        assert _exceeds_budget_cap(rec, settings) is False

    def test_zero_current_budget(self):
        """Returns False when current_budget is 0 (avoids division by zero)."""
        settings = _default_settings()
        rec = {
            "action_type": "budget_change",
            "action_params": {"current_budget": 0, "new_budget": 100},
        }
        assert _exceeds_budget_cap(rec, settings) is False

    def test_update_adset_budget_action_type(self):
        """Also checks update_adset_budget action type."""
        settings = _default_settings(max_budget_change_ratio=1.5)
        rec = {
            "action_type": "update_adset_budget",
            "action_params": {"current_budget": 100, "new_budget": 200},
        }
        assert _exceeds_budget_cap(rec, settings) is True

    def test_budget_decrease(self):
        """Budget decrease (ratio < 1) is always within cap."""
        settings = _default_settings(max_budget_change_ratio=1.5)
        rec = {
            "action_type": "budget_change",
            "action_params": {"current_budget": 200, "new_budget": 100},
        }
        assert _exceeds_budget_cap(rec, settings) is False

    def test_string_budget_values(self):
        """Handles string budget values via float() coercion."""
        settings = _default_settings(max_budget_change_ratio=1.5)
        rec = {
            "action_type": "budget_change",
            "action_params": {"current_budget": "100", "new_budget": "200"},
        }
        assert _exceeds_budget_cap(rec, settings) is True


# =====================================================================
# TestClaudeCodeTaskHardBlocks
# =====================================================================


class TestClaudeCodeTaskHardBlocks:
    """Tests for claude_code_task hard blocks in should_auto_execute()."""

    @pytest.mark.asyncio
    async def test_cc_blocks_bypass_permissions(self):
        """Blocks auto-execute when permission_mode is bypassPermissions."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="cc_rule",
            description="allow safe cc tasks",
            action_types=("claude_code_task",),
        )
        ruleset = AutoExecuteRuleSet(role_id="it_admin", rules=(rule,))
        rec = {
            "action_type": "claude_code_task",
            "action_params": {
                "permission_mode": "bypassPermissions",
                "max_budget_usd": 5.0,
            },
        }
        decision = await should_auto_execute(rec, "it_admin", ruleset, settings)
        assert decision.should_auto_execute is False
        assert any("bypassPermissions" in r for r in decision.reasons)

    @pytest.mark.asyncio
    async def test_cc_blocks_high_budget(self):
        """Blocks auto-execute when max_budget_usd exceeds $10."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="cc_rule",
            description="allow safe cc tasks",
            action_types=("claude_code_task",),
        )
        ruleset = AutoExecuteRuleSet(role_id="it_admin", rules=(rule,))
        rec = {
            "action_type": "claude_code_task",
            "action_params": {
                "permission_mode": "acceptEdits",
                "max_budget_usd": 15.0,
            },
        }
        decision = await should_auto_execute(rec, "it_admin", ruleset, settings)
        assert decision.should_auto_execute is False
        assert any("$15" in r or "10" in r for r in decision.reasons)

    @pytest.mark.asyncio
    async def test_cc_allows_matching_rule(self):
        """Allows auto-execute for safe claude_code_task with matching rule."""
        settings = _default_settings()
        rule = AutoExecuteRule(
            id="cc_safe",
            description="allow safe cc tasks",
            action_types=("claude_code_task",),
        )
        ruleset = AutoExecuteRuleSet(role_id="it_admin", rules=(rule,))
        rec = {
            "action_type": "claude_code_task",
            "action_params": {
                "permission_mode": "acceptEdits",
                "max_budget_usd": 5.0,
            },
        }
        decision = await should_auto_execute(rec, "it_admin", ruleset, settings)
        assert decision.should_auto_execute is True
        assert decision.matched_rule_id == "cc_safe"
