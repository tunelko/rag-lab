"""Encrypted API credentials; plaintext is never persisted or returned by the API."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hmac
import os
from pathlib import Path
import platform
import secrets
import sqlite3
from threading import RLock


PROVIDERS = frozenset({"scaleway", "ovh", "xai"})
_AAD = b"rag-local:provider-credential:v1"
_VERIFIER = b"rag-local encrypted credential store v1"


class CredentialError(ValueError):
    pass


class CredentialStoreLocked(CredentialError):
    pass


class CredentialStore:
    """DPAPI on Windows; passphrase-derived AES-GCM on other systems."""

    def __init__(self, path: Path, *, system: str | None = None):
        self.path = Path(path)
        self.system = (system or platform.system()).lower()
        self.lock = RLock()
        self._key: bytes | None = None
        self.db: sqlite3.Connection | None = None

    @property
    def scheme(self):
        return "dpapi-user-v1" if self.system == "windows" else "aesgcm-scrypt-v1"

    @property
    def locked(self):
        return self.scheme != "dpapi-user-v1" and self._key is None

    def _connect(self):
        if self.db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS credentials(
                    provider TEXT PRIMARY KEY,
                    scheme TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS credential_meta(
                    key TEXT PRIMARY KEY,
                    value BLOB NOT NULL
                );
                PRAGMA user_version=1;
            """)
        return self.db

    def _meta(self, key):
        row = self._connect().execute("SELECT value FROM credential_meta WHERE key=?", (key,)).fetchone()
        return bytes(row[0]) if row else None

    def _set_meta(self, key, value):
        self._connect().execute(
            "INSERT INTO credential_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @staticmethod
    def _derive(passphrase: str, salt: bytes):
        if len(passphrase) < 12:
            raise CredentialError("La contraseña maestra necesita al menos 12 caracteres.")
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))

    @staticmethod
    def _aes_encrypt(key: bytes, plaintext: bytes, aad: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)

    @staticmethod
    def _aes_decrypt(key: bytes, payload: bytes, aad: bytes):
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(payload) < 29:
            raise CredentialError("El almacén cifrado está dañado.")
        try:
            return AESGCM(key).decrypt(payload[:12], payload[12:], aad)
        except InvalidTag as exc:
            raise CredentialError("Contraseña maestra incorrecta o almacén cifrado dañado.") from exc

    def unlock(self, passphrase: str):
        if self.scheme == "dpapi-user-v1":
            return self.status()
        with self.lock:
            db = self._connect()
            salt = self._meta("salt")
            verifier = self._meta("verifier")
            if salt is None:
                salt = secrets.token_bytes(16)
            key = self._derive(passphrase, salt)
            if verifier is None:
                verifier = self._aes_encrypt(key, _VERIFIER, _AAD + b":verifier")
                with db:
                    self._set_meta("salt", salt)
                    self._set_meta("verifier", verifier)
            else:
                decoded = self._aes_decrypt(key, verifier, _AAD + b":verifier")
                if not hmac.compare_digest(decoded, _VERIFIER):
                    raise CredentialError("Contraseña maestra incorrecta.")
            self._key = key
            return self.status()

    def lock_store(self):
        with self.lock:
            self._key = None

    @staticmethod
    def _blob(data: bytes):
        buffer = ctypes.create_string_buffer(data)
        return buffer, _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))

    def _dpapi(self, plaintext: bytes | None = None, payload: bytes | None = None):
        if self.system != "windows":
            raise CredentialError("DPAPI solo está disponible en Windows.")
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        entropy_buffer, entropy = self._blob(_AAD)
        source_buffer, source = self._blob(plaintext if plaintext is not None else payload or b"")
        output = _DataBlob()
        flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
        if plaintext is not None:
            ok = crypt32.CryptProtectData(ctypes.byref(source), "RAG Local", ctypes.byref(entropy), None, None,
                                          flags, ctypes.byref(output))
        else:
            ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                                            flags, ctypes.byref(output))
        # Keep backing buffers alive until the Win32 call has returned.
        del entropy_buffer, source_buffer
        if not ok:
            raise CredentialError(f"Windows no pudo {'cifrar' if plaintext is not None else 'descifrar'} la credencial.")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)

    def _encrypt(self, provider: str, value: str):
        raw = value.encode("utf-8")
        if self.scheme == "dpapi-user-v1":
            return self._dpapi(plaintext=raw)
        if self._key is None:
            raise CredentialStoreLocked("Desbloquea primero el almacén cifrado con la contraseña maestra.")
        return self._aes_encrypt(self._key, raw, _AAD + b":" + provider.encode("ascii"))

    def _decrypt(self, provider: str, scheme: str, payload: bytes):
        if scheme != self.scheme:
            raise CredentialError("La credencial fue cifrada para otro sistema operativo o usuario.")
        if scheme == "dpapi-user-v1":
            return self._dpapi(payload=payload).decode("utf-8")
        if self._key is None:
            raise CredentialStoreLocked("El almacén de credenciales está bloqueado.")
        return self._aes_decrypt(self._key, payload, _AAD + b":" + provider.encode("ascii")).decode("utf-8")

    @staticmethod
    def _provider(provider):
        if provider not in PROVIDERS:
            raise CredentialError("Proveedor de credenciales no válido.")
        return provider

    def set(self, provider: str, value: str):
        provider = self._provider(provider)
        value = value.strip()
        if not 8 <= len(value) <= 8192 or any(character in value for character in "\r\n\0"):
            raise CredentialError("La clave API no tiene un formato válido.")
        with self.lock:
            payload = self._encrypt(provider, value)
            with self._connect():
                self._connect().execute(
                    "INSERT INTO credentials(provider,scheme,payload,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(provider) DO UPDATE SET scheme=excluded.scheme,payload=excluded.payload,updated_at=excluded.updated_at",
                    (provider, self.scheme, payload, datetime.now(timezone.utc).isoformat()),
                )
        return self.status()

    def get(self, provider: str):
        provider = self._provider(provider)
        with self.lock:
            row = self._connect().execute(
                "SELECT scheme,payload FROM credentials WHERE provider=?", (provider,)
            ).fetchone()
            if not row:
                raise CredentialError("No hay una clave API configurada para este proveedor.")
            return self._decrypt(provider, row[0], bytes(row[1]))

    def delete(self, provider: str):
        provider = self._provider(provider)
        with self.lock, self._connect():
            self._connect().execute("DELETE FROM credentials WHERE provider=?", (provider,))
        return self.status()

    def status(self):
        with self.lock:
            rows = self._connect().execute("SELECT provider,updated_at FROM credentials").fetchall()
        configured = {provider: updated for provider, updated in rows}
        return {
            "scheme": self.scheme,
            "locked": self.locked,
            "configured": {provider: provider in configured for provider in sorted(PROVIDERS)},
            "updated_at": configured,
        }

    def close(self):
        with self.lock:
            self._key = None
            if self.db is not None:
                self.db.close()
                self.db = None

class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
