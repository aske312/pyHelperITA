from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretStore:
    """Шифрует секреты перед записью в хранилище."""

    def __init__(self, key: str | bytes):
        if not key:
            raise ValueError(
                "Для сохранения пароля задайте INTEGRATION_SECRET_KEY "
                "(сгенерируйте через Fernet.generate_key())"
            )
        try:
            self._cipher = Fernet(key.encode() if isinstance(key, str) else key)
        except (TypeError, ValueError) as error:
            raise ValueError("INTEGRATION_SECRET_KEY не является ключом Fernet") from error

    def encrypt(self, value: str) -> str:
        return self._cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._cipher.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as error:
            raise ValueError("Секрет повреждён или зашифрован другим ключом") from error
