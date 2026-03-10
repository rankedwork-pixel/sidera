"""Core agent orchestrator for Sidera.

Wires together the Anthropic API, tool registry, prompt templates,
and audit logging into a single ``SideraAgent`` class that drives daily
briefings, skill execution, and ad-hoc analysis queries.

Each method on ``SideraAgent`` creates a **fresh** Claude conversation,
sends the appropriate prompt, collects the response, and returns a typed
result dataclass. The agent is stateless between runs — all durable state
lives in PostgreSQL.

Usage::

    from src.agent.core import SideraAgent

    agent = SideraAgent()
    result = await agent.run_daily_briefing(
        user_id="user_123",
        account_ids=[
            {
                "platform": "google_ads",
                "account_id": "1234567890",
                "account_name": "Acme Store",
                "target_roas": 4.0,
                "target_cpa": 25.00,
                "monthly_budget_cap": 50_000,
                "currency": "USD",
            },
        ],
    )
    print(result.briefing_text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import structlog

import src.mcp_servers.actions  # noqa: F401, E402 — trigger @tool registration
import src.mcp_servers.claude_code_actions  # noqa: F401, E402
import src.mcp_servers.context  # noqa: F401, E402
import src.mcp_servers.delegation  # noqa: F401, E402
import src.mcp_servers.evolution  # noqa: F401, E402
import src.mcp_servers.memory  # noqa: F401, E402
import src.mcp_servers.messaging  # noqa: F401, E402
import src.mcp_servers.orchestration  # noqa: F401, E402
import src.mcp_servers.slack  # noqa: F401, E402
import src.mcp_servers.system  # noqa: F401, E402
import src.mcp_servers.web  # noqa: F401, E402
from src.agent.api_client import run_agent_loop
from src.agent.prompts import (
    CONVERSATION_SUPPLEMENT,
    DATA_COLLECTION_SYSTEM,
    MANAGER_DELEGATION_SUPPLEMENT,
    STRATEGIC_ANALYSIS_SYSTEM,
    build_analysis_only_prompt,
    build_analysis_prompt,
    build_conversation_prompt,
    build_daily_briefing_prompt,
    build_data_collection_prompt,
    build_strategic_prompt,
    get_base_system_prompt,
    get_system_prompt,
    get_timestamp_context,
)
from src.agent.tool_registry import get_global_registry
from src.cache.service import CACHE_TTL_BRIEFING_RESULT, cache_get, cache_set
from src.config import settings
from src.llm.provider import TaskType

logger = structlog.get_logger(__name__)


def _thinking_budget() -> int | None:
    """Return the extended thinking budget if enabled, else ``None``.

    Only Sonnet 4+, Opus 4+, and Haiku 4.5+ support extended thinking.
    The model capability check happens inside ``run_agent_loop()`` — this
    helper just resolves the config toggle.
    """
    if settings.extended_thinking_enabled:
        return settings.extended_thinking_budget_tokens
    return None


# Maximum chars of previous skill output injected into the system prompt
# during pipeline execution. Keeps context window manageable.
_MAX_PIPELINE_CONTEXT_CHARS = 4000

# Phase 1.5 compression threshold: only run Haiku compression when
# Phase 1 raw data exceeds this many characters.
_COMPRESSION_THRESHOLD_CHARS = 8000


# =============================================================================
# Result dataclasses
# =============================================================================


@dataclass
class BriefingResult:
    """Return value from a daily briefing run."""

    user_id: str
    briefing_text: str
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    degradation_status: str = "full"  # "full" | "partial" | "stale"
    tool_errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class QueryResult:
    """Return value from an ad-hoc analysis query."""

    user_id: str
    response_text: str
    cost: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""


@dataclass
class ConversationTurnResult:
    """Return value from a single conversation turn."""

    role_id: str
    response_text: str
    cost: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    turn_number: int = 0
    thinking_blocks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class HeartbeatResult:
    """Return value from a proactive heartbeat check-in."""

    role_id: str
    output_text: str
    cost: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    tool_calls_used: int = 0
    has_findings: bool = False


# =============================================================================
# Agent orchestrator
# =============================================================================


class SideraAgent:
    """Orchestrates the Sidera performance marketing agent.

    Each public method creates a fresh Claude conversation, sends the
    appropriate prompt with tools attached, collects the full response,
    and returns a typed result dataclass.

    The agent is completely stateless between runs. Durable state (metrics,
    analysis history, approval queue) lives in PostgreSQL and is loaded
    at the start of each run via tools.

    Args:
        model_override: If set, forces all runs to use this model instead
            of the per-tier defaults from ``settings``.
        google_ads_credentials: Optional per-user Google Ads credentials
            dict. If omitted the MCP server falls back to global env-var
            credentials.
        meta_credentials: Optional per-user Meta credentials dict.
            Reserved for future use.
    """

    def __init__(
        self,
        model_override: str | None = None,
        google_ads_credentials: dict[str, str] | None = None,
        meta_credentials: dict[str, str] | None = None,
    ) -> None:
        self._model_override = model_override
        self._google_ads_credentials = google_ads_credentials
        self._meta_credentials = meta_credentials  # reserved for future use
        self._log = logger.bind(component="sidera_agent")
        self._registry = get_global_registry()

        self._log.info(
            "tools.registered",
            tool_count=len(self._registry),
            tools=self._registry.get_tool_names(),
        )

    # ------------------------------------------------------------------
    # Tool helpers
    # ------------------------------------------------------------------

    def _get_tools(
        self,
        allowed: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Get Anthropic API tool definitions, optionally filtered.

        Args:
            allowed: If provided, only return tools whose names appear
                in this list/tuple.  ``None`` returns all tools.

        Returns:
            List of tool definition dicts in Anthropic API format.
        """
        if allowed:
            return self._registry.get_filtered_definitions(allowed)
        return self._registry.get_tool_definitions()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_daily_briefing(
        self,
        user_id: str,
        account_ids: list[dict[str, Any]],
        analysis_date: date | None = None,
    ) -> BriefingResult:
        """Run a full daily performance analysis cycle.

        This is the main entry point invoked by the Inngest daily cron.
        It builds the prompt with account context, creates a Claude
        conversation using the standard model (Sonnet), collects the
        agent's full analysis, and returns a ``BriefingResult``.

        If the analysis is complex enough to warrant deeper reasoning,
        a follow-up turn is sent with the reasoning model (Opus).

        Args:
            user_id: Identifier for the advertiser / user.
            account_ids: List of account context dicts. Each dict should
                contain ``platform``, ``account_id``, ``account_name``,
                and optionally ``target_roas``, ``target_cpa``,
                ``monthly_budget_cap``, ``currency``.
            analysis_date: The date this analysis covers. Defaults to today.

        Returns:
            ``BriefingResult`` with the full briefing text, extracted
            recommendations, cost metadata, and session ID.
        """
        if analysis_date is None:
            analysis_date = date.today()

        self._log.info(
            "daily_briefing.start",
            user_id=user_id,
            num_accounts=len(account_ids),
            analysis_date=analysis_date.isoformat(),
        )

        prompt = build_daily_briefing_prompt(
            accounts=account_ids,
            analysis_date=analysis_date,
        )

        # -- Turn 1: Main analysis with Sonnet --
        try:
            result1 = await run_agent_loop(
                system_prompt=get_system_prompt(),
                user_prompt=prompt,
                model=self._model_override or settings.model_standard,
                tools=self._get_tools(),
                max_turns=settings.max_tool_calls_per_cycle,
                task_type=TaskType.DATA_COLLECTION,
                thinking_budget=_thinking_budget(),
            )
        except Exception:
            self._log.exception("daily_briefing.error", user_id=user_id)
            raise

        collected_text = [result1.text]
        cost_info = dict(result1.cost)

        # -- Turn 2 (optional): Escalate to Opus if analysis was complex --
        if result1.turn_count >= 8 and not self._model_override:
            self._log.info(
                "daily_briefing.escalate_to_opus",
                user_id=user_id,
                num_turns=result1.turn_count,
            )
            try:
                result2 = await run_agent_loop(
                    system_prompt=get_system_prompt(),
                    user_prompt=(
                        result1.text + "\n\n" + "The analysis above is thorough. Now step back and "
                        "think strategically: are there any higher-level "
                        "cross-platform insights, budget reallocation "
                        "opportunities, or risks that the campaign-level "
                        "analysis might have missed? If so, add a "
                        "'## Strategic Insights' section. If not, confirm "
                        "that the analysis is complete."
                    ),
                    model=settings.model_reasoning,
                    tools=None,
                    max_turns=1,
                    task_type=TaskType.STRATEGY,
                    thinking_budget=_thinking_budget(),
                )
                collected_text.append(result2.text)
                cost_info["total_cost_usd"] = cost_info.get(
                    "total_cost_usd", 0.0
                ) + result2.cost.get("total_cost_usd", 0.0)
                cost_info["num_turns"] = cost_info.get("num_turns", 0) + result2.cost.get(
                    "num_turns", 0
                )
                cost_info["duration_ms"] = cost_info.get("duration_ms", 0) + result2.cost.get(
                    "duration_ms", 0
                )
            except Exception:
                self._log.exception("daily_briefing.opus_escalation_error", user_id=user_id)
                # Non-fatal: continue with Sonnet-only analysis

        briefing_text = "\n\n".join(t for t in collected_text if t)

        self._log.info(
            "daily_briefing.complete",
            user_id=user_id,
            text_length=len(briefing_text),
            cost=cost_info,
        )

        return BriefingResult(
            user_id=user_id,
            briefing_text=briefing_text,
            recommendations=self._extract_recommendations(briefing_text),
            cost=cost_info,
            session_id="",
        )

    async def run_daily_briefing_optimized(
        self,
        user_id: str,
        account_ids: list[dict[str, Any]],
        analysis_date: date | None = None,
        force_refresh: bool = False,
    ) -> BriefingResult:
        """Three-phase optimized daily briefing.

        Splits the analysis into three model tiers:
        - **Phase 1 (Haiku):** Pull all data via tools (~$0.02)
        - **Phase 2 (Sonnet):** Tactical analysis and briefing (~$0.15)
        - **Phase 3 (Opus):** Strategic insights layer (~$0.35)

        Also includes a Redis result cache so duplicate runs within 2 hours
        return instantly without re-running the analysis.

        Args:
            user_id: Identifier for the advertiser / user.
            account_ids: List of account context dicts.
            analysis_date: The date this analysis covers. Defaults to today.
            force_refresh: If True, bypass the result cache and re-run.

        Returns:
            ``BriefingResult`` with the full briefing text (tactical +
            strategic), extracted recommendations, cost metadata, and
            session ID.
        """
        if analysis_date is None:
            analysis_date = date.today()

        self._log.info(
            "daily_briefing_optimized.start",
            user_id=user_id,
            num_accounts=len(account_ids),
            analysis_date=analysis_date.isoformat(),
            force_refresh=force_refresh,
        )

        # -- Result cache check --
        cache_key = f"sidera:briefing:{user_id}:{analysis_date.isoformat()}"
        if not force_refresh:
            try:
                cached = await cache_get(cache_key)
                if cached is not None:
                    self._log.info(
                        "daily_briefing_optimized.cache_hit",
                        user_id=user_id,
                        cache_key=cache_key,
                    )
                    return BriefingResult(
                        user_id=cached.get("user_id", user_id),
                        briefing_text=cached.get("briefing_text", ""),
                        recommendations=cached.get("recommendations", []),
                        cost=cached.get("cost", {}),
                        session_id=cached.get("session_id", ""),
                    )
            except Exception:
                self._log.debug("daily_briefing_optimized.cache_check_failed")

        total_cost: dict[str, Any] = {
            "total_cost_usd": 0.0,
            "num_turns": 0,
            "duration_ms": 0,
            "phases": {},
        }

        # ============================================================
        # Phase 1: Haiku — data collection
        # ============================================================
        self._log.info("daily_briefing_optimized.phase1.start", model="haiku")

        collection_prompt = build_data_collection_prompt(
            accounts=account_ids,
            analysis_date=analysis_date,
        )

        phase1_session_id = ""

        try:
            phase1_result = await run_agent_loop(
                system_prompt=f"{get_timestamp_context()}\n\n{DATA_COLLECTION_SYSTEM}",
                user_prompt=collection_prompt,
                model=self._model_override or settings.model_fast,
                tools=self._get_tools(),
                max_turns=settings.max_tool_calls_per_cycle,
                task_type=TaskType.DATA_COLLECTION,
            )
            total_cost["phases"]["data_collection"] = phase1_result.cost
            total_cost["total_cost_usd"] += phase1_result.cost.get("total_cost_usd", 0.0)
            total_cost["num_turns"] += phase1_result.cost.get("num_turns", 0)
            total_cost["duration_ms"] += phase1_result.cost.get("duration_ms", 0)
        except Exception as phase1_exc:
            self._log.warning(
                "daily_briefing_optimized.phase1.fallback",
                user_id=user_id,
                error=str(phase1_exc),
            )
            from src.middleware.sentry_setup import capture_exception

            capture_exception(phase1_exc)

            # Graceful degradation: fall back to last known analysis
            last_known = await self._get_last_known_analysis(user_id, analysis_date)
            if last_known:
                last_known.degradation_status = "stale"
                last_known.cost = {
                    "total_cost_usd": 0.0,
                    "note": "stale_fallback",
                }
                return last_known
            # No fallback available — re-raise
            raise

        collected_data = phase1_result.text

        self._log.info(
            "daily_briefing_optimized.phase1.complete",
            collected_chars=len(collected_data),
            cost=total_cost["phases"].get("data_collection"),
        )

        # ============================================================
        # Phase 1.5: Haiku — data compression (optional, large outputs)
        # ============================================================
        if len(collected_data) > _COMPRESSION_THRESHOLD_CHARS:
            try:
                from src.agent.prompts import (
                    DATA_COMPRESSION_SYSTEM,
                    build_data_compression_prompt,
                )

                compression_prompt = build_data_compression_prompt(collected_data)
                compress_result = await run_agent_loop(
                    system_prompt=DATA_COMPRESSION_SYSTEM,
                    user_prompt=compression_prompt,
                    model=settings.model_fast,  # Haiku
                    tools=None,
                    max_turns=1,
                    task_type=TaskType.PHASE_COMPRESSION,
                )
                compressed = compress_result.text
                ratio = len(compressed) / len(collected_data) if collected_data else 1.0
                if ratio < 0.85:
                    self._log.info(
                        "daily_briefing_optimized.phase1_5.compressed",
                        original_chars=len(collected_data),
                        compressed_chars=len(compressed),
                        ratio=round(ratio, 2),
                    )
                    collected_data = compressed
                    total_cost["phases"]["data_compression"] = compress_result.cost
                    total_cost["total_cost_usd"] += compress_result.cost.get("total_cost_usd", 0.0)
                    total_cost["num_turns"] += compress_result.cost.get("num_turns", 0)
                    total_cost["duration_ms"] += compress_result.cost.get("duration_ms", 0)
                else:
                    self._log.info(
                        "daily_briefing_optimized.phase1_5.skipped",
                        reason="poor_compression_ratio",
                        ratio=round(ratio, 2),
                    )
            except Exception:
                self._log.debug(
                    "daily_briefing_optimized.phase1_5.error",
                    exc_info=True,
                )
                # Non-fatal: continue with original collected_data

        # ============================================================
        # Phase 2: Sonnet — tactical analysis (no tools)
        # ============================================================
        self._log.info("daily_briefing_optimized.phase2.start", model="sonnet")

        analysis_prompt = build_analysis_only_prompt(
            accounts=account_ids,
            collected_data=collected_data,
            analysis_date=analysis_date,
        )

        try:
            phase2_result = await run_agent_loop(
                system_prompt=get_system_prompt(),
                user_prompt=analysis_prompt,
                model=self._model_override or settings.model_standard,
                tools=None,
                max_turns=1,
                task_type=TaskType.ANALYSIS,
                thinking_budget=_thinking_budget(),
            )
            total_cost["phases"]["tactical_analysis"] = phase2_result.cost
            total_cost["total_cost_usd"] += phase2_result.cost.get("total_cost_usd", 0.0)
            total_cost["num_turns"] += phase2_result.cost.get("num_turns", 0)
            total_cost["duration_ms"] += phase2_result.cost.get("duration_ms", 0)
        except Exception:
            self._log.exception("daily_briefing_optimized.phase2.error", user_id=user_id)
            raise

        briefing_text = phase2_result.text

        self._log.info(
            "daily_briefing_optimized.phase2.complete",
            briefing_chars=len(briefing_text),
            cost=total_cost["phases"].get("tactical_analysis"),
        )

        # ============================================================
        # Phase 3: Opus — strategic insights (skipped if stable)
        # ============================================================
        volatility = self._compute_volatility_score(collected_data)
        skip_opus = volatility < 10.0 and not force_refresh

        if skip_opus:
            self._log.info(
                "daily_briefing_optimized.phase3.skipped",
                volatility=volatility,
                reason="stable_metrics",
            )
            total_cost["phases"]["strategic_analysis"] = {
                "skipped": True,
                "volatility": volatility,
            }
        else:
            self._log.info(
                "daily_briefing_optimized.phase3.start",
                model="opus",
                volatility=volatility,
            )

            strategic_prompt = build_strategic_prompt(
                accounts=account_ids,
                briefing_text=briefing_text,
                analysis_date=analysis_date,
            )

            try:
                phase3_result = await run_agent_loop(
                    system_prompt=f"{get_timestamp_context()}\n\n{STRATEGIC_ANALYSIS_SYSTEM}",
                    user_prompt=strategic_prompt,
                    model=self._model_override or settings.model_reasoning,
                    tools=None,
                    max_turns=1,
                    task_type=TaskType.STRATEGY,
                    thinking_budget=_thinking_budget(),
                )
                total_cost["phases"]["strategic_analysis"] = phase3_result.cost
                total_cost["total_cost_usd"] += phase3_result.cost.get("total_cost_usd", 0.0)
                total_cost["num_turns"] += phase3_result.cost.get("num_turns", 0)
                total_cost["duration_ms"] += phase3_result.cost.get("duration_ms", 0)
            except Exception:
                self._log.exception("daily_briefing_optimized.phase3.error", user_id=user_id)
                raise

            strategic_text = phase3_result.text

            # Append strategic insights if Opus had something to add
            if strategic_text and "No additional strategic insights" not in strategic_text:
                briefing_text = briefing_text + "\n\n" + strategic_text

            self._log.info(
                "daily_briefing_optimized.phase3.complete",
                strategic_chars=len(strategic_text),
                cost=total_cost["phases"].get("strategic_analysis"),
            )

        # ============================================================
        # Build result and cache it
        # ============================================================
        recommendations = self._extract_recommendations(briefing_text)

        result = BriefingResult(
            user_id=user_id,
            briefing_text=briefing_text,
            recommendations=recommendations,
            cost=total_cost,
            session_id=phase1_session_id,
        )

        # Cache result for 2 hours
        try:
            await cache_set(
                cache_key,
                {
                    "user_id": user_id,
                    "briefing_text": briefing_text,
                    "recommendations": recommendations,
                    "cost": total_cost,
                    "session_id": phase1_session_id,
                },
                ttl_seconds=CACHE_TTL_BRIEFING_RESULT,
            )
        except Exception:
            self._log.debug("daily_briefing_optimized.cache_set_failed")

        self._log.info(
            "daily_briefing_optimized.complete",
            user_id=user_id,
            text_length=len(briefing_text),
            total_cost=total_cost,
        )

        return result

    async def run_skill(
        self,
        skill: "SkillDefinition",  # noqa: F821 — string annotation avoids circular import
        user_id: str,
        account_ids: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
        analysis_date: date | None = None,
        role_context: str = "",
    ) -> BriefingResult:
        """Run a skill through the agent.

        Composes a skill-specific system prompt from ``BASE_SYSTEM_PROMPT``
        plus the skill's supplement, output format, and business guidance.
        When ``role_context`` is provided (from a role/department execution),
        it is injected between the base prompt and the skill supplement.

        Args:
            skill: The ``SkillDefinition`` to execute.
            user_id: Identifier for the advertiser / user.
            account_ids: List of account context dicts (same format as
                ``run_daily_briefing``).
            params: Optional parameters to inject into the prompt template
                via ``str.format_map``.
            analysis_date: Reference date for the analysis. Defaults to today.
            role_context: Pre-composed context from department + role
                definitions. Injected between BASE_SYSTEM_PROMPT and the
                skill's system_supplement. Empty string by default.

        Returns:
            ``BriefingResult`` with the skill output, extracted
            recommendations, cost metadata, and session ID.
        """
        if analysis_date is None:
            analysis_date = date.today()

        # Reset per-turn traversal budget for cross-skill references
        from src.mcp_servers.context import reset_reference_load_count

        reset_reference_load_count()

        self._log.info(
            "skill.start",
            skill_id=skill.id,
            user_id=user_id,
            num_accounts=len(account_ids),
            analysis_date=analysis_date.isoformat(),
        )

        # -- Compose skill-specific system prompt --
        system_prompt = get_base_system_prompt()
        if role_context:
            system_prompt += "\n\n" + role_context
        system_prompt += "\n\n" + skill.system_supplement

        # Inject context files (folder-based skills)
        # For multi-turn skills (max_turns > 1), use lazy loading:
        # inject a manifest instead of full text, agent loads on demand.
        # For single-turn skills, inject full text (no second turn to load).
        if skill.context_files or skill.references:
            from src.skills.schema import load_context_text

            use_lazy = skill.max_turns > 1
            context_text = load_context_text(skill, lazy=use_lazy)
            if context_text:
                system_prompt += "\n\n" + context_text
                self._log.info(
                    "skill.context_injected",
                    skill_id=skill.id,
                    context_chars=len(context_text),
                    lazy=use_lazy,
                )

        if skill.output_format:
            system_prompt += "\n\n# Output Format\n\n" + skill.output_format
        if skill.business_guidance:
            system_prompt += "\n\n# Business Guidance\n\n" + skill.business_guidance

        # -- Inject previous skill output (pipeline mode) --
        _prev = (params or {}).get("previous_output", "")
        if _prev:
            truncated = _prev[:_MAX_PIPELINE_CONTEXT_CHARS]
            if len(_prev) > _MAX_PIPELINE_CONTEXT_CHARS:
                truncated += "\n\n[... truncated ...]"
            system_prompt += (
                "\n\n# Previous Skill Output\n\n"
                "The following was produced by the previous skill "
                "in this pipeline. Use it as context for your "
                "analysis.\n\n" + truncated
            )
            self._log.info(
                "skill.pipeline_context_injected",
                skill_id=skill.id,
                previous_output_chars=len(_prev),
                truncated=len(_prev) > _MAX_PIPELINE_CONTEXT_CHARS,
            )

        # -- Build accounts block for template rendering --
        account_lines: list[str] = []
        for acct in account_ids:
            platform = acct.get("platform", "unknown")
            acct_id = acct.get("account_id", "unknown")
            acct_name = acct.get("account_name", "Unnamed Account")
            currency = acct.get("currency", "USD")
            account_lines.append(
                f"- **{acct_name}** ({platform}, ID: {acct_id}, currency: {currency})"
            )
        accounts_block = "\n".join(account_lines) if account_lines else "No accounts configured."

        # -- Render prompt template --
        template_params: dict[str, Any] = {
            "analysis_date": analysis_date.isoformat(),
            "accounts_block": accounts_block,
            "lookback_days": 7,
            "extra_instructions": "",
            "previous_output": "",  # Pipeline: set by RoleExecutor
        }
        if params:
            template_params.update(params)

        prompt = skill.prompt_template.format_map(template_params)

        # -- Resolve model --
        resolved_model = self._resolve_model(skill.model)

        # -- Execute agent loop with skill-specific tools --
        try:
            result = await run_agent_loop(
                system_prompt=system_prompt,
                user_prompt=prompt,
                model=resolved_model,
                tools=self._get_tools(list(skill.tools_required)),
                max_turns=skill.max_turns,
                task_type=TaskType.SKILL_EXECUTION,
                thinking_budget=_thinking_budget(),
            )
        except Exception:
            self._log.exception(
                "skill.error",
                skill_id=skill.id,
                user_id=user_id,
            )
            raise

        output_text = result.text

        self._log.info(
            "skill.complete",
            skill_id=skill.id,
            user_id=user_id,
            text_length=len(output_text),
            cost=result.cost,
        )

        return BriefingResult(
            user_id=user_id,
            briefing_text=output_text,
            recommendations=self._extract_recommendations(output_text),
            cost=result.cost,
            session_id="",
            tool_errors=result.tool_errors,
        )

    async def run_query(
        self,
        user_id: str,
        query_text: str,
        account_ids: list[dict[str, Any]],
        analysis_date: date | None = None,
    ) -> QueryResult:
        """Run an ad-hoc analysis query.

        Used when an advertiser asks a specific question like "Why did
        CPA spike yesterday?" or "Compare search vs pmax performance
        this month." Uses ``run_agent_loop()`` with Sonnet.

        Args:
            user_id: Identifier for the advertiser / user.
            query_text: The user's natural-language question.
            account_ids: List of account context dicts.
            analysis_date: Reference date for relative queries.

        Returns:
            ``QueryResult`` with the response text, cost metadata,
            and session ID.
        """
        if analysis_date is None:
            analysis_date = date.today()

        self._log.info(
            "query.start",
            user_id=user_id,
            query_preview=query_text[:120],
        )

        prompt = build_analysis_prompt(
            query=query_text,
            accounts=account_ids,
            analysis_date=analysis_date,
        )

        try:
            result = await run_agent_loop(
                system_prompt=get_system_prompt(),
                user_prompt=prompt,
                model=self._model_override or settings.model_standard,
                tools=self._get_tools(),
                max_turns=settings.max_tool_calls_per_cycle,
                task_type=TaskType.ANALYSIS,
                thinking_budget=_thinking_budget(),
            )
        except Exception:
            self._log.exception("query.error", user_id=user_id)
            raise

        self._log.info(
            "query.complete",
            user_id=user_id,
            text_length=len(result.text),
            cost=result.cost,
        )

        return QueryResult(
            user_id=user_id,
            response_text=result.text,
            cost=result.cost,
            session_id="",
        )

    async def run_conversation_turn(
        self,
        role_id: str,
        role_context: str,
        thread_history: list[dict[str, Any]],
        current_message: str,
        user_id: str,
        bot_user_id: str = "",
        turn_number: int = 0,
        *,
        max_turns: int | None = None,
        disable_tools: bool = False,
        is_manager: bool = False,
        channel_id: str = "",
        message_ts: str = "",
        image_content: list[dict[str, Any]] | None = None,
        user_clearance: str = "public",
    ) -> ConversationTurnResult:
        """Run a single conversation turn for an interactive Slack thread.

        Creates a fresh Claude conversation with the role's context plus
        ``CONVERSATION_SUPPLEMENT``, sends the formatted thread history and
        current message, collects the response, and returns a typed result.

        The agent is stateless between turns — thread history is loaded from
        Slack on each invocation and formatted into the prompt.

        Args:
            role_id: The role handling this conversation (e.g. "strategist").
            role_context: Pre-composed role context from
                ``compose_role_context()`` — includes department context,
                role persona, and memory.
            thread_history: Message history from
                ``SlackConnector.get_thread_history()``. Each dict has
                ``user``, ``text``, ``ts``, ``bot_id``, ``is_bot`` keys.
            current_message: The latest user message to respond to.
            user_id: Identifier for the user in the conversation.
            bot_user_id: The Slack bot user ID, used to distinguish bot
                messages in thread history.
            turn_number: The current turn number in the conversation.
            max_turns: Override for maximum API round-trips. ``None`` uses
                the default ``settings.conversation_tool_calls_per_turn``.
                Set to ``1`` for single-turn (no tool calls) responses.
            disable_tools: If ``True``, pass no tools to the agent — it
                responds from context only, with no tool calling overhead.
                Used for meeting turns where latency is critical.
            is_manager: If ``True``, include the ``delegate_to_role``
                tool and manager delegation supplement in the prompt.
            channel_id: Slack channel ID, passed to the prompt so the
                agent can use ``react_to_message``.
            message_ts: Timestamp of the current user message, passed to
                the prompt so the agent can react to it.
            image_content: Optional list of Anthropic image content blocks
                (base64-encoded). When present, the user prompt is sent as
                a multimodal content list instead of a plain string.

        Returns:
            ``ConversationTurnResult`` with the response text, cost metadata,
            and turn number.
        """
        # Reset per-turn traversal budget for cross-skill references
        from src.mcp_servers.context import reset_reference_load_count

        reset_reference_load_count()

        self._log.info(
            "conversation_turn.start",
            role_id=role_id,
            user_id=user_id,
            turn_number=turn_number,
            history_length=len(thread_history),
        )

        # -- Build system prompt: BASE + role context + CONVERSATION_SUPPLEMENT --
        system_prompt = get_base_system_prompt()
        if role_context:
            system_prompt += "\n\n" + role_context
        system_prompt += "\n\n" + CONVERSATION_SUPPLEMENT
        if is_manager:
            system_prompt += "\n\n" + MANAGER_DELEGATION_SUPPLEMENT

        # -- Inject information clearance context --
        from src.agent.prompts import build_clearance_context

        clearance_ctx = build_clearance_context(user_clearance)
        if clearance_ctx:
            system_prompt += "\n\n" + clearance_ctx

        # -- Build user prompt from thread history --
        prompt_text = build_conversation_prompt(
            thread_history=thread_history,
            current_message=current_message,
            bot_user_id=bot_user_id,
            channel_id=channel_id,
            message_ts=message_ts,
        )

        # Assemble multimodal content if images are present
        user_prompt: str | list[dict[str, Any]]
        if image_content:
            img_count = len(image_content)
            prompt_text += (
                f"\n\n_The user attached {img_count} image(s) to this "
                f"message. Analyze them as part of your response._"
            )
            user_prompt = [
                {"type": "text", "text": prompt_text},
                *image_content,
            ]
            self._log.info(
                "conversation_turn.multimodal",
                role_id=role_id,
                image_count=img_count,
            )
        else:
            user_prompt = prompt_text

        # Resolve tools and max_turns — meetings disable tools for speed
        effective_tools = [] if disable_tools else self._get_tools()
        # Exclude delegation/consultation tools for non-manager roles
        _manager_only_tools = {"delegate_to_role", "consult_peer"}
        if not is_manager and not disable_tools:
            effective_tools = [t for t in effective_tools if t["name"] not in _manager_only_tools]
        effective_max_turns = (
            max_turns if max_turns is not None else (settings.conversation_tool_calls_per_turn)
        )

        try:
            result = await run_agent_loop(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self._model_override or settings.model_standard,
                tools=effective_tools,
                max_turns=effective_max_turns,
                task_type=TaskType.CONVERSATION,
                thinking_budget=_thinking_budget(),
            )
        except Exception:
            self._log.exception(
                "conversation_turn.error",
                role_id=role_id,
                user_id=user_id,
                turn_number=turn_number,
            )
            raise

        self._log.info(
            "conversation_turn.complete",
            role_id=role_id,
            user_id=user_id,
            turn_number=turn_number,
            response_length=len(result.text),
            cost=result.cost,
        )

        return ConversationTurnResult(
            role_id=role_id,
            response_text=result.text,
            cost=result.cost,
            session_id="",
            turn_number=turn_number,
            thinking_blocks=result.thinking_blocks,
        )

    # ------------------------------------------------------------------
    # Proactive heartbeat check-in
    # ------------------------------------------------------------------

    async def run_heartbeat_turn(
        self,
        role_id: str,
        role_context: str,
        user_id: str = "heartbeat",
        *,
        is_manager: bool = False,
        heartbeat_model: str = "",
        pending_messages_summary: str = "",
        user_prompt_override: str = "",
    ) -> HeartbeatResult:
        """Run a proactive heartbeat check-in for a role.

        Creates a fresh Claude conversation with the role's context plus
        ``HEARTBEAT_SUPPLEMENT``, gives an open-ended investigative prompt,
        and lets the agent freely use tools to check its domain.

        Unlike briefing runs (which execute specific skills), heartbeats
        let the agent decide what to investigate. Write operations remain
        gated by the normal approval flow.

        Args:
            role_id: The role running this heartbeat.
            role_context: Pre-composed role context from
                ``compose_role_context()`` — includes department context,
                role persona, memory, and pending messages.
            user_id: Identifier for audit purposes (default "heartbeat").
            is_manager: If ``True``, include delegation/consultation tools.
            heartbeat_model: Model override for this heartbeat. Empty string
                falls back to ``settings.heartbeat_model`` → ``settings.model_fast``.
            pending_messages_summary: Short summary of pending messages
                for inclusion in the heartbeat user prompt.
            user_prompt_override: If provided, replaces the default heartbeat
                prompt. Used by webhook-triggered investigations to include
                event context in the user prompt.

        Returns:
            ``HeartbeatResult`` with output text, cost, and findings flag.
        """
        from src.agent.prompts import (
            HEARTBEAT_SUPPLEMENT,
            MANAGER_DELEGATION_SUPPLEMENT,
            build_heartbeat_prompt,
        )

        # Reset per-turn traversal budget for cross-skill references
        from src.mcp_servers.context import reset_reference_load_count

        reset_reference_load_count()

        self._log.info(
            "heartbeat.start",
            role_id=role_id,
            user_id=user_id,
        )

        # -- Build system prompt: BASE + role context + HEARTBEAT_SUPPLEMENT --
        system_prompt = get_base_system_prompt()
        if role_context:
            system_prompt += "\n\n" + role_context
        # Skip default HEARTBEAT_SUPPLEMENT if user_prompt_override is provided
        # (the override already includes WEBHOOK_REACTION_SUPPLEMENT in role_context)
        if not user_prompt_override:
            system_prompt += "\n\n" + HEARTBEAT_SUPPLEMENT
        if is_manager:
            system_prompt += "\n\n" + MANAGER_DELEGATION_SUPPLEMENT

        # -- Build user prompt --
        prompt = user_prompt_override or build_heartbeat_prompt(
            role_name=role_id,
            pending_messages_summary=pending_messages_summary,
        )

        # -- Resolve model --
        # heartbeat_model may be an alias ("haiku", "sonnet", "opus") or
        # a full model ID.  Resolve aliases here directly (not via
        # _resolve_model which unconditionally prefers _model_override).
        _alias_map = {
            "haiku": settings.model_fast,
            "sonnet": settings.model_standard,
            "opus": settings.model_reasoning,
        }
        raw_model = (
            heartbeat_model
            or self._model_override
            or settings.heartbeat_model
            or settings.model_fast
        )
        model = _alias_map.get(raw_model, raw_model)

        # -- Resolve tools --
        effective_tools = self._get_tools()
        _manager_only_tools = {"delegate_to_role", "consult_peer"}
        if not is_manager:
            effective_tools = [t for t in effective_tools if t["name"] not in _manager_only_tools]

        try:
            result = await run_agent_loop(
                system_prompt=system_prompt,
                user_prompt=prompt,
                model=model,
                tools=effective_tools,
                max_turns=settings.heartbeat_max_tool_calls,
                task_type=TaskType.HEARTBEAT,
                thinking_budget=_thinking_budget(),
            )
        except Exception:
            self._log.exception(
                "heartbeat.error",
                role_id=role_id,
                user_id=user_id,
            )
            raise

        # Determine if findings were reported (not just "all clear")
        output = result.text.strip().lower()
        has_findings = not any(
            phrase in output
            for phrase in (
                "all clear",
                "nothing unusual",
                "everything looks normal",
                "no issues",
                "nothing to report",
            )
        )

        self._log.info(
            "heartbeat.complete",
            role_id=role_id,
            user_id=user_id,
            has_findings=has_findings,
            response_length=len(result.text),
            cost=result.cost,
        )

        return HeartbeatResult(
            role_id=role_id,
            output_text=result.text,
            cost=result.cost,
            session_id="",
            tool_calls_used=result.cost.get("tool_calls", 0),
            has_findings=has_findings,
        )

    # ------------------------------------------------------------------
    # Manager delegation and synthesis
    # ------------------------------------------------------------------

    async def run_delegation_decision(
        self,
        manager_name: str,
        manager_persona: str,
        own_results_summary: str,
        available_roles: list[dict[str, Any]],
        model: str | None = None,
    ) -> dict[str, Any]:
        """Run a single LLM call to decide which sub-roles to activate.

        Uses a structured prompt that asks the LLM to return JSON with
        activate/skip decisions. Falls back to activating all roles if
        the response cannot be parsed as valid JSON.

        Args:
            manager_name: The manager role's display name.
            manager_persona: The manager's persona description.
            own_results_summary: Summary of manager's own skill outputs.
            available_roles: List of dicts describing each managed role.
            model: Model to use. Defaults to settings.model_standard (Sonnet).

        Returns:
            Dict with "activate" and "skip" lists.
        """
        import json

        from src.agent.prompts import build_delegation_prompt

        prompt = build_delegation_prompt(
            manager_name=manager_name,
            manager_persona=manager_persona,
            own_results_summary=own_results_summary,
            available_roles=available_roles,
        )

        resolved_model = model or self._model_override or settings.model_standard

        try:
            result = await run_agent_loop(
                system_prompt="You are a delegation decision engine. Return only valid JSON.",
                user_prompt=prompt,
                model=resolved_model,
                tools=None,
                max_turns=1,
                task_type=TaskType.DELEGATION,
            )
        except Exception:
            self._log.exception("delegation_decision.error")
            return self._fallback_activate_all(available_roles)

        raw_text = result.text

        # Try to parse JSON from the response
        try:
            # Handle markdown code blocks
            text = raw_text.strip()
            if "```json" in text:
                text = text.split("```json", 1)[1].split("```", 1)[0]
            elif "```" in text:
                text = text.split("```", 1)[1].split("```", 1)[0]
            decision = json.loads(text.strip())
            if "activate" in decision:
                self._log.info(
                    "delegation_decision.complete",
                    activate_count=len(decision.get("activate", [])),
                    skip_count=len(decision.get("skip", [])),
                )
                return decision
        except (json.JSONDecodeError, IndexError, KeyError):
            self._log.warning(
                "delegation_decision.parse_failed",
                raw_text_preview=raw_text[:200],
            )

        return self._fallback_activate_all(available_roles)

    async def run_synthesis(
        self,
        manager_name: str,
        manager_persona: str,
        own_results: str,
        sub_role_results: str,
        synthesis_prompt: str = "",
    ) -> str:
        """Run a single LLM call to synthesize all role outputs.

        Args:
            manager_name: The manager role's display name.
            manager_persona: The manager's persona description.
            own_results: Full text of manager's own skill outputs.
            sub_role_results: Formatted text of all sub-role outputs.
            synthesis_prompt: Custom synthesis instructions from the role.

        Returns:
            Synthesis text.
        """
        from src.agent.prompts import build_synthesis_prompt

        prompt = build_synthesis_prompt(
            manager_name=manager_name,
            manager_persona=manager_persona,
            own_results=own_results,
            sub_role_results=sub_role_results,
            synthesis_instructions=synthesis_prompt,
        )

        resolved_model = self._model_override or settings.model_standard

        try:
            result = await run_agent_loop(
                system_prompt=get_base_system_prompt(),
                user_prompt=prompt,
                model=resolved_model,
                tools=None,
                max_turns=1,
                task_type=TaskType.SYNTHESIS,
            )
        except Exception:
            self._log.exception("synthesis.error")
            raise

        self._log.info(
            "synthesis.complete",
            output_length=len(result.text),
        )

        return result.text

    async def run_reflection(
        self,
        role_id: str,
        role_name: str,
        output_text: str,
        skill_ids: list[str] | None = None,
        principles: tuple[str, ...] | list[str] = (),
        peer_role_ids: tuple[str, ...] | list[str] = (),
        tool_errors: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a cheap Haiku reflection after a role execution.

        Asks a single question: "What was hard? What data was missing?
        What would you do differently?" Returns structured insight and
        lesson memories ready for ``db_service.save_memory()``.

        When ``principles`` are provided, the reflection also checks whether
        any observation supports, contradicts, or refines a principle, and
        tags the memory with ``related_principle`` and ``principle_alignment``.

        When ``peer_role_ids`` are provided (roles that have this role in
        their ``learning_channels``), the reflection also asks which
        observations should be shared with those peers as cross-role
        learnings.

        When ``tool_errors`` are provided (errors captured during tool
        dispatch in the preceding agent run), they are injected into the
        reflection prompt so the LLM can generate targeted lessons about
        recurring tool failures and potential workarounds.

        Cost: ~$0.005-0.02 per call (Haiku, single turn, no tools,
        ~500 tokens input, ~200 tokens output).

        Args:
            role_id: The role that just completed.
            role_name: Human-readable role name.
            output_text: The full output from the role run (truncated
                to 3000 chars to keep token cost low).
            skill_ids: Optional list of skill IDs that were executed.
            principles: Optional role principles (decision heuristics).
            peer_role_ids: Optional list of role IDs that accept learnings
                from this role (reverse lookup of ``learning_channels``).
            tool_errors: Optional list of tool error dicts captured during
                the agent run. Each dict has ``tool_name``,
                ``error_message``, and ``input_summary``.

        Returns:
            List of memory entry dicts (type ``insight`` or ``lesson``)
            ready for ``save_memory()``. Empty list on error.
        """
        import json
        from datetime import date as date_cls

        # Truncate output to keep cost minimal
        truncated = output_text[:3000]
        skill_list = ", ".join(skill_ids) if skill_ids else "unknown"

        # Build principles section if available
        principles_section = ""
        if principles:
            numbered = "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(principles))
            principles_section = (
                "\n\nYour role has these decision-making principles:\n"
                f"{numbered}\n\n"
                "For each observation, if it relates to one of these principles, "
                "include these extra fields:\n"
                '- "related_principle": the principle text (verbatim)\n'
                '- "principle_alignment": one of "supports", "contradicts", or "refines"\n'
            )

        # Build peer sharing section if applicable
        peer_section = ""
        if peer_role_ids:
            peer_list = ", ".join(peer_role_ids)
            peer_section = (
                f"\n\nThese peer roles accept learnings from you: {peer_list}\n"
                "For observations that would be valuable to a specific peer, "
                "add this field:\n"
                '- "share_with": a list of role IDs from the above list that '
                "should receive this learning. Only include roles where the "
                "observation is genuinely relevant to their domain.\n"
            )

        # Build tool error section if errors were captured
        error_section = ""
        if tool_errors:
            error_lines = []
            for err in tool_errors[:5]:  # Cap at 5 errors
                error_lines.append(
                    f"- Tool '{err.get('tool_name', '?')}': "
                    f"{err.get('error_message', 'unknown error')[:200]}"
                )
            error_section = (
                "\n\nTool errors encountered during this run:\n"
                + "\n".join(error_lines)
                + "\n\nConsider whether these errors suggest a recurring "
                "pattern or a skill/tool improvement.\n"
            )

        reflection_prompt = (
            f"You just completed a run as '{role_name}' (role_id={role_id}) "
            f"executing skills: {skill_list}.\n\n"
            f"Here is a summary of your output:\n\n{truncated}\n\n"
            "Reflect briefly on this run and respond with a JSON array of "
            "observations. Each observation should have:\n"
            '- "type": one of "insight" (a useful pattern or finding), '
            '"lesson" (something that went wrong or could be improved), '
            '"error_pattern" (a recurring tool failure worth tracking), or '
            '"gap" (a request or need you encountered that falls outside your '
            "skills and outside any existing role's domain)\n"
            '- "title": a short title (max 100 chars)\n'
            '- "content": a 1-2 sentence explanation\n'
            '- "confidence": 0.0-1.0 how confident you are this is valuable\n'
            f"{principles_section}"
            f"{peer_section}"
            f"{error_section}\n"
            'For "gap" type observations, also include:\n'
            '- "domain": a 1-3 word label for the missing capability area '
            '(e.g. "compliance", "customer support", "infrastructure")\n\n'
            'For "error_pattern" observations, also include:\n'
            '- "error_tools": list of tool names involved in the error\n\n'
            "Focus on:\n"
            "- What data was missing or hard to get?\n"
            "- What assumptions did you have to make?\n"
            "- What would you do differently next time?\n"
            "- Any patterns worth remembering for future runs?\n"
            "- Were there requests or needs that fell completely outside your "
            "role's capabilities, where no existing role could help?\n"
            "- Did any tool errors indicate a systemic issue?\n\n"
            "Return ONLY the JSON array. If nothing noteworthy, return []."
        )

        resolved_model = self._model_override or settings.model_fast

        try:
            result = await run_agent_loop(
                system_prompt=(
                    "You are a reflective AI analyst. Respond only with "
                    "a valid JSON array. No markdown, no explanation."
                ),
                user_prompt=reflection_prompt,
                model=resolved_model,
                tools=None,
                max_turns=1,
                task_type=TaskType.REFLECTION,
            )
        except Exception:
            self._log.warning("reflection.llm_error", role_id=role_id)
            return []

        # Parse the JSON response
        try:
            raw_text = result.text.strip()
            # Strip markdown code fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[-1]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3].strip()

            observations = json.loads(raw_text)
            if not isinstance(observations, list):
                return []
        except (json.JSONDecodeError, ValueError):
            self._log.warning(
                "reflection.parse_error",
                role_id=role_id,
                raw_text=result.text[:200],
            )
            return []

        # Convert to memory entries
        memories: list[dict[str, Any]] = []
        today = date_cls.today()

        for obs in observations[:5]:  # Max 5 reflections per run
            obs_type = obs.get("type", "insight")
            if obs_type not in ("insight", "lesson", "gap", "error_pattern"):
                obs_type = "insight"

            title = str(obs.get("title", ""))[:100]
            content = str(obs.get("content", ""))
            confidence = min(1.0, max(0.0, float(obs.get("confidence", 0.7))))

            if not title or not content:
                continue

            evidence: dict[str, Any] = {
                "source": "post_run_reflection",
                "skills_executed": skill_ids or [],
            }

            # Capture principle link if present
            related_principle = obs.get("related_principle", "")
            principle_alignment = obs.get("principle_alignment", "")
            if related_principle and principle_alignment in (
                "supports",
                "contradicts",
                "refines",
            ):
                evidence["related_principle"] = related_principle
                evidence["principle_alignment"] = principle_alignment

            # Capture gap domain for gap detection observations
            if obs_type == "gap":
                gap_domain = obs.get("domain", "")
                if gap_domain:
                    evidence["gap_domain"] = gap_domain

            # Capture error tool names for error_pattern observations
            if obs_type == "error_pattern":
                error_tools = obs.get("error_tools", [])
                if isinstance(error_tools, list) and error_tools:
                    evidence["error_tools"] = error_tools
                evidence["error_type"] = "tool_failure"

            # Capture share_with for cross-role learning
            share_with = obs.get("share_with", [])
            if isinstance(share_with, list) and share_with and peer_role_ids:
                # Only keep role IDs that are valid peers
                valid_peers = [r for r in share_with if r in peer_role_ids]
                if valid_peers:
                    evidence["share_with"] = valid_peers

            # Map special types to DB MemoryType equivalents:
            # - "gap" → "insight" (with gap_domain in evidence)
            # - "error_pattern" → "lesson" (with error_tools in evidence)
            if obs_type == "gap":
                db_memory_type = "insight"
                tag = "[Gap Detection]"
            elif obs_type == "error_pattern":
                db_memory_type = "lesson"
                tag = "[Error Pattern]"
            else:
                db_memory_type = obs_type
                tag = "[Reflection]"

            memories.append(
                {
                    "role_id": role_id,
                    "department_id": "",  # filled by caller
                    "memory_type": db_memory_type,
                    "title": title,
                    "content": f"[{today}] {tag} {content}",
                    "confidence": confidence,
                    "source_skill_id": f"reflection:{role_id}",
                    "source_run_date": today,
                    "evidence": evidence,
                }
            )

        self._log.info(
            "reflection.complete",
            role_id=role_id,
            memories_generated=len(memories),
            cost_model=resolved_model,
        )

        return memories

    @staticmethod
    def _fallback_activate_all(
        available_roles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fallback: activate all available roles.

        Used when the delegation LLM call fails or returns unparseable output.

        Args:
            available_roles: List of role info dicts.

        Returns:
            Decision dict that activates all roles.
        """
        return {
            "activate": [
                {
                    "role_id": r.get("role_id", r.get("id", "")),
                    "reason": "Fallback: activating all roles due to delegation parse failure",
                    "priority": i + 1,
                }
                for i, r in enumerate(available_roles)
            ],
            "skip": [],
        }

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    def _resolve_model(self, model_name: str) -> str:
        """Map a logical model tier name to the configured model ID.

        If ``_model_override`` is set on the agent, it takes precedence
        over any mapping. Otherwise the logical names ``haiku``,
        ``sonnet``, and ``opus`` are resolved to their configured model
        IDs via ``settings``.

        Args:
            model_name: Logical model tier (``"haiku"``, ``"sonnet"``,
                ``"opus"``) or an explicit model ID.

        Returns:
            Resolved model ID string.
        """
        model_map = {
            "haiku": settings.model_fast,
            "sonnet": settings.model_standard,
            "opus": settings.model_reasoning,
        }
        return self._model_override or model_map.get(model_name, settings.model_standard)

    # ------------------------------------------------------------------
    # Response extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_recommendations(briefing_text: str) -> list[dict[str, Any]]:
        """Extract structured recommendations from the briefing text.

        Performs a lightweight parse of the recommendations section.
        Each recommendation is expected to have Action, Reasoning,
        Projected Impact, and Risk Level fields. If parsing fails,
        returns the raw text blocks.

        Args:
            briefing_text: The full briefing output from the agent.

        Returns:
            List of recommendation dicts, each with ``action``,
            ``reasoning``, ``projected_impact``, and ``risk_level`` keys.
        """
        recommendations: list[dict[str, Any]] = []

        # Find the Recommendations section
        sections = briefing_text.split("## ")
        rec_section = ""
        for section in sections:
            if section.strip().lower().startswith("recommendations"):
                rec_section = section
                break

        if not rec_section:
            return recommendations

        def _field_match(text: str, field_name: str) -> bool:
            """Check if text starts with a field label in any format."""
            return (
                text.startswith(f"{field_name}:")
                or text.startswith(f"**{field_name}:**")
                or text.startswith(f"**{field_name}**:")
            )

        def _extract_value(text: str) -> str:
            """Extract the value after a field label."""
            return text.split(":", 1)[-1].strip().strip("*")

        current_rec: dict[str, Any] = {}
        for line in rec_section.split("\n"):
            stripped = line.strip().lstrip("- *")
            lower = stripped.lower()

            if lower.startswith("action:"):
                if current_rec.get("action"):
                    recommendations.append(current_rec)
                    current_rec = {}
                current_rec["action"] = stripped[len("action:") :].strip().strip("*")
            elif lower.startswith("**action:**") or lower.startswith("**action**:"):
                if current_rec.get("action"):
                    recommendations.append(current_rec)
                    current_rec = {}
                # Strip markdown bold markers
                value = stripped.split(":", 1)[-1].strip().strip("*")
                current_rec["action"] = value
            elif _field_match(lower, "reasoning"):
                current_rec["reasoning"] = _extract_value(stripped)
            elif _field_match(lower, "projected impact"):
                current_rec["projected_impact"] = _extract_value(stripped)
            elif _field_match(lower, "risk level"):
                current_rec["risk_level"] = _extract_value(stripped)

        # Don't forget the last one
        if current_rec.get("action"):
            recommendations.append(current_rec)

        return recommendations

    # ------------------------------------------------------------------
    # Graceful degradation helpers
    # ------------------------------------------------------------------

    async def _get_last_known_analysis(
        self, user_id: str, analysis_date: date
    ) -> BriefingResult | None:
        """Retrieve the most recent analysis from cache or DB as a fallback.

        Used when Phase 1 (data collection) fails and we need to return
        *something* rather than crashing. Checks the Redis cache for the
        current date first, then queries the database for the most recent
        analysis within the past 7 days.

        Args:
            user_id: Identifier for the advertiser / user.
            analysis_date: The date the failed analysis was targeting.

        Returns:
            A ``BriefingResult`` populated from cached/historical data,
            or ``None`` if no fallback is available.
        """
        # 1) Try Redis cache for today
        cache_key = f"sidera:briefing:{user_id}:{analysis_date.isoformat()}"
        try:
            cached = await cache_get(cache_key)
            if cached is not None:
                self._log.info(
                    "fallback.cache_hit",
                    user_id=user_id,
                    cache_key=cache_key,
                )
                return BriefingResult(
                    user_id=user_id,
                    briefing_text=cached.get("briefing_text", ""),
                    recommendations=cached.get("recommendations", []),
                    cost=cached.get("cost", {}),
                    session_id=cached.get("session_id", ""),
                )
        except Exception:
            self._log.debug("fallback.cache_check_failed")

        # 2) Try DB for most recent analysis in last 7 days
        try:
            from src.db import service as db_service
            from src.db.session import get_db_session

            async with get_db_session() as session:
                recent = await db_service.get_analyses_for_period(
                    session,
                    user_id,
                    analysis_date - timedelta(days=7),
                    analysis_date,
                )
                if recent:
                    latest = recent[-1]
                    self._log.info(
                        "fallback.db_hit",
                        user_id=user_id,
                        analysis_id=latest.id,
                    )
                    return BriefingResult(
                        user_id=user_id,
                        briefing_text=latest.briefing_content or "",
                        recommendations=latest.recommendations or [],
                        cost={},
                        session_id="",
                    )
        except Exception:
            self._log.debug("fallback.db_check_failed")

        self._log.warning("fallback.no_data", user_id=user_id)
        return None

    # ------------------------------------------------------------------
    # Volatility scoring for tiered analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_volatility_score(collected_data: str) -> float:
        """Parse collected data for WoW metric deltas and return max absolute % change.

        Scans the collected data text for numeric patterns that indicate
        week-over-week changes (e.g. ``"+15.3%"``, ``"-8.2%"``,
        ``"increased 12%"``, ``"decreased 5%"``). Returns the maximum
        absolute percentage change found, or ``0.0`` if no patterns are
        detected.

        This is used to decide whether Phase 3 (Opus strategic analysis)
        should run. If all metrics changed by less than 10%, the Opus
        phase is skipped to save cost.

        Args:
            collected_data: The raw text output from Phase 1 data collection.

        Returns:
            Maximum absolute percentage change found in the data, or 0.0
            if no percentage patterns are detected.
        """
        # Match patterns like: +15.3%, -8.2%, 12%, 5.0%
        pct_pattern = r"[+-]?\d+\.?\d*\s*%"
        matches = re.findall(pct_pattern, collected_data)
        if not matches:
            return 0.0

        values: list[float] = []
        for m in matches:
            try:
                val = float(m.replace("%", "").strip())
                values.append(abs(val))
            except ValueError:
                continue

        return max(values) if values else 0.0
