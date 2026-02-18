"""Tests for GAQL input validation in Google Ads connector.

Covers:
- _validate_gaql_id accepts valid numeric IDs
- _validate_gaql_id rejects SQL injection attempts
- _validate_gaql_id rejects special characters
- _validate_gaql_id handles int and str inputs
"""

from __future__ import annotations

import pytest

from src.connectors.google_ads import _validate_gaql_id


class TestValidateGaqlId:
    """Tests for the _validate_gaql_id helper."""

    def test_accepts_numeric_string(self) -> None:
        assert _validate_gaql_id("1234567890", "campaign_id") == "1234567890"

    def test_accepts_integer(self) -> None:
        assert _validate_gaql_id(1014044, "geo_target_id") == "1014044"

    def test_accepts_zero(self) -> None:
        assert _validate_gaql_id("0", "id") == "0"

    def test_rejects_sql_injection(self) -> None:
        with pytest.raises(ValueError, match="expected digits only"):
            _validate_gaql_id("123' OR '1'='1", "campaign_id")

    def test_rejects_semicolon(self) -> None:
        with pytest.raises(ValueError, match="expected digits only"):
            _validate_gaql_id("123; DROP TABLE", "campaign_id")

    def test_rejects_alphabetic(self) -> None:
        with pytest.raises(ValueError, match="expected digits only"):
            _validate_gaql_id("abc123", "campaign_id")

    def test_rejects_dashes(self) -> None:
        """Google Ads IDs don't use dashes — those are display-only."""
        with pytest.raises(ValueError, match="expected digits only"):
            _validate_gaql_id("123-456-7890", "customer_id")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="expected digits only"):
            _validate_gaql_id("", "campaign_id")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="expected digits only"):
            _validate_gaql_id("  ", "campaign_id")

    def test_strips_whitespace_around_digits(self) -> None:
        """Leading/trailing whitespace around valid ID should be stripped."""
        assert _validate_gaql_id(" 123456 ", "id") == "123456"

    def test_rejects_newline_injection(self) -> None:
        with pytest.raises(ValueError, match="expected digits only"):
            _validate_gaql_id("123\nDROP TABLE", "campaign_id")
