"""Tests for the _execute_action helper and execution step in workflows.

Covers:
- Routing to GoogleAdsConnector for all 7 Google Ads action types
- Routing to MetaConnector for all 7 Meta action types
- Alias action types (update_budget, update_bid_target)
- ValueError for unknown platform or action type
- Connector error propagation

All connector classes are patched at the module-level import path
(inside ``src.workflows.daily_briefing``) since _execute_action uses
deferred imports.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.workflows.daily_briefing import _execute_action

# =====================================================================
# Google Ads — budget_change
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_budget_change():
    """Routes budget_change to GoogleAdsConnector.update_campaign_budget."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_budget.return_value = {"success": True}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "budget_change",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "new_budget_micros": "5000000",
            },
        )

    assert result == {"success": True}
    mock_connector.update_campaign_budget.assert_called_once_with(
        "123",
        "456",
        5000000,
        validate_cap=False,
    )


# =====================================================================
# Google Ads — pause_campaign
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_pause_campaign():
    """Routes pause_campaign to GoogleAdsConnector.update_campaign_status with PAUSED."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_status.return_value = {"status": "PAUSED"}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "pause_campaign",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
            },
        )

    assert result == {"status": "PAUSED"}
    mock_connector.update_campaign_status.assert_called_once_with(
        "123",
        "456",
        "PAUSED",
    )


# =====================================================================
# Google Ads — enable_campaign
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_enable_campaign():
    """Routes enable_campaign to GoogleAdsConnector.update_campaign_status with ENABLED."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_status.return_value = {"status": "ENABLED"}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "enable_campaign",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
            },
        )

    assert result == {"status": "ENABLED"}
    mock_connector.update_campaign_status.assert_called_once_with(
        "123",
        "456",
        "ENABLED",
    )


# =====================================================================
# Google Ads — bid_change
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_bid_change():
    """Routes bid_change to GoogleAdsConnector.update_bid_strategy_target."""
    mock_connector = MagicMock()
    mock_connector.update_bid_strategy_target.return_value = {"updated": True}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "bid_change",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "target_cpa_micros": 2500000,
                "target_roas": None,
            },
        )

    assert result == {"updated": True}
    mock_connector.update_bid_strategy_target.assert_called_once_with(
        "123",
        "456",
        target_cpa_micros=2500000,
        target_roas=None,
    )


# =====================================================================
# Google Ads — add_negative_keywords
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_add_negative_keywords():
    """Routes add_negative_keywords to GoogleAdsConnector.add_negative_keywords."""
    mock_connector = MagicMock()
    mock_connector.add_negative_keywords.return_value = {"added": 3}

    keywords = ["free", "cheap", "discount"]
    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "add_negative_keywords",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "keywords": keywords,
            },
        )

    assert result == {"added": 3}
    mock_connector.add_negative_keywords.assert_called_once_with(
        "123",
        "456",
        keywords,
    )


# =====================================================================
# Google Ads — update_ad_schedule
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_update_ad_schedule():
    """Routes update_ad_schedule to GoogleAdsConnector.update_ad_schedule."""
    mock_connector = MagicMock()
    mock_connector.update_ad_schedule.return_value = {"schedule_set": True}

    schedule = [{"day": "MONDAY", "start_hour": 8, "end_hour": 20}]
    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_ad_schedule",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "schedule": schedule,
            },
        )

    assert result == {"schedule_set": True}
    mock_connector.update_ad_schedule.assert_called_once_with(
        "123",
        "456",
        schedule,
    )


# =====================================================================
# Google Ads — update_geo_bid_modifier
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_update_geo_bid_modifier():
    """Routes update_geo_bid_modifier to GoogleAdsConnector.update_geo_bid_modifier."""
    mock_connector = MagicMock()
    mock_connector.update_geo_bid_modifier.return_value = {"modifier_set": True}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_geo_bid_modifier",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "geo_target_id": "2840",
                "bid_modifier": "1.25",
            },
        )

    assert result == {"modifier_set": True}
    mock_connector.update_geo_bid_modifier.assert_called_once_with(
        "123",
        "456",
        2840,
        1.25,
    )


