from __future__ import annotations

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


class Security:
    def __init__(self, session_secret: str, encryption_key: str) -> None:
        self._signer = URLSafeTimedSerializer(session_secret, salt="discord-oauth-state")
        self._cipher = Fernet(encryption_key.encode())

    @staticmethod
    def random_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def session_hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def sign_oauth_state(self, state: str) -> str:
        return self._signer.dumps(state)

    def verify_oauth_state(self, signed: str, returned: str) -> bool:
        try:
            expected = self._signer.loads(signed, max_age=600)
        except (BadSignature, SignatureExpired):
            return False
        return secrets.compare_digest(expected, returned)

    def encrypt(self, value: str) -> str:
        return self._cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._cipher.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Stored Discord credential cannot be decrypted") from exc

