"""Webhook event pipeline for always-on monitoring.

External systems push events to Sidera via webhook endpoints.
Events are normalized, classified by severity, deduplicated,
and dispatched as Inngest events for the reactor workflow.
"""
