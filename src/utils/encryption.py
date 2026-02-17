"""Application-level token encryption for Sidera.

Provides MultiFernet symmetric encryption for OAuth tokens stored in the
database. Uses ``MultiFernet`` to support key rotation: encrypts with the
current key and can decrypt with either the current or previous key.

Tokens are prefixed with ``enc:`` after encryption so the system can
distinguish encrypted tokens from legacy plaintext ones (backward compatible).

When ``TOKEN_ENCRYPTION_KEY`` is not set (empty string), functions pass through
without encrypting, allowing development/test use without a key.

Key rotation workflow::

    1. Generate a new Fernet key
    2. Move the current key to ``TOKEN_ENCRYPTION_KEY_PREVIOUS``
    3. Set the new key as ``TOKEN_ENCRYPTION_KEY``
    4. Run ``scripts/rotate_encryption_key.py`` to re-encrypt all tokens
    5. Once re-encrypted, ``TOKEN_ENCRYPTION_KEY_PREVIOUS`` can be cleared

Usage::

    from src.utils.encryption import encrypt_token, decrypt_token, rotate_token

    # Encrypt before saving to DB
    encrypted = encrypt_token("my_secret_token")
    # Returns: "enc:gAAAAABh..." or "my_secret_token" if no key set

    # Decrypt when reading from DB
    plaintext = decrypt_token(encrypted)
    # Returns: "my_secret_token"

    # Re-encrypt with current key (during key rotation)
    rotated = rotate_token(encrypted)
"""

from __future__ import annotations

import structlog
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from src.config import settings

logger = structlog.get_logger(__name__)

_ENC_PREFIX = "enc:"
_multi_fernet_instance: MultiFernet | None = None


def get_multi_fernet() -> MultiFernet | None:
    """Get or create the MultiFernet instance from configured keys.

    Builds a ``MultiFernet`` with the current key first (used for encryption)
    and the previous key second (used for decryption during rotation).

    Returns:
        A ``MultiFernet`` instance if ``token_encryption_key`` is configured,
        ``None`` otherwise.
    """
    global _multi_fernet_instance  # noqa: PLW0603
    if _multi_fernet_instance is not None:
        return _multi_fernet_instance

    key = settings.token_encryption_key
    if not key:
        return None

    try:
        fernets: list[Fernet] = [
            Fernet(key.encode() if isinstance(key, str) else key),
        ]

        prev_key = settings.token_encryption_key_previous
        if prev_key:
            fernets.append(Fernet(prev_key.encode() if isinstance(prev_key, str) else prev_key))

        _multi_fernet_instance = MultiFernet(fernets)
        return _multi_fernet_instance
    except Exception as exc:
        logger.error("encryption.invalid_key", error=str(exc))
        raise ValueError(
            "TOKEN_ENCRYPTION_KEY is invalid. Generate one with: "
            "python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        ) from exc


# Backward-compatible alias
def get_fernet() -> Fernet | None:
    """Get encryption instance (backward-compatible alias).

    Returns the underlying ``MultiFernet`` (which is also a valid
    encrypt/decrypt interface). Returns ``None`` when no key is configured.
    """
    return get_multi_fernet()  # type: ignore[return-value]


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string and return it with the ``enc:`` prefix.

    If no encryption key is configured, returns the plaintext unchanged
    (pass-through mode for development/test).

    Args:
        plaintext: The token to encrypt.

    Returns:
        Encrypted string prefixed with ``enc:``, or the original plaintext
        if encryption is not configured.
    """
    if not plaintext:
        return plaintext

    mf = get_multi_fernet()
    if mf is None:
        return plaintext  # Pass-through: no key configured

    encrypted = mf.encrypt(plaintext.encode("utf-8"))
    return f"{_ENC_PREFIX}{encrypted.decode('utf-8')}"


def decrypt_token(stored: str) -> str:
    """Decrypt a token string, handling both encrypted and plaintext formats.

    If the stored value starts with ``enc:``, it is decrypted using
    MultiFernet (tries current key first, then previous key).
    Otherwise, it is returned as-is (backward compatibility with
    pre-encryption tokens).

    Args:
        stored: The stored token (may be encrypted or plaintext).

    Returns:
        The decrypted plaintext token.

    Raises:
        ValueError: If the token is encrypted but decryption fails
            (wrong key, corrupted data, etc.).
    """
    if not stored:
        return stored

    if not stored.startswith(_ENC_PREFIX):
        return stored  # Plaintext — backward compatible

    mf = get_multi_fernet()
    if mf is None:
        logger.warning(
            "encryption.decrypt_without_key",
            hint="Token is encrypted but TOKEN_ENCRYPTION_KEY is not set",
        )
        raise ValueError(
            "Cannot decrypt token: TOKEN_ENCRYPTION_KEY is not configured. "
            "Set the same key that was used to encrypt."
        )

    try:
        ciphertext = stored[len(_ENC_PREFIX) :]
        decrypted = mf.decrypt(ciphertext.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt token: invalid ciphertext or wrong key") from exc


def rotate_token(stored: str) -> str:
    """Decrypt a token with any available key and re-encrypt with the current key.

    Use this during key rotation to re-encrypt tokens that were encrypted
    with the previous key. If the token is already encrypted with the
    current key, it will be re-encrypted (new ciphertext, same plaintext).

    Args:
        stored: The stored token (may be encrypted or plaintext).

    Returns:
        The token re-encrypted with the current key. Plaintext tokens
        are encrypted for the first time. Empty strings pass through.
    """
    if not stored:
        return stored

    # Decrypt (handles both enc: prefixed and plaintext)
    plaintext = decrypt_token(stored)

    # Re-encrypt with current key
    return encrypt_token(plaintext)
