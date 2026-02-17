"""Shared helper functions for MCP server tools.

These utilities are used across all MCP server modules to build responses
and format values for display.  Extracted here to avoid duplication.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# MCP response builders
# ---------------------------------------------------------------------------


def text_response(text: str) -> dict[str, Any]:
    """Build a standard MCP text response."""
    return {"content": [{"type": "text", "text": text}]}


def error_response(message: str) -> dict[str, Any]:
    """Build an MCP error response."""
    return {"content": [{"type": "text", "text": f"ERROR: {message}"}], "is_error": True}


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------


def format_currency(value: Decimal | float | int | None) -> str:
    """Format a monetary value for display."""
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def format_number(value: int | float | None) -> str:
    """Format a numeric value with commas."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        if value == int(value):
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return f"{value:,}"


def format_percentage(value: float | None) -> str:
    """Format a float as a percentage string."""
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"
