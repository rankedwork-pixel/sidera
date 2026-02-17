"""Auto-execute rule engine for graduated trust.

Evaluates whether a recommendation can be auto-executed (no human
approval needed) based on YAML-defined rules per role.  Rules live
alongside ``_role.yaml`` as ``_rules.yaml``.

Three-tier trust model:
- Tier 1: Read-only (``requires_approval: false``) — already exists
- Tier 2: Auto-execute with guardrails — routine writes matching rules
- Tier 3: Requires human approval — current Slack approve/reject flow

Usage::

    from src.skills.auto_execute import should_auto_execute, load_rules_from_yaml

    ruleset = load_rules_from_yaml(Path("path/to/_rules.yaml"))
    decision = await should_auto_execute(rec, "buyer", ruleset, settings, session)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)


# =====================================================================
# Data classes
# =====================================================================


@dataclass(frozen=True)
class RuleCondition:
    """A single condition in an auto-execute rule."""

    field: str  # dot-notation path into recommendation data
    operator: str  # eq, ne, gt, gte, lt, lte, in, not_in, contains, regex
    value: Any


@dataclass(frozen=True)
class RuleConstraints:
    """Runtime constraints for an auto-execute rule."""

    max_daily_auto_executions: int = 10
    cooldown_minutes: int = 0
    platforms: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoExecuteRule:
    """A single auto-execute rule."""

    id: str
    description: str
    enabled: bool = True
    action_types: tuple[str, ...] = ()
    conditions: tuple[RuleCondition, ...] = ()
    constraints: RuleConstraints = field(default_factory=RuleConstraints)


@dataclass(frozen=True)
class AutoExecuteRuleSet:
    """All auto-execute rules for a role."""

    role_id: str
    rules: tuple[AutoExecuteRule, ...] = ()


@dataclass(frozen=True)
class AutoExecuteDecision:
    """Result of evaluating auto-execute rules against a recommendation."""

    should_auto_execute: bool
    matched_rule_id: str = ""
    reasons: tuple[str, ...] = ()
    conditions_evaluated: int = 0


# =====================================================================
# YAML loading
# =====================================================================

_VALID_OPERATORS = frozenset(
    {
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "contains",
        "regex",
    }
)


def load_rules_from_yaml(path: Path) -> AutoExecuteRuleSet:
    """Load an ``AutoExecuteRuleSet`` from a ``_rules.yaml`` file.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed rule set.

    Raises:
        ValueError: On parse or structural errors.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Expected dict at top level in {path}")

    role_id = raw.get("role_id", "")
    if not role_id:
        raise ValueError(f"Missing role_id in {path}")

    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"Expected list for 'rules' in {path}")

    rules: list[AutoExecuteRule] = []
    for i, r in enumerate(raw_rules):
        if not isinstance(r, dict):
            raise ValueError(f"Rule {i} must be a dict in {path}")

        rule_id = r.get("id", "")
        if not rule_id:
            raise ValueError(f"Rule {i} missing 'id' in {path}")

        # Parse conditions
        conditions: list[RuleCondition] = []
        for j, c in enumerate(r.get("conditions", [])):
            if not isinstance(c, dict):
                raise ValueError(f"Condition {j} in rule '{rule_id}' must be a dict")
            op = c.get("operator", "")
            if op not in _VALID_OPERATORS:
                raise ValueError(f"Invalid operator '{op}' in condition {j} of rule '{rule_id}'")
            conditions.append(
                RuleCondition(
                    field=c.get("field", ""),
                    operator=op,
                    value=c.get("value"),
                )
            )

        # Parse constraints
        raw_constraints = r.get("constraints", {})
        platforms_raw = raw_constraints.get("platforms", [])
        constraints = RuleConstraints(
            max_daily_auto_executions=raw_constraints.get(
                "max_daily_auto_executions",
                10,
            ),
            cooldown_minutes=raw_constraints.get("cooldown_minutes", 0),
            platforms=tuple(platforms_raw) if platforms_raw else (),
        )

        action_types_raw = r.get("action_types", [])
        rules.append(
            AutoExecuteRule(
                id=rule_id,
                description=r.get("description", ""),
                enabled=r.get("enabled", True),
                action_types=tuple(action_types_raw),
                conditions=tuple(conditions),
                constraints=constraints,
            )
        )

    return AutoExecuteRuleSet(role_id=role_id, rules=tuple(rules))


