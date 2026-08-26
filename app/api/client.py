from typing import Any, Dict, Optional
import httpx

try:
    from app.core.config import client_config
    from app.core.logging import logger
except ImportError:
    from client.app.core.config import client_config
    from client.app.core.logging import logger


class SignageServerClient:
    """HTTP Client for communicating with the Digital Signage Server."""

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None) -> None:
        self.base_url = (base_url or client_config.server.base_url).rstrip("/")
        self.api_prefix = client_config.server.api_prefix
        self.timeout = timeout or client_config.server.timeout_seconds

    @property
    def api_url(self) -> str:
        return f"{self.base_url}{self.api_prefix}"

    async def check_server_health(self) -> Dict[str, Any]:
        """Check if server /api/v1/health is reachable and operational."""
        endpoint = f"{self.api_url}/health"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(endpoint)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.warning(f"Server health check failed at {endpoint}: {e}")
                return {"success": False, "error": str(e)}

    async def send_heartbeat(self, telemetry: Dict[str, Any]) -> bool:
        """Send device heartbeat and telemetry status to server."""
        endpoint = f"{self.api_url}/monitoring/heartbeat"
        payload = {
            "device_id": client_config.device.id,
            "telemetry": telemetry,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(endpoint, json=payload)
                return response.status_code == 200
            except httpx.HTTPError as e:
                logger.debug(f"Heartbeat dispatch skipped/offline: {e}")
                return False
