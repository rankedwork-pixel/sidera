"""Tests for src.utils.encryption — MultiFernet-based token encryption."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Valid Fernet keys generated at import time for deterministic test use.
TEST_KEY = Fernet.generate_key().decode()
# A second valid key, different from the first, for wrong-key / rotation tests.
WRONG_KEY = Fernet.generate_key().decode()
# A third key for multi-key rotation scenarios.
OLD_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_fernet_singleton():
    """Reset the module-level cached MultiFernet instance between every test."""
    import src.utils.encryption as enc_mod

    enc_mod._multi_fernet_instance = None
    yield
    enc_mod._multi_fernet_instance = None


def _patch_key(key_value: str):
    """Return a context-manager that patches ``settings.token_encryption_key``."""
    return patch("src.utils.encryption.settings.token_encryption_key", key_value)


def _patch_prev_key(key_value: str):
    """Return a context-manager that patches ``settings.token_encryption_key_previous``."""
    return patch("src.utils.encryption.settings.token_encryption_key_previous", key_value)


# ===================================================================
# 1. encrypt_token / decrypt_token round-trip
# ===================================================================


class TestRoundTrip:
    def test_round_trip_basic(self):
        """Encrypting then decrypting recovers the original plaintext."""
        from src.utils.encryption import decrypt_token, encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            original = "ya29.super_secret_oauth_token"
            encrypted = encrypt_token(original)
            assert encrypted != original
            assert encrypted.startswith("enc:")
            assert decrypt_token(encrypted) == original

    def test_round_trip_unicode(self):
        """Round-trip works with non-ASCII characters."""
        from src.utils.encryption import decrypt_token, encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            original = "token-with-unicode-\u00e9\u00e0\u00fc\u2603"
            encrypted = encrypt_token(original)
            assert decrypt_token(encrypted) == original

    def test_round_trip_long_token(self):
        """Round-trip works with a very long token string."""
        from src.utils.encryption import decrypt_token, encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            original = "x" * 4096
            encrypted = encrypt_token(original)
            assert decrypt_token(encrypted) == original


# ===================================================================
# 2. Backward compatibility — plaintext passthrough on decrypt
# ===================================================================


class TestBackwardCompat:
    def test_decrypt_returns_plaintext_when_no_prefix(self):
        """decrypt_token returns plaintext as-is when there is no enc: prefix."""
        from src.utils.encryption import decrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            plain = "ya29.legacy_plain_token"
            assert decrypt_token(plain) == plain

    def test_decrypt_returns_plaintext_when_no_key(self):
        """Even with no key configured, plaintext tokens come back unchanged."""
        from src.utils.encryption import decrypt_token

        with _patch_key(""), _patch_prev_key(""):
            plain = "ya29.legacy_plain_token"
            assert decrypt_token(plain) == plain


# ===================================================================
# 3. Pass-through mode (empty key)
# ===================================================================


class TestPassThroughMode:
    def test_encrypt_passthrough_when_key_empty(self):
        """encrypt_token returns plaintext unchanged when no key is set."""
        from src.utils.encryption import encrypt_token

        with _patch_key(""), _patch_prev_key(""):
            token = "some_token"
            assert encrypt_token(token) == token

    def test_decrypt_passthrough_when_key_empty_and_no_prefix(self):
        """decrypt_token returns plaintext unchanged when no key is set."""
        from src.utils.encryption import decrypt_token

        with _patch_key(""), _patch_prev_key(""):
            token = "some_token"
            assert decrypt_token(token) == token


# ===================================================================
# 4. Empty string input
# ===================================================================


class TestEmptyString:
    def test_encrypt_empty_string(self):
        """encrypt_token returns empty string for empty input."""
        from src.utils.encryption import encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            assert encrypt_token("") == ""

    def test_decrypt_empty_string(self):
        """decrypt_token returns empty string for empty input."""
        from src.utils.encryption import decrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            assert decrypt_token("") == ""


# ===================================================================
# 5. Invalid ciphertext raises ValueError
# ===================================================================


class TestInvalidCiphertext:
    def test_decrypt_garbled_ciphertext(self):
        """Decrypting garbage after enc: prefix raises ValueError."""
        from src.utils.encryption import decrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            with pytest.raises(ValueError, match="invalid ciphertext or wrong key"):
                decrypt_token("enc:this_is_not_valid_fernet_ciphertext")

    def test_decrypt_wrong_key(self):
        """Decrypting with a different key raises ValueError."""
        import src.utils.encryption as enc_mod
        from src.utils.encryption import decrypt_token, encrypt_token

        # Encrypt with TEST_KEY
        with _patch_key(TEST_KEY), _patch_prev_key(""):
            encrypted = encrypt_token("secret")

        # Reset singleton so it picks up the new key
        enc_mod._multi_fernet_instance = None

        # Decrypt with WRONG_KEY (no previous key)
        with _patch_key(WRONG_KEY), _patch_prev_key(""):
            with pytest.raises(ValueError, match="invalid ciphertext or wrong key"):
                decrypt_token(encrypted)


# ===================================================================
# 6. enc: prefix detection
# ===================================================================


class TestPrefixDetection:
    def test_encrypted_tokens_have_prefix(self):
        """encrypt_token output always starts with 'enc:'."""
        from src.utils.encryption import encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            result = encrypt_token("token123")
            assert result.startswith("enc:")

    def test_prefix_not_added_in_passthrough(self):
        """In pass-through mode the prefix is never added."""
        from src.utils.encryption import encrypt_token

        with _patch_key(""), _patch_prev_key(""):
            result = encrypt_token("token123")
            assert not result.startswith("enc:")


# ===================================================================
# 7. decrypt with enc: prefix but no key configured → ValueError
# ===================================================================


class TestDecryptWithoutKey:
    def test_raises_when_encrypted_but_no_key(self):
        """Attempting to decrypt an enc:-prefixed value with no key raises."""
        from src.utils.encryption import decrypt_token

        with _patch_key(""), _patch_prev_key(""):
            with pytest.raises(ValueError, match="TOKEN_ENCRYPTION_KEY is not configured"):
                decrypt_token("enc:gAAAAABh_some_cipher_bytes")


# ===================================================================
# 8-9. get_multi_fernet() behavior
# ===================================================================


class TestGetMultiFernet:
    def test_valid_key_returns_multifernet(self):
        """get_multi_fernet returns a MultiFernet instance when key is valid."""
        from cryptography.fernet import MultiFernet

        from src.utils.encryption import get_multi_fernet

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            f = get_multi_fernet()
            assert isinstance(f, MultiFernet)

    def test_empty_key_returns_none(self):
        """get_multi_fernet returns None when key is empty string."""
        from src.utils.encryption import get_multi_fernet

        with _patch_key(""), _patch_prev_key(""):
            assert get_multi_fernet() is None

    def test_invalid_key_raises_value_error(self):
        """get_multi_fernet raises ValueError for a non-base64 / wrong-length key."""
        from src.utils.encryption import get_multi_fernet

        with _patch_key("not-a-valid-fernet-key!!!"), _patch_prev_key(""):
            with pytest.raises(ValueError, match="TOKEN_ENCRYPTION_KEY is invalid"):
                get_multi_fernet()

    def test_get_fernet_alias_works(self):
        """get_fernet() backward-compatible alias returns the MultiFernet."""
        from src.utils.encryption import get_fernet

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            f = get_fernet()
            assert f is not None


# ===================================================================
# 10. MultiFernet singleton / caching
# ===================================================================


class TestMultiFernetCaching:
    def test_same_instance_returned_twice(self):
        """Calling get_multi_fernet() twice returns the exact same object."""
        from src.utils.encryption import get_multi_fernet

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            first = get_multi_fernet()
            second = get_multi_fernet()
            assert first is second

    def test_singleton_survives_key_change(self):
        """Once cached, the singleton is returned even if the key changes."""
        import src.utils.encryption as enc_mod
        from src.utils.encryption import get_multi_fernet

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            first = get_multi_fernet()

        # Patch a different key — singleton should still return the old one
        with _patch_key(WRONG_KEY), _patch_prev_key(""):
            second = get_multi_fernet()
            assert first is second

        enc_mod._multi_fernet_instance = None


# ===================================================================
# 11. Multiple encrypt/decrypt cycles
# ===================================================================


class TestMultipleCycles:
    def test_encrypt_decrypt_many_values(self):
        """Multiple different tokens can each round-trip correctly."""
        from src.utils.encryption import decrypt_token, encrypt_token

        tokens = [
            "ya29.short",
            "EAAGe0BlaToken" + "x" * 200,
            "1/refresh_token_abc123",
            "\u00e9\u00e0\u00fc",
        ]

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            for original in tokens:
                encrypted = encrypt_token(original)
                assert decrypt_token(encrypted) == original


# ===================================================================
# 12. Different plaintexts → different ciphertexts
# ===================================================================


class TestCiphertextUniqueness:
    def test_different_plaintexts_different_ciphertexts(self):
        """Two distinct plaintext values produce distinct ciphertexts."""
        from src.utils.encryption import encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            a = encrypt_token("token_aaa")
            b = encrypt_token("token_bbb")
            assert a != b

    def test_same_plaintext_different_ciphertexts(self):
        """Fernet produces unique ciphertext on each call (random IV)."""
        from src.utils.encryption import encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            c1 = encrypt_token("same_value")
            c2 = encrypt_token("same_value")
            assert c1 != c2


# ===================================================================
# 13. Double-encryption behavior
# ===================================================================


class TestDoubleEncryption:
    def test_encrypt_does_not_guard_against_double_encryption(self):
        """encrypt_token will re-encrypt an already-encrypted value."""
        from src.utils.encryption import decrypt_token, encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            original = "my_token"
            once = encrypt_token(original)
            twice = encrypt_token(once)

            assert twice != once
            assert twice.startswith("enc:")

            inner = decrypt_token(twice)
            assert inner == once
            assert inner.startswith("enc:")

            assert decrypt_token(inner) == original


# ===================================================================
# 14. Key rotation — MultiFernet with previous key
# ===================================================================


class TestKeyRotation:
    def test_decrypt_with_previous_key(self):
        """Token encrypted with old key can be decrypted when old is set as previous."""
        import src.utils.encryption as enc_mod
        from src.utils.encryption import decrypt_token, encrypt_token

        # Encrypt with OLD_KEY
        with _patch_key(OLD_KEY), _patch_prev_key(""):
            encrypted = encrypt_token("my_secret")

        # Reset singleton
        enc_mod._multi_fernet_instance = None

        # Now set TEST_KEY as current, OLD_KEY as previous
        with _patch_key(TEST_KEY), _patch_prev_key(OLD_KEY):
            # Should decrypt successfully using the previous key
            result = decrypt_token(encrypted)
            assert result == "my_secret"

    def test_encrypt_always_uses_current_key(self):
        """New encryptions always use the current (first) key."""
        import src.utils.encryption as enc_mod
        from src.utils.encryption import decrypt_token, encrypt_token

        # Encrypt with current=TEST_KEY, previous=OLD_KEY
        with _patch_key(TEST_KEY), _patch_prev_key(OLD_KEY):
            encrypted = encrypt_token("new_secret")

        enc_mod._multi_fernet_instance = None

        # Verify it can be decrypted with TEST_KEY alone (no previous)
        with _patch_key(TEST_KEY), _patch_prev_key(""):
            assert decrypt_token(encrypted) == "new_secret"

    def test_rotate_token_reencrypts(self):
        """rotate_token decrypts with any key and re-encrypts with current."""
        import src.utils.encryption as enc_mod
        from src.utils.encryption import decrypt_token, encrypt_token, rotate_token

        # Encrypt with OLD_KEY
        with _patch_key(OLD_KEY), _patch_prev_key(""):
            old_encrypted = encrypt_token("rotate_me")

        enc_mod._multi_fernet_instance = None

        # Rotate: current=TEST_KEY, previous=OLD_KEY
        with _patch_key(TEST_KEY), _patch_prev_key(OLD_KEY):
            rotated = rotate_token(old_encrypted)

        enc_mod._multi_fernet_instance = None

        # Verify rotated token works with TEST_KEY alone
        with _patch_key(TEST_KEY), _patch_prev_key(""):
            assert decrypt_token(rotated) == "rotate_me"

    def test_rotate_token_empty_passthrough(self):
        """rotate_token returns empty string for empty input."""
        from src.utils.encryption import rotate_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            assert rotate_token("") == ""

    def test_rotate_plaintext_encrypts(self):
        """rotate_token encrypts plaintext tokens for the first time."""
        from src.utils.encryption import decrypt_token, rotate_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            rotated = rotate_token("plain_token")
            assert rotated.startswith("enc:")
            assert decrypt_token(rotated) == "plain_token"

    def test_multifernet_current_key_only(self):
        """MultiFernet works correctly with just the current key (no previous)."""
        from src.utils.encryption import decrypt_token, encrypt_token

        with _patch_key(TEST_KEY), _patch_prev_key(""):
            encrypted = encrypt_token("solo_key")
            assert decrypt_token(encrypted) == "solo_key"

    def test_both_keys_invalid_previous_raises(self):
        """Invalid previous key raises ValueError."""
        from src.utils.encryption import get_multi_fernet

        with _patch_key(TEST_KEY), _patch_prev_key("not-valid-key!!!"):
            with pytest.raises(ValueError, match="TOKEN_ENCRYPTION_KEY is invalid"):
                get_multi_fernet()
