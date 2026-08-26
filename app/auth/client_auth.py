from typing import Dict, Optional

try:
    from app.core.config import client_config
    from app.core.logging import logger
    from app.database.connection import client_db
except ImportError:
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db


class ClientAuthManager:
    """Manages Raspberry Pi client JWT credentials and persistence in local SQLite."""

    TOKEN_SETTING_KEY = "auth_token"
    DEVICE_ID_SETTING_KEY = "auth_device_id"

    def __init__(self) -> None:
        self._cached_token: Optional[str] = None

    def get_token(self) -> Optional[str]:
        """Retrieve active JWT access token from memory or local SQLite settings."""
        if self._cached_token:
            return self._cached_token

        token = client_db.get_setting(self.TOKEN_SETTING_KEY)
        if token:
            self._cached_token = token
        return token

    def save_token(self, token: str, device_id: Optional[str] = None) -> None:
        """Persist JWT access token in local SQLite."""
        self._cached_token = token
        client_db.set_setting(self.TOKEN_SETTING_KEY, token)
        if device_id:
            client_db.set_setting(self.DEVICE_ID_SETTING_KEY, device_id)
        logger.info("Client authentication token stored securely in local SQLite settings.")

    def clear_token(self) -> None:
        """Purge stored token on logout or invalidation."""
        self._cached_token = None
        client_db.delete_setting(self.TOKEN_SETTING_KEY)
        logger.info("Client authentication token cleared.")

    def is_authenticated(self) -> bool:
        """Check if client currently possesses a stored access token."""
        return bool(self.get_token())

    def get_auth_headers(self) -> Dict[str, str]:
        """Generate Authorization headers for outgoing server HTTP requests."""
        headers: Dict[str, str] = {
            "X-Device-ID": client_config.device.id,
            "Accept": "application/json",
        }
        token = self.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


client_auth = ClientAuthManager()
