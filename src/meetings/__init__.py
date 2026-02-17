"""Listen-only meeting support for Sidera.

Enables department heads (manager roles) to join video calls as
listen-only participants via Recall.ai bot. The MeetingSessionManager
orchestrates transcript capture via webhooks. Post-call: transcript
summary → action item extraction → manager delegation.
"""
