"""
Encrypted credential vault.

Secrets are stored as AES-128-CBC + HMAC (Fernet) in data/vault.enc.
The master key is derived from a passphrase via PBKDF2-HMAC-SHA256.

On first use the vault creates a salt file (data/vault.salt) and prompts
for a passphrase via Streamlit's session-state gate or a CLI prompt.
Subsequent uses derive the same key from the same passphrase + salt.

Agent tools:
    vault_store_secret(name, value)  → encrypts + stores
    vault_get_secret(name)           → decrypts + returns (never logs value)
    vault_list_secrets()             → names only, no values

Why this matters: credentials the agent needs for autonomous tasks
(IRCTC password, BookMyShow saved-card, etc.) live here instead of
appearing in conversation history, .env files, or system prompt text.
"""

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

_DATA_DIR = Path(__file__).parent.parent / "data"
_VAULT_PATH = _DATA_DIR / "vault.enc"
_SALT_PATH  = _DATA_DIR / "vault.salt"

_PBKDF2_ITERATIONS = 390_000   # OWASP 2023 recommendation for PBKDF2-HMAC-SHA256


# ── Key derivation ────────────────────────────────────────────────────────

def _get_or_create_salt() -> bytes:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _SALT_PATH.exists():
        return _SALT_PATH.read_bytes()
    salt = os.urandom(16)
    _SALT_PATH.write_bytes(salt)
    return salt


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _get_fernet(passphrase: str) -> Fernet:
    salt = _get_or_create_salt()
    key  = _derive_key(passphrase, salt)
    return Fernet(key)


# ── Vault read / write ─────────────────────────────────────────────────────

def _load_vault(fernet: Fernet) -> dict:
    if not _VAULT_PATH.exists():
        return {}
    try:
        raw = fernet.decrypt(_VAULT_PATH.read_bytes())
        return json.loads(raw.decode("utf-8"))
    except InvalidToken:
        raise ValueError("Wrong passphrase — cannot decrypt vault.")
    except Exception as e:
        raise ValueError(f"Vault read error: {e}")


def _save_vault(fernet: Fernet, data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    _VAULT_PATH.write_bytes(fernet.encrypt(raw))


# ── Session passphrase cache (in-process only, never persisted) ───────────
# Stored as a module-level variable so the user only types the passphrase
# once per agent session. On next agent restart it clears.

_cached_passphrase: str | None = None


def set_passphrase(passphrase: str) -> None:
    global _cached_passphrase
    _cached_passphrase = passphrase


def has_passphrase() -> bool:
    return _cached_passphrase is not None


def clear_passphrase() -> None:
    global _cached_passphrase
    _cached_passphrase = None


def vault_is_initialized() -> bool:
    return _VAULT_PATH.exists() and _SALT_PATH.exists()


# ── Public API ────────────────────────────────────────────────────────────

def store_secret(name: str, value: str, passphrase: str | None = None) -> str:
    pw = passphrase or _cached_passphrase
    if not pw:
        return "Vault locked — call vault_unlock first."
    name = name.strip()
    if not name:
        return "Error: secret name cannot be empty."
    try:
        f = _get_fernet(pw)
        data = _load_vault(f)
        data[name] = value
        _save_vault(f, data)
        return f"Secret '{name}' stored in vault."
    except ValueError as e:
        return f"Error: {e}"


def get_secret(name: str, passphrase: str | None = None) -> str:
    pw = passphrase or _cached_passphrase
    if not pw:
        return "Vault locked — call vault_unlock first."
    name = name.strip()
    try:
        f = _get_fernet(pw)
        data = _load_vault(f)
        if name not in data:
            return f"No secret named '{name}' in vault. Use vault_list_secrets to see what's stored."
        return data[name]
    except ValueError as e:
        return f"Error: {e}"


def list_secrets(passphrase: str | None = None) -> str:
    pw = passphrase or _cached_passphrase
    if not pw:
        return "Vault locked — call vault_unlock first."
    try:
        f = _get_fernet(pw)
        data = _load_vault(f)
        if not data:
            return "Vault is empty. Use vault_store_secret to add credentials."
        names = sorted(data.keys())
        return "Stored secrets (names only — values never shown):\n" + "\n".join(f"  - {n}" for n in names)
    except ValueError as e:
        return f"Error: {e}"


def unlock_vault(passphrase: str) -> str:
    try:
        f = _get_fernet(passphrase)
        _load_vault(f)      # validate the passphrase by decrypting
        set_passphrase(passphrase)
        count = len(_load_vault(f))
        return f"Vault unlocked. {count} secret(s) available this session."
    except ValueError as e:
        return f"Error: {e}"


def delete_secret(name: str, passphrase: str | None = None) -> str:
    pw = passphrase or _cached_passphrase
    if not pw:
        return "Vault locked — call vault_unlock first."
    name = name.strip()
    try:
        f = _get_fernet(pw)
        data = _load_vault(f)
        if name not in data:
            return f"No secret named '{name}'."
        del data[name]
        _save_vault(f, data)
        return f"Secret '{name}' deleted from vault."
    except ValueError as e:
        return f"Error: {e}"
