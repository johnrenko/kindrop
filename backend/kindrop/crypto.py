import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    def __init__(self, key_file: Path) -> None:
        try:
            key = key_file.read_bytes().strip()
            self.fernet = Fernet(key)
        except (OSError, ValueError) as error:
            raise SecretStoreError(
                f"Kindrop could not read a valid encryption key from {key_file}"
            ) from error

    def encrypt_json(self, value: dict[str, Any]) -> str:
        return self.fernet.encrypt(json.dumps(value).encode()).decode()

    def decrypt_json(self, value: str) -> dict[str, Any]:
        try:
            decoded = self.fernet.decrypt(value.encode())
            result = json.loads(decoded)
        except (InvalidToken, json.JSONDecodeError) as error:
            raise SecretStoreError("An encrypted Google credential could not be opened") from error
        if not isinstance(result, dict):
            raise SecretStoreError("The encrypted credential has an invalid shape")
        return result
