"""Tests for observation masking in conversation prompts.

Covers:
- _mask_observation(): first sentence extraction, fallback truncation
- build_conversation_prompt(): old verbose bot messages masked, recent bot
  messages preserved, human messages never masked, short bot messages
  preserved, empty history, current message always included.

The observation masking system compresses verbose bot responses older than
_OBSERVATION_MASK_TURN_AGE=3 turns from the end that exceed
_OBSERVATION_MASK_MIN_LENGTH=500 chars, preventing context window waste
while preserving conversational flow and all human messages in full.
"""

from __future__ import annotations

from src.agent.prompts import (
    _mask_observation,
    build_conversation_prompt,
)

# =====================================================================
# _mask_observation
# =====================================================================


class TestMaskObservation:
    """Tests for _mask_observation()."""

    def test_mask_observation_extracts_first_sentence(self):
        """_mask_observation extracts the first sentence (ending with '. ')
        and wraps it in an [Earlier response summarized] marker."""
        text = (
            "Campaign CPA dropped by 15% this week. "
            "This was driven by improved targeting on the Search campaign. "
            "Additionally the creative refresh on Shopping boosted CTR."
        )
        result = _mask_observation(text)

        assert result.startswith("[Earlier response summarized]")
        assert "Campaign CPA dropped by 15% this week." in result
        # Should NOT include the second sentence
        assert "improved targeting" not in result

    def test_mask_observation_fallback_truncation(self):
        """When no sentence boundary is found within 200 chars, falls back
        to 150-char truncation with '...' appended."""
        # Build a long string with no period/exclamation/question within 200 chars
        text = "A" * 300
        result = _mask_observation(text)

        assert result.startswith("[Earlier response summarized]")
        # Should have exactly 150 A's followed by ...
        assert "A" * 150 + "..." in result
        # Should NOT have 200 A's
        assert "A" * 200 not in result

    def test_mask_observation_sentence_boundary_variants(self):
        """Sentence boundaries include '. ', '.\\n', '!\\n', and '? '."""
        # Period-newline boundary
        text_period_nl = "First sentence here.\nSecond sentence continues with more text."
        result = _mask_observation(text_period_nl)
        assert "First sentence here." in result
        assert "Second sentence" not in result

    def test_mask_observation_ignores_late_sentence_boundary(self):
        """A sentence boundary found after 200 chars triggers the fallback."""
        # No sentence boundary within first 200 chars
        text = "x" * 201 + ". more text after that boundary"
        result = _mask_observation(text)

        # Should use 150-char truncation fallback
        assert "x" * 150 + "..." in result

    def test_mask_observation_short_text_no_ellipsis(self):
        """Text <= 150 chars without a sentence boundary gets no ellipsis."""
        text = "Short text without sentence ending"
        result = _mask_observation(text)

        assert "[Earlier response summarized]" in result
        assert "Short text without sentence ending" in result
        assert "..." not in result


# =====================================================================
# build_conversation_prompt — observation masking integration
# =====================================================================