# =====================================================================
# Meta — budget_change
# =====================================================================


@pytest.mark.asyncio
async def test_meta_budget_change():
    """Routes budget_change to MetaConnector.update_campaign_budget."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_budget.return_value = {"success": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "budget_change",
            {
                "platform": "meta",
                "account_id": "act_111",
                "campaign_id": "camp_222",
                "new_budget_cents": "5000",
                "budget_type": "daily",
            },
        )

    assert result == {"success": True}
    mock_connector.update_campaign_budget.assert_called_once_with(
        "act_111",
        "camp_222",
        5000,
        "daily",
        validate_cap=False,
    )


# =====================================================================
# Meta — pause_campaign
# =====================================================================


@pytest.mark.asyncio
async def test_meta_pause_campaign():
    """Routes pause_campaign to MetaConnector.update_campaign_status with PAUSED."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_status.return_value = {"status": "PAUSED"}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "pause_campaign",
            {
                "platform": "meta",
                "account_id": "act_111",
                "campaign_id": "camp_222",
            },
        )

    assert result == {"status": "PAUSED"}
    mock_connector.update_campaign_status.assert_called_once_with(
        "act_111",
        "camp_222",
        "PAUSED",
    )


# =====================================================================
# Meta — enable_campaign
# =====================================================================


