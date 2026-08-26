from typing import Dict

try:
    from app.core.config import client_config
except ImportError:
    from client.app.core.config import client_config


class DeviceAuthenticator:
    """Handles device authentication headers and token verification."""

    @staticmethod
    def get_auth_headers() -> Dict[str, str]:
        return {
            "X-Device-ID": client_config.device.id,
            "Authorization": f"Bearer {client_config.device.secret_token}",
        }
