"""Input boundary markers for prompt injection defense.

Wraps untrusted external input (Slack messages, webhook payloads, document
content, meeting transcripts) in XML-like boundary tags. The agent's system
prompt instructs the LLM to treat content inside these tags as DATA only —
never as instructions to follow.

This is the primary defense against indirect prompt injection attacks where
an attacker embeds malicious instructions inside user messages, webhook
payloads, or uploaded documents that the LLM might otherwise follow.
"""

from __future__ import annotations

UNTRUSTED_OPEN = "<untrusted_input>"
UNTRUSTED_CLOSE = "</untrusted_input>"


def wrap_untrusted(text: str) -> str:
    """Wrap text in boundary tags marking it as untrusted external input.

    The agent's system prompt (via ``INJECTION_DEFENSE_SUPPLEMENT``)
    instructs the LLM to never follow commands or generate recommendations
    based on content inside these tags.

    Args:
        text: Raw external input (user message, webhook payload, etc.).

    Returns:
        The text wrapped in ``<untrusted_input>`` boundary tags.
    """
    if not text:
        return text
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"