class TestBuildConversationPromptMasking:
    """Tests for observation masking inside build_conversation_prompt()."""

    def _make_bot_message(self, text: str, ts: str = "1.0") -> dict:
        return {
            "user": "BOT123",
            "text": text,
            "ts": ts,
            "bot_id": "B123",
            "is_bot": True,
        }

    def _make_user_message(self, text: str, ts: str = "2.0") -> dict:
        return {
            "user": "U_HUMAN",
            "text": text,
            "ts": ts,
            "is_bot": False,
        }

    def test_old_verbose_bot_messages_masked(self):
        """Bot messages older than 3 positions from end that exceed 500 chars
        get replaced with the masked summary."""
        verbose_text = "Here is a detailed analysis. " + "x" * 600
        # Build history: 6 messages total, early bot messages should be masked
        history = [
            self._make_bot_message(verbose_text, "1.0"),  # index 0 — old, >500 chars
            self._make_user_message("Thanks", "2.0"),  # index 1
            self._make_bot_message(verbose_text, "3.0"),  # index 2 — old, >500 chars
            self._make_user_message("What next?", "4.0"),  # index 3 — recent (cutoff=3)
            self._make_bot_message("Short recent.", "5.0"),  # index 4 — recent
            self._make_user_message("Ok", "6.0"),  # index 5 — recent
        ]

        prompt = build_conversation_prompt(
            history,
            "Latest question",
            bot_user_id="BOT123",
        )

        # Old verbose bot messages (index 0, 2) should be masked
        assert "[Earlier response summarized]" in prompt
        # The verbose content should NOT appear in full
        assert "x" * 600 not in prompt
        # But first sentence of the masking should be there
        assert "Here is a detailed analysis." in prompt

    def test_recent_bot_messages_not_masked(self):
        """Bot messages in the last 3 positions are NOT masked even if verbose."""
        verbose_text = "Recent detailed response. " + "y" * 600

        history = [
            self._make_user_message("First question", "1.0"),
            self._make_bot_message(verbose_text, "2.0"),  # position 1 of 3 — recent
            self._make_user_message("Follow up", "3.0"),  # position 2 of 3
        ]

        prompt = build_conversation_prompt(
            history,
            "New question",
            bot_user_id="BOT123",
        )

        # The verbose text should appear in full (not masked)
        assert "y" * 600 in prompt
        assert "[Earlier response summarized]" not in prompt

    def test_human_messages_never_masked(self):
        """Human messages are always kept in full regardless of age or length."""
        long_human_text = "I have a very detailed question. " + "z" * 600

        history = [
            self._make_user_message(long_human_text, "1.0"),  # old, >500 chars
            self._make_bot_message("Short reply.", "2.0"),
            self._make_user_message("Another question", "3.0"),
            self._make_bot_message("Another reply.", "4.0"),
            self._make_user_message("More", "5.0"),
            self._make_bot_message("Final.", "6.0"),
        ]

        prompt = build_conversation_prompt(
            history,
            "Current msg",
            bot_user_id="BOT123",
        )

        # Human message should be in full even though it is old and long
        assert "z" * 600 in prompt
        assert "[Earlier response summarized]" not in prompt

    def test_short_bot_messages_not_masked(self):
        """Old bot messages under 500 chars are NOT masked."""
        short_text = "CPA is $25. ROAS is 3.5x."  # well under 500 chars

        history = [
            self._make_bot_message(short_text, "1.0"),  # old, but short
            self._make_user_message("Q1", "2.0"),
            self._make_user_message("Q2", "3.0"),
            self._make_user_message("Q3", "4.0"),
            self._make_user_message("Q4", "5.0"),
        ]

        prompt = build_conversation_prompt(
            history,
            "Current",
            bot_user_id="BOT123",
        )

        # Short bot message should be kept in full
        assert "CPA is $25. ROAS is 3.5x." in prompt
        assert "[Earlier response summarized]" not in prompt

    def test_empty_history_works(self):
        """No history still produces a valid prompt with current message."""
        prompt = build_conversation_prompt(
            [],
            "Hello there",
            bot_user_id="BOT123",
        )

        assert "Hello there" in prompt
        assert "Current Message" in prompt
        # No conversation history section
        assert "Conversation History" not in prompt

    def test_current_message_always_included(self):
        """The current_message is always present at the end."""
        history = [
            self._make_user_message("Old msg", "1.0"),
            self._make_bot_message("Old reply", "2.0"),
        ]

        prompt = build_conversation_prompt(
            history,
            "This is the current message",
            bot_user_id="BOT123",
        )

        assert "This is the current message" in prompt
        # Current message should come after the history
        history_end = prompt.index("Old reply")
        current_pos = prompt.index("This is the current message")
        assert current_pos > history_end

    def test_bot_user_id_matching(self):
        """When bot_user_id is provided, messages matching that user are
        identified as bot messages for masking purposes."""
        verbose_text = "Detailed analysis follows. " + "w" * 600

        history = [
            # Bot message identified by user ID (not is_bot flag)
            {"user": "BOT123", "text": verbose_text, "ts": "1.0", "bot_id": "", "is_bot": False},
            self._make_user_message("Q", "2.0"),
            self._make_user_message("Q", "3.0"),
            self._make_user_message("Q", "4.0"),
            self._make_user_message("Q", "5.0"),
        ]

        prompt = build_conversation_prompt(
            history,
            "Current",
            bot_user_id="BOT123",
        )

        # Should be masked because user matches bot_user_id
        assert "[Earlier response summarized]" in prompt
        assert "w" * 600 not in prompt
