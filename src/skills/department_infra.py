"""Department-scoped infrastructure resolution.

Resolves per-department Slack channels and credentials with
graceful fallback to global defaults.

Usage::

    channel = await resolve_slack_channel("marketing")
    # → "#marketing-sidera" (from dept YAML/DB) or settings.slack_channel_id

    creds = await resolve_department_credentials("marketing", "google_ads")
    # → dept-scoped credentials dict, or None (use global)
"""

from __future__ import annotations

import structlog

from src.config import settings

logger = structlog.get_logger(__name__)


async def resolve_slack_channel(
    department_id: str | None = None,
    *,
    fallback_to_global: bool = True,
) -> str:
    """Return the Slack channel ID for a department.

    Resolution order:
    1. Department-specific channel (from DB or YAML)
    2. Global default channel (``settings.slack_channel_id``)

    Parameters
    ----------
    department_id:
        Department to resolve the channel for.  If ``None``, skips
        department lookup and returns the global channel.
    fallback_to_global:
        If ``True`` (default), fall back to the global channel when no
        department-specific channel is configured.

    Returns
    -------
    str
        A Slack channel ID (e.g. ``"C0123456789"``), or empty string
        if no channel is configured at any level.
    """
    if department_id:
        try:
            from src.skills.db_loader import load_registry_with_db

            registry = await load_registry_with_db()
            dept = registry.get_department(department_id)
            if dept and dept.slack_channel_id:
                logger.debug(
                    "resolve_slack_channel.department",
                    department_id=department_id,
                    channel=dept.slack_channel_id,
                )
                return dept.slack_channel_id
        except Exception as exc:
            logger.warning(
                "resolve_slack_channel.lookup_failed",
                department_id=department_id,
                error=str(exc),
            )

    if fallback_to_global:
        return settings.slack_channel_id
    return ""


async def resolve_department_credentials(
    department_id: str | None,
    platform: str,
) -> dict[str, str] | None:
    """Return department-scoped credentials for a platform, or ``None``.

    When ``None`` is returned, the caller should use the global
    credentials from ``settings`` (the existing default behavior).

    Parameters
    ----------
    department_id:
        Department to resolve credentials for.
    platform:
        Platform name (e.g. ``"google_ads"``, ``"meta"``, ``"bigquery"``).

    Returns
    -------
    dict or None
        A credentials dict if department-scoped credentials are
        configured, or ``None`` to signal "use global defaults".
    """
    if not department_id:
        return None

    try:
        from src.skills.db_loader import load_registry_with_db

        registry = await load_registry_with_db()
        dept = registry.get_department(department_id)
        if not dept or dept.credentials_scope != "department":
            return None
    except Exception as exc:
        logger.warning(
            "resolve_credentials.lookup_failed",
            department_id=department_id,
            platform=platform,
            error=str(exc),
        )
        return None

    # Department-scoped credentials are stored as env vars with a
    # department prefix convention:
    #   MARKETING_GOOGLE_ADS_REFRESH_TOKEN
    #   MARKETING_META_ACCESS_TOKEN
    #   etc.
    import os

    prefix = department_id.upper()
    platform_upper = platform.upper()

    creds: dict[str, str] = {}

    if platform == "google_ads":
        keys = [
            "developer_token",
            "client_id",
            "client_secret",
            "refresh_token",
            "login_customer_id",
        ]
        for key in keys:
            env_key = f"{prefix}_{platform_upper}_{key.upper()}"
            val = os.environ.get(env_key, "")
            if val:
                creds[key] = val

    elif platform == "meta":
        keys = ["app_id", "app_secret", "access_token"]
        for key in keys:
            env_key = f"{prefix}_{platform_upper}_{key.upper()}"
            val = os.environ.get(env_key, "")
            if val:
                creds[key] = val

    elif platform == "bigquery":
        keys = ["project_id", "dataset_id", "credentials_json"]
        for key in keys:
            env_key = f"{prefix}_{platform_upper}_{key.upper()}"
            val = os.environ.get(env_key, "")
            if val:
                creds[key] = val

    if not creds:
        logger.debug(
            "resolve_credentials.no_dept_creds",
            department_id=department_id,
            platform=platform,
        )
        return None

    logger.info(
        "resolve_credentials.department_scoped",
        department_id=department_id,
        platform=platform,
        keys_found=list(creds.keys()),
    )
    return creds


def resolve_department_for_role(role_id: str) -> str | None:
    """Look up which department a role belongs to (sync, best-effort).

    Returns the department_id or ``None`` if not found.  Uses the
    already-loaded registry if available, otherwise returns ``None``
    rather than blocking on async I/O.
    """
    try:
        from src.skills.registry import SkillRegistry

        # Try the default registry from disk (fast, no DB call)
        reg = SkillRegistry()
        reg.load_all()
        role = reg.get_role(role_id)
        if role and role.department_id:
            return role.department_id
    except Exception:
        pass
    return None