@pytest.mark.asyncio
async def test_meta_enable_campaign():
    """Routes enable_campaign to MetaConnector.update_campaign_status with ACTIVE."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_status.return_value = {"status": "ACTIVE"}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "enable_campaign",
            {
                "platform": "meta",
                "account_id": "act_111",
                "campaign_id": "camp_222",
            },
        )

    assert result == {"status": "ACTIVE"}
    mock_connector.update_campaign_status.assert_called_once_with(
        "act_111",
        "camp_222",
        "ACTIVE",
    )


# =====================================================================
# Meta — pause_ad_set
# =====================================================================


@pytest.mark.asyncio
async def test_meta_pause_ad_set():
    """Routes pause_ad_set to MetaConnector.update_adset_status."""
    mock_connector = MagicMock()
    mock_connector.update_adset_status.return_value = {"adset_paused": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "pause_ad_set",
            {
                "platform": "meta",
                "account_id": "act_111",
                "adset_id": "adset_333",
            },
        )

    assert result == {"adset_paused": True}
    mock_connector.update_adset_status.assert_called_once_with(
        "act_111",
        "adset_333",
        "PAUSED",
    )


# =====================================================================
# Meta — update_ad_status
# =====================================================================


@pytest.mark.asyncio
async def test_meta_update_ad_status():
    """Routes update_ad_status to MetaConnector.update_ad_status."""
    mock_connector = MagicMock()
    mock_connector.update_ad_status.return_value = {"ad_updated": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_ad_status",
            {
                "platform": "meta",
                "account_id": "act_111",
                "ad_id": "ad_444",
                "status": "ACTIVE",
            },
        )

    assert result == {"ad_updated": True}
    mock_connector.update_ad_status.assert_called_once_with(
        "act_111",
        "ad_444",
        "ACTIVE",
    )


# =====================================================================
# Meta — update_adset_budget
# =====================================================================


@pytest.mark.asyncio
async def test_meta_update_adset_budget():
    """Routes update_adset_budget to MetaConnector.update_adset_budget."""
    mock_connector = MagicMock()
    mock_connector.update_adset_budget.return_value = {"budget_updated": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_adset_budget",
            {
                "platform": "meta",
                "account_id": "act_111",
                "adset_id": "adset_333",
                "new_budget_cents": "7500",
                "budget_type": "lifetime",
            },
        )

    assert result == {"budget_updated": True}
    mock_connector.update_adset_budget.assert_called_once_with(
        "act_111",
        "adset_333",
        7500,
        "lifetime",
        validate_cap=False,
    )


# =====================================================================
# Meta — update_adset_bid
# =====================================================================


@pytest.mark.asyncio
async def test_meta_update_adset_bid():
    """Routes update_adset_bid to MetaConnector.update_adset_bid."""
    mock_connector = MagicMock()
    mock_connector.update_adset_bid.return_value = {"bid_updated": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_adset_bid",
            {
                "platform": "meta",
                "account_id": "act_111",
                "adset_id": "adset_333",
                "bid_amount_cents": "350",
            },
        )

    assert result == {"bid_updated": True}
    mock_connector.update_adset_bid.assert_called_once_with(
        "act_111",
        "adset_333",
        350,
    )


# =====================================================================
# Unknown platform raises ValueError
# =====================================================================


@pytest.mark.asyncio
async def test_unknown_platform_raises_value_error():
    """Raises ValueError when platform is not google_ads or meta."""
    with pytest.raises(ValueError, match="Unknown platform: tiktok"):
        await _execute_action(
            "budget_change",
            {"platform": "tiktok", "campaign_id": "999"},
        )


# =====================================================================
# Unknown Google Ads action raises ValueError
# =====================================================================


@pytest.mark.asyncio
async def test_unknown_google_ads_action_raises_value_error():
    """Raises ValueError for an unsupported Google Ads action type."""
    mock_connector = MagicMock()

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        with pytest.raises(ValueError, match="Unsupported Google Ads action: delete_campaign"):
            await _execute_action(
                "delete_campaign",
                {
                    "platform": "google_ads",
                    "customer_id": "123",
                    "campaign_id": "456",
                },
            )


# =====================================================================
# Unknown Meta action raises ValueError
# =====================================================================


@pytest.mark.asyncio
async def test_unknown_meta_action_raises_value_error():
    """Raises ValueError for an unsupported Meta action type."""
    mock_connector = MagicMock()

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        with pytest.raises(ValueError, match="Unsupported Meta action: delete_ad"):
            await _execute_action(
                "delete_ad",
                {
                    "platform": "meta",
                    "account_id": "act_111",
                },
            )


# =====================================================================
# Connector error propagates
# =====================================================================


@pytest.mark.asyncio
async def test_connector_error_propagates():
    """When the connector raises an exception, it propagates to the caller."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_budget.side_effect = RuntimeError(
        "Google Ads API rate limit exceeded"
    )

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            await _execute_action(
                "budget_change",
                {
                    "platform": "google_ads",
                    "customer_id": "123",
                    "campaign_id": "456",
                    "new_budget_micros": "5000000",
                },
            )


# =====================================================================
# update_budget alias (same as budget_change) — Google Ads
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_update_budget_alias():
    """update_budget alias routes to the same path as budget_change for Google Ads."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_budget.return_value = {"alias_ok": True}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_budget",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "new_budget_micros": "3000000",
            },
        )

    assert result == {"alias_ok": True}
    mock_connector.update_campaign_budget.assert_called_once_with(
        "123",
        "456",
        3000000,
        validate_cap=False,
    )


# =====================================================================
# update_bid_target alias (same as bid_change) — Google Ads
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_update_bid_target_alias():
    """update_bid_target alias routes to the same path as bid_change for Google Ads."""
    mock_connector = MagicMock()
    mock_connector.update_bid_strategy_target.return_value = {"alias_ok": True}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_bid_target",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "target_roas": 3.5,
            },
        )

    assert result == {"alias_ok": True}
    mock_connector.update_bid_strategy_target.assert_called_once_with(
        "123",
        "456",
        target_cpa_micros=None,
        target_roas=3.5,
    )


# =====================================================================
# update_budget alias — Meta
# =====================================================================


@pytest.mark.asyncio
async def test_meta_update_budget_alias():
    """update_budget alias routes to the same path as budget_change for Meta."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_budget.return_value = {"meta_alias": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "update_budget",
            {
                "platform": "meta",
                "account_id": "act_111",
                "campaign_id": "camp_222",
                "new_budget_cents": "10000",
            },
        )

    assert result == {"meta_alias": True}
    mock_connector.update_campaign_budget.assert_called_once_with(
        "act_111",
        "camp_222",
        10000,
        "daily",
        validate_cap=False,
    )


