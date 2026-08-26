import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

# Bootstrap sys.path
_client_root = str(Path(__file__).resolve().parent.parent)
_workspace_root = str(Path(__file__).resolve().parent.parent.parent)
for p in [_client_root, _workspace_root]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from app.api.client_api import signage_api
    from app.auth.client_auth import client_auth
    from app.core.config import client_config
    from app.core.logging import logger
    from app.database.connection import client_db
    from app.media.cache_manager import media_cache
    from app.player.mpv_player import media_player
    from app.player.playback_engine import playback_engine
    from app.sync.service import sync_engine
except ImportError:
    from client.app.api.client_api import signage_api
    from client.app.auth.client_auth import client_auth
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db
    from client.app.media.cache_manager import media_cache
    from client.app.player.mpv_player import media_player
    from client.app.player.playback_engine import playback_engine
    from client.app.sync.service import sync_engine


class SignageClientApplication:
    """
    Main Raspberry Pi Client Application.
    Implements Instant Boot Playback and Background Synchronization.
    """

    def __init__(self) -> None:
        self.running = False
        self.authenticated = False

    def setup_signal_handlers(self) -> None:
        """Attach OS interrupt handlers for clean shutdown."""
        def _handle_exit(sig, frame):
            logger.info("Received termination signal. Initiating graceful shutdown...")
            self.running = False

        try:
            signal.signal(signal.SIGINT, _handle_exit)
            signal.signal(signal.SIGTERM, _handle_exit)
        except Exception:
            pass

    async def initialize(self) -> bool:
        """
        Instant Boot Sequence:
        1. Initialize SQLite local database schema.
        2. Recover media cache and purge leftover temporary files.
        3. Start Playback Engine immediately with local Active Playlist (DO NOT WAIT FOR SERVER).
        4. Attempt server login and state refresh in non-blocking fashion.
        """
        logger.info(
            f"=== Digital Signage Client (Offline-First) Starting ===\n"
            f"Device ID: {client_config.device.id}\n"
            f"Device Name: {client_config.device.name}\n"
            f"Server Target: {client_config.server.base_url}\n"
            f"Database: {client_config.storage.database_file}\n"
            f"Media Directory: {client_config.storage.media_dir}\n"
            f"Player Backend: {client_config.player.backend}"
        )

        # 1. Initialize SQLite schema & recover media cache
        client_db.init_schema()
        media_cache.recover_cache()

        # 2. Instant Local Playback: Launch playback loop immediately from local SQLite
        active_pl = client_db.get_active_playlist()
        if active_pl:
            logger.info(
                f"Boot: Found Local Active Playlist '{active_pl['name']}' (v{active_pl['version']}). "
                "Starting Instant Offline Playback..."
            )
        else:
            logger.info("Boot: No active playlist cached locally yet. Standby mode active.")

        await playback_engine.start()

        # 3. Non-blocking Server Login
        try:
            if await signage_api.check_server_health():
                if await signage_api.login():
                    self.authenticated = True
                    await signage_api.get_device_profile()
                    logger.info("Client authenticated with server.")
            else:
                logger.warning(
                    f"Server {client_config.server.base_url} is currently unreachable. "
                    "Operating in continuous Offline-First mode."
                )
        except Exception as e:
            logger.warning(f"Initial server connection check skipped: {e}")

        logger.info("Client status: OK")
        return True

    async def start(self, single_cycle: bool = False) -> None:
        """Run client daemon lifecycle."""
        self.running = True
        self.setup_signal_handlers()

        await self.initialize()

        # 4. Launch background sync worker
        await sync_engine.start()

        logger.info("Digital Signage Client is running and active.")

        while self.running:
            if single_cycle:
                break
            await asyncio.sleep(1)

        self.cleanup()

    def cleanup(self) -> None:
        """Graceful shutdown of playback engine, background sync, and media player."""
        logger.info("Cleaning up client daemon processes...")
        sync_engine.stop()
        playback_engine.stop()
        media_player.stop_media()
        logger.info("Digital Signage Client shut down successfully.")


def main() -> None:
    app = SignageClientApplication()
    try:
        asyncio.run(app.start())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
