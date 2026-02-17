"""Inngest client singleton for Sidera workflows."""

import inngest
from src.config import settings

inngest_client = inngest.Inngest(
    app_id="sidera",
    event_key=settings.inngest_event_key or "local-dev-key",
    signing_key=settings.inngest_signing_key or None,
    is_production=settings.is_production,
)
