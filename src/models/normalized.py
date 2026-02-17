"""Cross-platform metric normalization.

Maps Google Ads, Meta, and Bing metrics to a unified schema so the agent
can reason about performance across platforms without caring which API
the data came from.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class NormalizedMetrics:
    """Platform-agnostic campaign metrics for a single day."""

    campaign_id: int  # Our internal campaign ID
    date: date
    impressions: int
    clicks: int
    cost: Decimal
    conversions: float
    conversion_value: Decimal
    # Computed
    ctr: float | None = None
    cpc: Decimal | None = None
    cpa: Decimal | None = None
    roas: float | None = None
    # Raw platform data preserved for deep dives
    platform_metrics: dict[str, Any] | None = None

    def __post_init__(self):
        """Compute derived metrics."""
        if self.impressions > 0:
            self.ctr = self.clicks / self.impressions
        if self.clicks > 0:
            self.cpc = self.cost / self.clicks
        if self.conversions > 0:
            self.cpa = self.cost / Decimal(str(self.conversions))
        if self.cost > 0:
            self.roas = float(self.conversion_value / self.cost)


# =============================================================================
# Google Ads → Normalized
# =============================================================================

# Google Ads API metric names → our names
GOOGLE_ADS_METRIC_MAP = {
    "metrics.impressions": "impressions",
    "metrics.clicks": "clicks",
    "metrics.cost_micros": "cost",  # Google returns micros (÷ 1,000,000)
    "metrics.conversions": "conversions",
    "metrics.conversions_value": "conversion_value",
    # Platform-specific (kept in platform_metrics)
    "metrics.search_impression_share": "search_impression_share",
    "metrics.search_top_impression_percentage": "search_top_is",
    "metrics.search_absolute_top_impression_percentage": "search_abs_top_is",
    "metrics.average_cpc": "avg_cpc_micros",
    "metrics.cost_per_conversion": "cost_per_conversion_micros",
    "metrics.interaction_rate": "interaction_rate",
    "metrics.all_conversions": "all_conversions",
    "metrics.view_through_conversions": "view_through_conversions",
}

# Google Ads campaign type mapping
GOOGLE_ADS_CAMPAIGN_TYPE_MAP = {
    "SEARCH": "search",
    "DISPLAY": "display",
    "SHOPPING": "shopping",
    "VIDEO": "video",
    "PERFORMANCE_MAX": "pmax",
    "DEMAND_GEN": "demand_gen",
    "APP": "app",
}


def normalize_google_ads_metrics(
    campaign_id: int,
    metric_date: date,
    raw: dict[str, Any],
) -> NormalizedMetrics:
    """Convert Google Ads API response metrics to normalized format.

    Google Ads returns monetary values in micros (1/1,000,000 of the currency unit).
    """
    cost_micros = raw.get("metrics.cost_micros", 0) or 0
    conv_value = raw.get("metrics.conversions_value", 0) or 0

    # Preserve platform-specific metrics
    platform_specific = {}
    for google_key, our_key in GOOGLE_ADS_METRIC_MAP.items():
        if our_key not in ("impressions", "clicks", "cost", "conversions", "conversion_value"):
            val = raw.get(google_key)
            if val is not None:
                platform_specific[our_key] = val

    return NormalizedMetrics(
        campaign_id=campaign_id,
        date=metric_date,
        impressions=int(raw.get("metrics.impressions", 0) or 0),
        clicks=int(raw.get("metrics.clicks", 0) or 0),
        cost=Decimal(str(cost_micros)) / Decimal("1000000"),
        conversions=float(raw.get("metrics.conversions", 0) or 0),
        conversion_value=Decimal(str(conv_value)),
        platform_metrics=platform_specific,
    )


# =============================================================================
# Meta (Facebook) → Normalized
# =============================================================================

# Meta Insights API field names → our names
META_METRIC_MAP = {
    "impressions": "impressions",
    "clicks": "clicks",  # Meta: total clicks (includes link clicks + other)
    "spend": "cost",  # Meta returns spend as a string decimal
    "actions": "conversions",  # Filtered by action_type == "purchase" or custom
    "action_values": "conversion_value",  # Filtered similarly
    # Platform-specific
    "inline_link_clicks": "link_clicks",
    "outbound_clicks": "outbound_clicks",
    "cpm": "cpm",
    "cpp": "cpp",
    "frequency": "frequency",
    "reach": "reach",
    "social_spend": "social_spend",
}

# Meta campaign objective mapping
META_CAMPAIGN_OBJECTIVE_MAP = {
    "OUTCOME_AWARENESS": "awareness",
    "OUTCOME_ENGAGEMENT": "engagement",
    "OUTCOME_TRAFFIC": "traffic",
    "OUTCOME_LEADS": "leads",
    "OUTCOME_APP_PROMOTION": "app",
    "OUTCOME_SALES": "sales",
}


def _extract_meta_conversions(
    actions: list[dict] | None,
    action_type: str = "purchase",
) -> float:
    """Extract conversion count from Meta's actions array.

    Meta returns conversions as a list of {action_type, value} dicts.
    We filter by the specified action_type (default: purchase).
    """
    if not actions:
        return 0.0
    total = 0.0
    for action in actions:
        if action.get("action_type") == action_type:
            total += float(action.get("value", 0))
    return total


def _extract_meta_conversion_value(
    action_values: list[dict] | None,
    action_type: str = "purchase",
) -> Decimal:
    """Extract conversion value from Meta's action_values array."""
    if not action_values:
        return Decimal("0")
    total = Decimal("0")
    for av in action_values:
        if av.get("action_type") == action_type:
            total += Decimal(str(av.get("value", "0")))
    return total


def normalize_meta_metrics(
    campaign_id: int,
    metric_date: date,
    raw: dict[str, Any],
    conversion_action_type: str = "purchase",
) -> NormalizedMetrics:
    """Convert Meta Marketing API response to normalized format.

    Meta returns spend as a string decimal and conversions as nested action arrays.
    """
    platform_specific = {}
    for meta_key, our_key in META_METRIC_MAP.items():
        if our_key not in ("impressions", "clicks", "cost", "conversions", "conversion_value"):
            val = raw.get(meta_key)
            if val is not None:
                platform_specific[our_key] = val

    return NormalizedMetrics(
        campaign_id=campaign_id,
        date=metric_date,
        impressions=int(raw.get("impressions", 0) or 0),
        clicks=int(raw.get("clicks", 0) or 0),
        cost=Decimal(str(raw.get("spend", "0") or "0")),
        conversions=_extract_meta_conversions(raw.get("actions"), conversion_action_type),
        conversion_value=_extract_meta_conversion_value(
            raw.get("action_values"), conversion_action_type
        ),
        platform_metrics=platform_specific,
    )


# =============================================================================
# Bing (Microsoft Advertising) → Normalized (stub for month 2)
# =============================================================================


def normalize_bing_metrics(
    campaign_id: int,
    metric_date: date,
    raw: dict[str, Any],
) -> NormalizedMetrics:
    """Convert Bing Ads API response to normalized format.

    Placeholder — will implement when Bing connector is built (month 2).
    """
    raise NotImplementedError("Bing normalization not yet implemented")