# =====================================================================
# Validation
# =====================================================================


def validate_rules(ruleset: AutoExecuteRuleSet) -> list[str]:
    """Validate an ``AutoExecuteRuleSet``.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not ruleset.role_id:
        errors.append("role_id is required")

    seen_ids: set[str] = set()
    for rule in ruleset.rules:
        if not rule.id:
            errors.append("Rule missing 'id'")
            continue

        if rule.id in seen_ids:
            errors.append(f"Duplicate rule id: {rule.id}")
        seen_ids.add(rule.id)

        if not rule.action_types:
            errors.append(f"Rule '{rule.id}': at least one action_type required")

        for cond in rule.conditions:
            if not cond.field:
                errors.append(f"Rule '{rule.id}': condition missing 'field'")
            if cond.operator not in _VALID_OPERATORS:
                errors.append(f"Rule '{rule.id}': invalid operator '{cond.operator}'")

        if rule.constraints.max_daily_auto_executions < 1:
            errors.append(f"Rule '{rule.id}': max_daily_auto_executions must be >= 1")
        if rule.constraints.cooldown_minutes < 0:
            errors.append(f"Rule '{rule.id}': cooldown_minutes must be >= 0")

    return errors


# =====================================================================
# Field resolution and operator evaluation
# =====================================================================


def _resolve_field(field_path: str, data: dict) -> Any:
    """Traverse a dot-notation path into nested dicts.

    Args:
        field_path: e.g. ``"action_params.metrics.roas"``
        data: The dict to traverse.

    Returns:
        The resolved value, or ``_MISSING`` sentinel if not found.
    """
    current: Any = data
    for part in field_path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current[part]
        else:
            return _MISSING
    return current


class _MissingSentinel:
    """Sentinel for missing field values."""

    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING = _MissingSentinel()


def _evaluate_operator(value: Any, operator: str, threshold: Any) -> bool:
    """Evaluate a single operator expression.

    Args:
        value: The actual field value from the recommendation.
        operator: One of the valid operator strings.
        threshold: The expected/threshold value from the rule.

    Returns:
        True if the condition passes, False otherwise.
    """
    try:
        if operator == "eq":
            return value == threshold
        elif operator == "ne":
            return value != threshold
        elif operator == "gt":
            return float(value) > float(threshold)
        elif operator == "gte":
            return float(value) >= float(threshold)
        elif operator == "lt":
            return float(value) < float(threshold)
        elif operator == "lte":
            return float(value) <= float(threshold)
        elif operator == "in":
            return value in threshold
        elif operator == "not_in":
            return value not in threshold
        elif operator == "contains":
            return str(threshold) in str(value)
        elif operator == "regex":
            return bool(re.search(str(threshold), str(value)))
        else:
            return False
    except (TypeError, ValueError):
        return False


# =====================================================================
# Condition evaluation
# =====================================================================


def evaluate_conditions(
    conditions: tuple[RuleCondition, ...],
    recommendation: dict,
) -> tuple[bool, list[str]]:
    """Evaluate all conditions against a recommendation (AND logic).

    Args:
        conditions: The rule's conditions.
        recommendation: The recommendation dict to evaluate against.

    Returns:
        ``(all_passed, reasons)`` — reasons explain each condition result.
    """
    reasons: list[str] = []

    for cond in conditions:
        value = _resolve_field(cond.field, recommendation)

        if isinstance(value, _MissingSentinel):
            reasons.append(f"FAIL: field '{cond.field}' not found in recommendation")
            return False, reasons

        passed = _evaluate_operator(value, cond.operator, cond.value)
        if passed:
            reasons.append(f"PASS: {cond.field} {cond.operator} {cond.value} (actual: {value})")
        else:
            reasons.append(f"FAIL: {cond.field} {cond.operator} {cond.value} (actual: {value})")
            return False, reasons

    return True, reasons


# =====================================================================
# Main decision function
# =====================================================================


async def should_auto_execute(
    recommendation: dict,
    role_id: str,
    ruleset: AutoExecuteRuleSet | None,
    settings: Any,
    session: Any = None,
) -> AutoExecuteDecision:
    """Decide whether a recommendation can be auto-executed.

    Evaluation order:
    1. Global kill switch (``settings.auto_execute_enabled``)
    2. Ruleset exists and has rules for this role
    3. For each enabled rule whose action_types match:
       a. Evaluate all conditions (AND logic)
       b. Check constraints (daily count, cooldown, platform)
       c. First match wins
    4. Budget cap check

    Args:
        recommendation: The recommendation dict from agent output.
        role_id: The role that produced this recommendation.
        ruleset: The loaded rules for this role (or None).
        settings: App settings (needs ``auto_execute_enabled``,
            ``auto_execute_max_per_day``, ``max_budget_change_ratio``).
        session: Optional async DB session for constraint checks.

    Returns:
        An ``AutoExecuteDecision`` with the result and reasoning.
    """
    # 0. Skill/role proposals must ALWAYS go through human review
    action_type = recommendation.get("action_type", "")
    if action_type == "skill_proposal":
        return AutoExecuteDecision(
            should_auto_execute=False,
            reasons=("Skill proposals require human approval — auto-execute blocked",),
        )

    if action_type == "role_proposal":
        return AutoExecuteDecision(
            should_auto_execute=False,
            reasons=("Role proposals require human approval — auto-execute blocked",),
        )

    # 0b. Claude Code tasks: hard-block dangerous configurations
    if action_type == "claude_code_task":
        params = recommendation.get("action_params", {})
        # Never auto-execute bypassPermissions mode
        if params.get("permission_mode") == "bypassPermissions":
            return AutoExecuteDecision(
                should_auto_execute=False,
                reasons=("Claude Code bypassPermissions always requires human approval",),
            )
        # Never auto-execute above $10 budget
        budget = float(params.get("max_budget_usd", 0))
        if budget > 10.0:
            return AutoExecuteDecision(
                should_auto_execute=False,
                reasons=(f"Claude Code budget ${budget:.2f} exceeds $10 auto-execute limit",),
            )

    # 1. Global kill switch
    if not getattr(settings, "auto_execute_enabled", False):
        return AutoExecuteDecision(
            should_auto_execute=False,
            reasons=("Global auto-execute disabled",),
        )

    # 2. Check ruleset
    if ruleset is None or not ruleset.rules:
        return AutoExecuteDecision(
            should_auto_execute=False,
            reasons=("No auto-execute rules defined for this role",),
        )

    # Determine recommendation's action type and platform
    rec_action_type = recommendation.get("action_type", "")
    rec_platform = recommendation.get(
        "platform",
        recommendation.get("action_params", {}).get("platform", ""),
    )

    total_conditions = 0

    # 3. Evaluate each rule
    for rule in ruleset.rules:
        if not rule.enabled:
            continue

        # Action type match
        if rule.action_types and rec_action_type not in rule.action_types:
            continue

        # Platform constraint
        if rule.constraints.platforms and rec_platform:
            if rec_platform not in rule.constraints.platforms:
                continue

        # Evaluate conditions
        passed, reasons = evaluate_conditions(
            rule.conditions,
            recommendation,
        )
        total_conditions += len(rule.conditions)

        if not passed:
            continue

        # Check constraints via DB
        if session is not None:
            from src.db import service as db

            # Daily count check
            user_id = recommendation.get("user_id", "")
            daily_count = await db.count_auto_executions_today(
                session,
                user_id,
                rule.id,
            )
            if daily_count >= rule.constraints.max_daily_auto_executions:
                continue

            # Global daily cap
            global_max = getattr(settings, "auto_execute_max_per_day", 20)
            global_count = await db.count_auto_executions_today(
                session,
                user_id,
                "",
            )
            if global_count >= global_max:
                return AutoExecuteDecision(
                    should_auto_execute=False,
                    reasons=("Global daily auto-execute limit reached",),
                    conditions_evaluated=total_conditions,
                )

            # Cooldown check
            if rule.constraints.cooldown_minutes > 0:
                last_exec = await db.get_last_auto_execution_time(
                    session,
                    user_id,
                    rule.id,
                )
                if last_exec is not None:
                    elapsed = (datetime.now(timezone.utc) - last_exec).total_seconds() / 60
                    if elapsed < rule.constraints.cooldown_minutes:
                        continue

        # Budget cap safety check
        if _exceeds_budget_cap(recommendation, settings):
            return AutoExecuteDecision(
                should_auto_execute=False,
                matched_rule_id=rule.id,
                reasons=(
                    *tuple(reasons),
                    "BLOCKED: Would exceed budget safety cap",
                ),
                conditions_evaluated=total_conditions,
            )

        # Lesson contradiction check — block if high-confidence lesson warns against this
        if session is not None:
            lesson_block = await _check_lesson_contradictions(recommendation, role_id, session)
            if lesson_block:
                return AutoExecuteDecision(
                    should_auto_execute=False,
                    matched_rule_id=rule.id,
                    reasons=(
                        *tuple(reasons),
                        f"BLOCKED: Lesson contradiction — {lesson_block}",
                    ),
                    conditions_evaluated=total_conditions,
                )

        # All checks passed — auto-execute!
        return AutoExecuteDecision(
            should_auto_execute=True,
            matched_rule_id=rule.id,
            reasons=tuple(reasons),
            conditions_evaluated=total_conditions,
        )

    # No rule matched
    return AutoExecuteDecision(
        should_auto_execute=False,
        reasons=("No matching auto-execute rule found",),
        conditions_evaluated=total_conditions,
    )


def _exceeds_budget_cap(recommendation: dict, settings: Any) -> bool:
    """Check if a recommendation would exceed the budget change ratio cap.

    Only applies to budget_change action types. Returns False for
    non-budget actions (they pass through).
    """
    action_type = recommendation.get("action_type", "")
    if action_type not in (
        "budget_change",
        "update_budget",
        "update_adset_budget",
    ):
        return False

    params = recommendation.get("action_params", {})
    current_budget = params.get("current_budget", 0)
    new_budget = params.get("new_budget", 0)

    if not current_budget or not new_budget:
        return False

    try:
        ratio = float(new_budget) / float(current_budget)
        max_ratio = getattr(settings, "max_budget_change_ratio", 1.5)
        return ratio > max_ratio
    except (TypeError, ValueError, ZeroDivisionError):
        return False


# Negative keywords that indicate a lesson warns against an action
_NEGATIVE_LESSON_KEYWORDS = re.compile(
    r"\b(don't|dont|avoid|failed|mistake|never|"
    r"caused problems|too aggressive|backfired|"
    r"caused instability|should not|shouldn't)\b",
    re.IGNORECASE,
)


async def _check_lesson_contradictions(
    recommendation: dict,
    role_id: str,
    session: Any,
) -> str | None:
    """Check if any high-confidence lessons warn against this action.

    Searches role memories for lessons matching the action type and platform.
    If a high-confidence (>=0.8) lesson with negative keywords is found,
    returns the lesson title as a block reason.

    Non-fatal: returns None on any error.

    Args:
        recommendation: The recommendation dict.
        role_id: The role that produced this recommendation.
        session: Async DB session.

    Returns:
        Lesson title string if a contradiction is found, None otherwise.
    """
    try:
        from src.db import service as db

        action_type = recommendation.get("action_type", "")
        platform = recommendation.get(
            "platform",
            recommendation.get("action_params", {}).get("platform", ""),
        )
        user_id = recommendation.get("user_id", "")

        search_terms = [t for t in [action_type, platform] if t]
        if not search_terms:
            return None

        for term in search_terms:
            lessons = await db.search_role_memories(
                session,
                user_id=user_id,
                role_id=role_id,
                memory_type="lesson",
                keyword=term,
                limit=5,
            )
            for lesson in lessons:
                confidence = getattr(lesson, "confidence", 0.0) or 0.0
                if confidence < 0.8:
                    continue
                content = getattr(lesson, "content", "") or ""
                title = getattr(lesson, "title", "") or ""
                combined = f"{title} {content}"
                if _NEGATIVE_LESSON_KEYWORDS.search(combined):
                    return title[:200]

        return None

    except Exception:
        return None
