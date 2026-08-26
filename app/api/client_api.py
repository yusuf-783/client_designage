from typing import Any, Dict, List, Optional
import httpx

try:
    from app.auth.client_auth import client_auth
    from app.core.config import client_config
    from app.core.logging import logger
    from app.database.connection import client_db
except ImportError:
    from client.app.auth.client_auth import client_auth
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db


class SignageServerApiClient:
    """HTTP client communicating with Digital Signage backend server."""

    def __init__(self) -> None:
        self.base_url = f"{client_config.server.base_url.rstrip('/')}{client_config.server.api_prefix}"
        self.timeout = client_config.server.timeout_seconds

    def _get_http_client(self) -> httpx.AsyncClient:
        """Create configured httpx.AsyncClient enforcing TLS verification."""
        verify: Any = (
            client_config.server.ca_bundle
            if client_config.server.ca_bundle
            else client_config.server.verify_ssl
        )
        return httpx.AsyncClient(timeout=self.timeout, verify=verify)

    async def check_server_health(self) -> bool:
        """Probe server health endpoint."""
        url = f"{self.base_url}/health"
        try:
            async with self._get_http_client() as client:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    logger.debug(f"Server health check OK: {data.get('data', {})}")
                    return True
                logger.warning(f"Server health check returned HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"Could not connect to server at {url}: {e}")
            return False

    async def login(self) -> bool:
        """Authenticate Raspberry Pi client with backend server and store token in SQLite."""
        url = f"{self.base_url}/client/login"
        payload = {
            "device_id": client_config.device.id,
            "username": client_config.device.username,
            "password": client_config.device.password,
            "client_version": "0.1.0",
        }

        try:
            async with self._get_http_client() as client:
                response = await client.post(url, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    token = data.get("access_token")
                    device_data = data.get("device", {})

                    if token:
                        client_auth.save_token(token, device_id=client_config.device.id)
                        if device_data:
                            client_db.save_device_state(device_data)
                        logger.info(
                            f"Client authenticated successfully with server. Device: '{device_data.get('device_name')}' "
                            f"(ID: {device_data.get('device_id')}, Status: {device_data.get('status')})"
                        )
                        return True
                    else:
                        logger.error("Server response missing access_token.")
                        return False

                elif response.status_code == 401:
                    logger.error("Authentication failed: Invalid device_id, username, or password.")
                    return False
                elif response.status_code == 403:
                    logger.error("Authentication failed: Device is disabled on server.")
                    return False
                else:
                    logger.error(f"Authentication failed with HTTP {response.status_code}: {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Network error during client login to {url}: {e}")
            return False

    async def get_device_profile(self) -> Optional[Dict[str, Any]]:
        """Fetch latest device profile and active assigned playlist."""
        url = f"{self.base_url}/client/me"

        # Ensure we have a token
        if not client_auth.is_authenticated():
            logged_in = await self.login()
            if not logged_in:
                return None

        headers = client_auth.get_auth_headers()

        try:
            async with self._get_http_client() as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    device_data = response.json()
                    client_db.save_device_state(device_data)
                    return device_data

                elif response.status_code in [401, 403]:
                    logger.warning("Token rejected by server. Attempting re-authentication...")
                    client_auth.clear_token()
                    if await self.login():
                        # Retry with new token
                        new_headers = client_auth.get_auth_headers()
                        retry_resp = await client.get(url, headers=new_headers)
                        if retry_resp.status_code == 200:
                            data = retry_resp.json()
                            client_db.save_device_state(data)
                            return data

                logger.warning(f"Failed to fetch device profile (HTTP {response.status_code})")
                return None

        except Exception as e:
            logger.error(f"Error fetching device profile from {url}: {e}")
            return None

    async def get_assigned_playlist(self) -> Optional[Dict[str, Any]]:
        """Fetch full assigned playlist manifest with ordered items and media checksums."""
        url = f"{self.base_url}/client/playlist"

        if not client_auth.is_authenticated():
            logged_in = await self.login()
            if not logged_in:
                return None

        headers = client_auth.get_auth_headers()

        try:
            async with self._get_http_client() as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    return data
                elif response.status_code in [401, 403]:
                    logger.warning("Token expired while fetching playlist. Retrying authentication...")
                    client_auth.clear_token()
                    if await self.login():
                        new_headers = client_auth.get_auth_headers()
                        retry_resp = await client.get(url, headers=new_headers)
                        if retry_resp.status_code == 200:
                            return retry_resp.json()

                logger.warning(f"Could not retrieve assigned playlist (HTTP {response.status_code})")
                return None

        except Exception as e:
            logger.error(f"Network error fetching assigned playlist from {url}: {e}")
            return None

    async def get_assigned_schedules(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch all active playback timetable schedules assigned to this device."""
        url = f"{self.base_url}/client/media/schedules"

        if not client_auth.is_authenticated():
            logged_in = await self.login()
            if not logged_in:
                return None

        headers = client_auth.get_auth_headers()

        try:
            async with self._get_http_client() as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code in [401, 403]:
                    logger.warning("Token expired while fetching schedules. Retrying authentication...")
                    client_auth.clear_token()
                    if await self.login():
                        new_headers = client_auth.get_auth_headers()
                        retry_resp = await client.get(url, headers=new_headers)
                        if retry_resp.status_code == 200:
                            return retry_resp.json()

                logger.warning(f"Could not retrieve schedules (HTTP {response.status_code})")
                return None

        except Exception as e:
            logger.error(f"Network error fetching schedules from {url}: {e}")
            return None

    async def send_heartbeat(self, telemetry_payload: Dict[str, Any]) -> bool:
        """Send hardware and playback telemetry to server heartbeat endpoint."""
        url = f"{self.base_url}/client/heartbeat"

        if not client_auth.is_authenticated():
            logged_in = await self.login()
            if not logged_in:
                return False

        headers = client_auth.get_auth_headers()

        try:
            async with self._get_http_client() as client:
                response = await client.post(url, json=telemetry_payload, headers=headers)

                if response.status_code == 200:
                    logger.debug(f"Heartbeat sent successfully to {url}")
                    return True
                elif response.status_code in [401, 403]:
                    logger.warning("Token expired during heartbeat dispatch. Re-authenticating...")
                    client_auth.clear_token()
                    if await self.login():
                        new_headers = client_auth.get_auth_headers()
                        retry_resp = await client.post(url, json=telemetry_payload, headers=new_headers)
                        return retry_resp.status_code == 200

                logger.warning(f"Heartbeat failed with HTTP {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Network error sending heartbeat to {url}: {e}")
            return False


signage_api = SignageServerApiClient()