# =====================================================================
# Meta — budget_type defaults to "daily"
# =====================================================================


@pytest.mark.asyncio
async def test_meta_budget_change_defaults_to_daily():
    """When budget_type is absent, defaults to 'daily' for Meta."""
    mock_connector = MagicMock()
    mock_connector.update_campaign_budget.return_value = {"ok": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        await _execute_action(
            "budget_change",
            {
                "platform": "meta",
                "account_id": "act_111",
                "campaign_id": "camp_222",
                "new_budget_cents": "5000",
                # No budget_type key
            },
        )

    mock_connector.update_campaign_budget.assert_called_once_with(
        "act_111",
        "camp_222",
        5000,
        "daily",
        validate_cap=False,
    )


# =====================================================================
# Meta — update_ad_status defaults to PAUSED
# =====================================================================


@pytest.mark.asyncio
async def test_meta_update_ad_status_defaults_to_paused():
    """When status is absent from action_params, defaults to PAUSED."""
    mock_connector = MagicMock()
    mock_connector.update_ad_status.return_value = {"ok": True}

    with patch(
        "src.connectors.meta.MetaConnector",
        return_value=mock_connector,
    ):
        await _execute_action(
            "update_ad_status",
            {
                "platform": "meta",
                "account_id": "act_111",
                "ad_id": "ad_444",
                # No status key
            },
        )

    mock_connector.update_ad_status.assert_called_once_with(
        "act_111",
        "ad_444",
        "PAUSED",
    )


# =====================================================================
# Missing platform key raises ValueError
# =====================================================================


@pytest.mark.asyncio
async def test_missing_platform_raises_value_error():
    """Raises ValueError when platform key is absent from action_params."""
    with pytest.raises(ValueError, match="Unknown platform: "):
        await _execute_action(
            "budget_change",
            {"campaign_id": "456"},  # No platform key
        )


# =====================================================================
# Google Ads — bid_change with target_roas only
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_bid_change_target_roas_only():
    """bid_change with target_roas but no target_cpa_micros passes None for CPA."""
    mock_connector = MagicMock()
    mock_connector.update_bid_strategy_target.return_value = {"roas_set": True}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        result = await _execute_action(
            "bid_change",
            {
                "platform": "google_ads",
                "customer_id": "123",
                "campaign_id": "456",
                "target_roas": 4.0,
                # No target_cpa_micros key
            },
        )

    assert result == {"roas_set": True}
    mock_connector.update_bid_strategy_target.assert_called_once_with(
        "123",
        "456",
        target_cpa_micros=None,
        target_roas=4.0,
    )


# =====================================================================
# Google Ads — geo_target_id and bid_modifier are cast to int/float
# =====================================================================


@pytest.mark.asyncio
async def test_google_ads_geo_bid_modifier_type_casting():
    """geo_target_id is cast to int and bid_modifier to float from strings."""
    mock_connector = MagicMock()
    mock_connector.update_geo_bid_modifier.return_value = {"cast_ok": True}

    with patch(
        "src.connectors.google_ads.GoogleAdsConnector",
        return_value=mock_connector,
    ):
        await _execute_action(
            "update_geo_bid_modifier",
            {
                "platform": "google_ads",
                "customer_id": "C1",
                "campaign_id": "P1",
                "geo_target_id": "1023191",
                "bid_modifier": "0.85",
            },
        )

    call_args = mock_connector.update_geo_bid_modifier.call_args
    # Fourth positional arg is geo_target_id (int)
    assert isinstance(call_args[0][2], int)
    assert call_args[0][2] == 1023191
    # Fifth positional arg is bid_modifier (float)
    assert isinstance(call_args[0][3], float)
    assert call_args[0][3] == 0.85
