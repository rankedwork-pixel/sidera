"""Google BigQuery connector for Sidera.

Read-only connector that pulls backend business data from BigQuery -- goals,
budget pacing, business metrics, channel performance, and campaign attribution.
This data is the advertiser's source of truth, independent of what Google Ads
or Meta report. The agent uses it to ground analysis in real business outcomes
rather than platform-reported metrics.

Architecture:
    connector (this file) -> MCP tools -> agent loop
    Each method builds a parameterized SQL query, executes it via the
    BigQuery client, converts result rows to plain dicts, and returns
    clean Python data structures.

Usage:
    from src.connectors.bigquery import BigQueryConnector

    connector = BigQueryConnector()  # uses settings singleton
    goals = connector.get_goals(period="2024-01", channel="google_ads")
    metrics = connector.get_business_metrics("2024-01-01", "2024-01-31")
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date
from typing import Any

import structlog
from google.api_core.exceptions import (
    BadRequest,
    Forbidden,
    GoogleAPICallError,
    NotFound,
)
from google.cloud import bigquery
from google.oauth2.service_account import Credentials

from src.cache.decorators import cached
from src.cache.service import (
    CACHE_TTL_BQ_GOALS,
    CACHE_TTL_BQ_METRICS,
    CACHE_TTL_BQ_PACING,
)
from src.config import settings
from src.connectors.retry import retry_with_backoff
from src.middleware.sentry_setup import capture_exception

logger = structlog.get_logger(__name__)

# Maximum rows allowed for custom queries
_MAX_CUSTOM_QUERY_ROWS = 5000

# Default row limit for standard queries
_DEFAULT_ROW_LIMIT = 10_000


class BigQueryConnectorError(Exception):
    """Base exception for BigQuery connector errors."""

    pass


class BigQueryAuthError(BigQueryConnectorError):
    """Authentication or authorization failure -- must be surfaced to user."""

    pass


class BigQueryTableNotFoundError(BigQueryConnectorError):
    """A required table or view is not configured or does not exist."""

    pass


class BigQueryConnector:
    """Read-only client for Google BigQuery.

    Wraps the google-cloud-bigquery SDK and exposes clean, dict-based
    methods for pulling goals, budget pacing, business metrics, channel
    performance, and campaign attribution from the advertiser's backend
    data warehouse.

    Args:
        credentials: Optional dict of credentials. If omitted, values are
            read from the ``settings`` singleton (env vars / .env file).
            Expected keys: ``project_id``, ``dataset_id``,
            ``credentials_json`` (service account JSON string, base64-encoded
            JSON, or empty for Application Default Credentials).
    """

    def __init__(self, credentials: dict[str, str] | None = None) -> None:
        self._credentials = credentials or self._credentials_from_settings()
        self._client = self._build_client(self._credentials)
        self._table_config = self._load_table_config()
        self._log = logger.bind(connector="bigquery")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def discover_tables(self) -> list[dict[str, Any]]:
        """List tables and views in the configured dataset.

        Useful for the agent to understand what backend data is available
        before running queries. Not cached because it is only called during
        setup or diagnostics.

        Returns:
            List of dicts with ``table_id``, ``table_type``, ``num_rows``,
            ``num_bytes``, ``description``, or an empty list on failure.
        """
        dataset_ref = (
            f"{self._credentials.get('project_id', '')}.{self._credentials.get('dataset_id', '')}"
        )
        self._log.info("discovering_tables", dataset=dataset_ref)
        try:
            tables = self._client.list_tables(dataset_ref)
            results: list[dict[str, Any]] = []
            for table in tables:
                # Fetch full table metadata for row count / size
                full_table = self._client.get_table(table.reference)
                results.append(
                    {
                        "table_id": table.table_id,
                        "table_type": table.table_type,
                        "num_rows": full_table.num_rows,
                        "num_bytes": full_table.num_bytes,
                        "description": full_table.description or "",
                    }
                )

            self._log.info("tables_discovered", count=len(results))
            return results

        except GoogleAPICallError as exc:
            self._handle_bigquery_error(exc, "discover_tables")
            return []

    @cached(ttl_seconds=CACHE_TTL_BQ_GOALS, key_prefix="bigquery:get_goals")
    def get_goals(
        self,
        period: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch revenue / CPA / ROAS targets from the goals table.

        The goals table is expected to have columns such as ``period``
        (e.g. ``"2024-01"``), ``channel``, ``metric_name``, ``target_value``.

        Args:
            period: Optional period filter (e.g. ``"2024-01"``).
            channel: Optional channel filter (e.g. ``"google_ads"``).

        Returns:
            List of goal dicts, or empty list on failure.
        """
        table = self._resolve_table("goals")
        self._log.info("getting_goals", period=period, channel=channel)

        conditions = []
        params: list[bigquery.ScalarQueryParameter] = []

        if period:
            conditions.append("period = @period")
            params.append(bigquery.ScalarQueryParameter("period", "STRING", period))
        if channel:
            conditions.append("channel = @channel")
            params.append(bigquery.ScalarQueryParameter("channel", "STRING", channel))

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT *
            FROM `{table}`
            {where_clause}
            ORDER BY period DESC, channel
            LIMIT 500
        """

        rows = self._execute_query(query, params=params)
        if rows is None:
            return []

        self._log.info("goals_fetched", count=len(rows))
        return rows

    @cached(ttl_seconds=CACHE_TTL_BQ_PACING, key_prefix="bigquery:get_budget_pacing")
    def get_budget_pacing(
        self,
        period: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch planned vs actual spend pacing from the budget pacing table.

        The budget_pacing table is expected to have columns such as
        ``period``, ``channel``, ``planned_spend``, ``actual_spend``,
        ``pacing_pct``.

        Args:
            period: Optional period filter (e.g. ``"2024-01"``).
            channel: Optional channel filter (e.g. ``"google_ads"``).

        Returns:
            List of pacing dicts, or empty list on failure.
        """
        table = self._resolve_table("budget_pacing")
        self._log.info("getting_budget_pacing", period=period, channel=channel)

        conditions = []
        params: list[bigquery.ScalarQueryParameter] = []

        if period:
            conditions.append("period = @period")
            params.append(bigquery.ScalarQueryParameter("period", "STRING", period))
        if channel:
            conditions.append("channel = @channel")
            params.append(bigquery.ScalarQueryParameter("channel", "STRING", channel))

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT *
            FROM `{table}`
            {where_clause}
            ORDER BY period DESC, channel
            LIMIT 500
        """

        rows = self._execute_query(query, params=params)
        if rows is None:
            return []

        self._log.info("budget_pacing_fetched", count=len(rows))
        return rows

    @cached(ttl_seconds=CACHE_TTL_BQ_METRICS, key_prefix="bigquery:get_business_metrics")
    def get_business_metrics(
        self,
        start_date: date | str,
        end_date: date | str,
        granularity: str = "daily",
    ) -> list[dict[str, Any]]:
        """Fetch top-line business performance (revenue, orders, AOV).

        Queries the orders table and aggregates by the requested granularity.

        Args:
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).
            granularity: ``"daily"``, ``"weekly"``, or ``"monthly"``.

        Returns:
            List of metric dicts with ``date``, ``revenue``, ``orders``,
            ``aov`` (average order value), or empty list on failure.
        """
        table = self._resolve_table("orders")
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)
        self._log.info(
            "getting_business_metrics",
            start_date=start_str,
            end_date=end_str,
            granularity=granularity,
        )

        # Build date truncation expression based on granularity
        granularity_map = {
            "daily": "DATE(order_date)",
            "weekly": "DATE_TRUNC(DATE(order_date), WEEK)",
            "monthly": "DATE_TRUNC(DATE(order_date), MONTH)",
        }
        date_expr = granularity_map.get(granularity, "DATE(order_date)")

        params = [
            bigquery.ScalarQueryParameter("start_date", "STRING", start_str),
            bigquery.ScalarQueryParameter("end_date", "STRING", end_str),
        ]

        query = f"""
            SELECT
                {date_expr} AS date,
                SUM(revenue) AS revenue,
                COUNT(*) AS orders,
                SAFE_DIVIDE(SUM(revenue), COUNT(*)) AS aov
            FROM `{table}`
            WHERE DATE(order_date) BETWEEN DATE(@start_date) AND DATE(@end_date)
            GROUP BY date
            ORDER BY date DESC
            LIMIT 1000
        """

        rows = self._execute_query(query, params=params)
        if rows is None:
            return []

        self._log.info("business_metrics_fetched", count=len(rows))
        return rows

    @cached(ttl_seconds=CACHE_TTL_BQ_METRICS, key_prefix="bigquery:get_channel_performance")
    def get_channel_performance(
        self,
        start_date: date | str,
        end_date: date | str,
    ) -> list[dict[str, Any]]:
        """Fetch revenue and orders by marketing channel from backend attribution.

        This is the advertiser's own attribution data, which may differ from
        what Google Ads or Meta report. The agent uses this to cross-check
        platform-reported metrics.

        Args:
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            List of dicts with ``channel``, ``date``, ``revenue``,
            ``orders``, ``cost``, or empty list on failure.
        """
        table = self._resolve_table("channel_performance")
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)
        self._log.info(
            "getting_channel_performance",
            start_date=start_str,
            end_date=end_str,
        )

        params = [
            bigquery.ScalarQueryParameter("start_date", "STRING", start_str),
            bigquery.ScalarQueryParameter("end_date", "STRING", end_str),
        ]

        query = f"""
            SELECT
                channel,
                DATE(date) AS date,
                revenue,
                orders,
                cost
            FROM `{table}`
            WHERE DATE(date) BETWEEN DATE(@start_date) AND DATE(@end_date)
            ORDER BY date DESC, channel
            LIMIT 5000
        """

        rows = self._execute_query(query, params=params)
        if rows is None:
            return []

        self._log.info("channel_performance_fetched", count=len(rows))
        return rows

    @cached(ttl_seconds=CACHE_TTL_BQ_METRICS, key_prefix="bigquery:get_campaign_attribution")
    def get_campaign_attribution(
        self,
        start_date: date | str,
        end_date: date | str,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch backend-attributed conversions by campaign.

        This lets the agent compare what a platform claims a campaign
        generated against what the advertiser's backend actually recorded.

        Args:
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).
            channel: Optional channel filter (e.g. ``"google_ads"``).

        Returns:
            List of dicts with ``campaign_id``, ``campaign_name``,
            ``channel``, ``date``, ``conversions``, ``revenue``,
            or empty list on failure.
        """
        table = self._resolve_table("campaign_attribution")
        start_str = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
        end_str = end_date.isoformat() if isinstance(end_date, date) else str(end_date)
        self._log.info(
            "getting_campaign_attribution",
            start_date=start_str,
            end_date=end_str,
            channel=channel,
        )

        conditions = [
            "DATE(date) BETWEEN DATE(@start_date) AND DATE(@end_date)",
        ]
        params = [
            bigquery.ScalarQueryParameter("start_date", "STRING", start_str),
            bigquery.ScalarQueryParameter("end_date", "STRING", end_str),
        ]

        if channel:
            conditions.append("channel = @channel")
            params.append(bigquery.ScalarQueryParameter("channel", "STRING", channel))

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT
                campaign_id,
                campaign_name,
                channel,
                DATE(date) AS date,
                conversions,
                revenue
            FROM `{table}`
            WHERE {where_clause}
            ORDER BY date DESC, revenue DESC
            LIMIT 5000
        """

        rows = self._execute_query(query, params=params)
        if rows is None:
            return []

        self._log.info("campaign_attribution_fetched", count=len(rows))
        return rows

    def run_custom_query(
        self,
        sql: str,
        params: list[bigquery.ScalarQueryParameter] | None = None,
        max_rows: int = 1000,
    ) -> list[dict[str, Any]]:
        """Execute a read-only SQL query against BigQuery.

        Only SELECT statements are allowed. The row limit is capped at
        ``_MAX_CUSTOM_QUERY_ROWS`` (5000) regardless of what the caller
        requests. Not cached because custom queries are ad-hoc.

        Args:
            sql: A SQL SELECT statement.
            params: Optional list of BigQuery query parameters.
            max_rows: Maximum rows to return (capped at 5000).

        Returns:
            List of row dicts, or empty list on failure.

        Raises:
            BigQueryConnectorError: If the SQL is not a SELECT statement.
        """
        # Validate that the query is read-only
        stripped = sql.strip()
        if not re.match(r"(?i)^SELECT\b", stripped):
            raise BigQueryConnectorError(
                "Only SELECT queries are allowed. "
                "Got: " + stripped[:60] + ("..." if len(stripped) > 60 else "")
            )

        # Cap max_rows
        effective_max = min(max_rows, _MAX_CUSTOM_QUERY_ROWS)

        self._log.info(
            "running_custom_query",
            query_preview=stripped[:120],
            max_rows=effective_max,
        )

        # Append LIMIT if not already present
        if not re.search(r"(?i)\bLIMIT\b", stripped):
            sql = f"{stripped}\nLIMIT {effective_max}"

        rows = self._execute_query(sql, params=params)
        if rows is None:
            return []

        # Enforce row cap even if the query had its own LIMIT
        return rows[:effective_max]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _credentials_from_settings() -> dict[str, str]:
        """Build a credentials dict from the global settings singleton."""
        return {
            "project_id": settings.bigquery_project_id,
            "dataset_id": settings.bigquery_dataset_id,
            "credentials_json": settings.bigquery_credentials_json,
        }

    @staticmethod
    def _build_client(credentials: dict[str, str]) -> bigquery.Client:
        """Create a ``bigquery.Client`` from a credentials dict.

        Supports three authentication modes:

        1. **Service account JSON string** -- ``credentials_json`` is a raw
           JSON string containing service account credentials.
        2. **Base64-encoded JSON** -- ``credentials_json`` is a base64-encoded
           string (useful for Railway / container env vars where newlines in
           JSON are problematic).
        3. **Application Default Credentials** -- ``credentials_json`` is
           empty; falls back to ADC (local ``gcloud auth`` or GCE metadata).

        Args:
            credentials: Dict with ``project_id``, ``dataset_id``, and
                optionally ``credentials_json``.

        Returns:
            Configured ``bigquery.Client`` instance.

        Raises:
            BigQueryAuthError: If credentials are invalid or client creation
                fails.
        """
        project_id = credentials.get("project_id", "")
        credentials_json = credentials.get("credentials_json", "")

        try:
            if credentials_json:
                # Try to parse as raw JSON first
                sa_info = None
                try:
                    sa_info = json.loads(credentials_json)
                except json.JSONDecodeError:
                    # Might be base64-encoded
                    try:
                        decoded = base64.b64decode(credentials_json)
                        sa_info = json.loads(decoded)
                    except Exception as b64_exc:
                        raise BigQueryAuthError(
                            "credentials_json is neither valid JSON nor valid base64-encoded JSON."
                        ) from b64_exc

                sa_credentials = Credentials.from_service_account_info(sa_info)
                return bigquery.Client(
                    project=project_id or sa_info.get("project_id"),
                    credentials=sa_credentials,
                )
            else:
                # Fall back to Application Default Credentials
                return bigquery.Client(project=project_id or None)

        except BigQueryAuthError:
            raise
        except Exception as exc:
            raise BigQueryAuthError(f"Failed to create BigQuery client: {exc}") from exc

    def _load_table_config(self) -> dict[str, str]:
        """Map logical table names to fully-qualified BigQuery table paths.

        Reads table name overrides from settings and constructs paths in the
        format ``project_id.dataset_id.table_name``.

        Returns:
            Dict mapping logical names (e.g. ``"goals"``) to full BQ paths
            (e.g. ``"my-project.my_dataset.goals"``).
        """
        project = self._credentials.get("project_id", "")
        dataset = self._credentials.get("dataset_id", "")
        prefix = f"{project}.{dataset}" if project and dataset else ""

        table_map = {
            "goals": settings.bigquery_table_goals,
            "orders": settings.bigquery_table_orders,
            "channel_performance": settings.bigquery_table_channel_performance,
            "budget_pacing": settings.bigquery_table_budget_pacing,
            "campaign_attribution": settings.bigquery_table_campaign_attribution,
        }

        config: dict[str, str] = {}
        for logical_name, table_name in table_map.items():
            if table_name:
                # If the table name already contains dots, treat it as
                # fully qualified; otherwise prepend project.dataset
                if "." in table_name:
                    config[logical_name] = table_name
                elif prefix:
                    config[logical_name] = f"{prefix}.{table_name}"
                else:
                    config[logical_name] = table_name

        return config

    def _resolve_table(self, logical_name: str) -> str:
        """Look up a logical table name and return its full BigQuery path.

        Args:
            logical_name: The logical name (e.g. ``"goals"``).

        Returns:
            Fully-qualified BigQuery table path.

        Raises:
            BigQueryTableNotFoundError: If the logical name is not configured.
        """
        full_path = self._table_config.get(logical_name)
        if not full_path:
            raise BigQueryTableNotFoundError(
                f"Table '{logical_name}' is not configured. "
                f"Set bigquery_table_{logical_name} in settings or .env."
            )
        return full_path

    @retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0)
    def _execute_query(
        self,
        query: str,
        params: list[bigquery.ScalarQueryParameter] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Execute a parameterized SQL query and return rows as dicts.

        Args:
            query: A SQL query string (may contain ``@param`` placeholders).
            params: Optional list of ``bigquery.ScalarQueryParameter``.

        Returns:
            List of row dicts on success, or ``None`` on transient failure
            (auth errors are raised).
        """
        self._log.debug(
            "executing_bq_query",
            query=query.strip()[:120],
        )

        job_config = bigquery.QueryJobConfig()
        if params:
            job_config.query_parameters = params

        try:
            query_job = self._client.query(query, job_config=job_config)
            result = query_job.result()

            rows: list[dict[str, Any]] = []
            for row in result:
                rows.append(dict(row))

            self._log.debug("query_returned_rows", count=len(rows))
            return rows

        except GoogleAPICallError as exc:
            self._handle_bigquery_error(exc, "execute_query", query_preview=query.strip()[:120])
            # _handle_bigquery_error raises on auth errors, so if we
            # reach here the error was transient.
            return None

    def _check_table_exists(self, table_ref: str) -> bool:
        """Check whether a table or view exists in BigQuery.

        Args:
            table_ref: Fully-qualified table path
                (e.g. ``"project.dataset.table"``).

        Returns:
            ``True`` if the table exists, ``False`` otherwise.
        """
        try:
            self._client.get_table(table_ref)
            return True
        except NotFound:
            return False
        except GoogleAPICallError as exc:
            self._log.warning(
                "table_exists_check_failed",
                table_ref=table_ref,
                error=str(exc),
            )
            return False

    def _handle_bigquery_error(
        self,
        exc: GoogleAPICallError,
        operation: str,
        **context: Any,
    ) -> None:
        """Inspect a BigQuery API error and raise or log appropriately.

        Auth/permission failures are raised as ``BigQueryAuthError`` so
        callers can surface them to users. Not-found errors are raised as
        ``BigQueryTableNotFoundError``. Other errors are logged and
        swallowed (methods return empty results).

        Args:
            exc: The caught exception.
            operation: A label for the operation that failed.
            **context: Extra fields to include in the structured log.

        Raises:
            BigQueryAuthError: On permission / authentication failures.
            BigQueryTableNotFoundError: On 404 not-found errors.
        """
        capture_exception(exc)

        self._log.error(
            "bigquery_api_error",
            operation=operation,
            error_type=type(exc).__name__,
            error_message=str(exc),
            **context,
        )

        if isinstance(exc, Forbidden):
            raise BigQueryAuthError(
                f"BigQuery permission denied during {operation}: {exc}"
            ) from exc

        if isinstance(exc, NotFound):
            raise BigQueryTableNotFoundError(
                f"BigQuery resource not found during {operation}: {exc}"
            ) from exc

        if isinstance(exc, BadRequest):
            self._log.error(
                "bigquery_bad_request",
                operation=operation,
                error=str(exc),
                **context,
            )
            # Bad requests are not transient but also not auth errors;
            # let the caller handle the None return.
