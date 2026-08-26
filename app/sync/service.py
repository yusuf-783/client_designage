import asyncio
from typing import Any, Dict, List, Optional

try:
    from app.api.client_api import signage_api
    from app.core.config import client_config
    from app.core.logging import logger
    from app.database.connection import client_db
    from app.media.cache_manager import media_cache
    from app.monitoring.health import system_monitor
    from app.player.playback_engine import playback_engine
except ImportError:
    from client.app.api.client_api import signage_api
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db
    from client.app.media.cache_manager import media_cache
    from client.app.monitoring.health import system_monitor
    from client.app.player.playback_engine import playback_engine


class SyncEngine:
    """
    Offline-First Background Synchronization & Staging Engine:
    - Periodically syncs default fallback playlists and active timetable schedules.
    - Stages playlist manifests in local SQLite before downloading media.
    - Pre-caches all media assets for scheduled playlists so they are 100% READY.
    - Performs atomic commit on playlist versions.
    """

    def __init__(self) -> None:
        self.running: bool = False
        self.is_online: bool = False
        self.task: Optional[asyncio.Task] = None
        self.last_sync_error: Optional[str] = None

    async def start(self) -> None:
        """Start background polling loop in asyncio task."""
        if not self.running:
            self.running = True
            self.task = asyncio.create_task(self.run_loop())
            logger.info("Background Sync Engine started.")

    def stop(self) -> None:
        """Gracefully stop synchronization loop."""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
        logger.info("Background Sync Engine stopped.")

    async def run_loop(self) -> None:
        """Continuous background sync loop."""
        logger.info("Entering continuous sync loop...")
        while self.running:
            try:
                await self.run_sync_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_sync_error = str(e)
                logger.error(f"Error during background sync cycle: {e}")

            # Sleep interval with interruptible polling
            for _ in range(client_config.server.sync_interval_seconds):
                if not self.running:
                    break
                await asyncio.sleep(1)

    async def run_sync_cycle(self) -> bool:
        """
        Execute single synchronization & staging pass:
        1. Check server health.
        2. Dispatch heartbeat telemetry.
        3. Fetch assigned default playlist manifest & schedule rules.
        4. Stage all playlists and pre-cache all media assets.
        5. Verify 100% READY status and commit default playlist.
        """
        logger.debug("Sync Engine: Probing server status...")

        # 1. Probe server health
        server_healthy = await signage_api.check_server_health()
        if not server_healthy:
            if self.is_online or not hasattr(self, "_offline_warned"):
                logger.warning(
                    f"Sync Engine: Server {client_config.server.base_url} is OFFLINE. "
                    "Continuing local offline playback and timetable scheduling seamlessly."
                )
                self._offline_warned = True
            self.is_online = False
            return False

        if not self.is_online:
            logger.info("Sync Engine: Reconnected to Server! Operating in ONLINE mode.")
            self.is_online = True
            self._offline_warned = False

        # 2. Transmit Heartbeat Telemetry
        try:
            telemetry = system_monitor.get_heartbeat_payload(
                sync_status="syncing" if self.last_sync_error else "synced",
                last_error=self.last_sync_error,
            )
            await signage_api.send_heartbeat(telemetry)
        except Exception as e:
            logger.warning(f"Sync Engine: Could not send heartbeat: {e}")

        all_downloads_ok = True

        # 3. Sync Timetable Schedules
        try:
            schedules_data = await signage_api.get_assigned_schedules()
            if schedules_data is not None:
                client_db.save_schedules(schedules_data)
                logger.debug(f"Sync Engine: Synchronized {len(schedules_data)} timetable schedules to local SQLite.")

                # Stage scheduled playlists and pre-cache their media
                for s in schedules_data:
                    pl_manifest = s.get("playlist_manifest")
                    if pl_manifest:
                        pl_items = pl_manifest.get("items", [])
                        client_db.stage_pending_playlist(pl_manifest, pl_items)
                        for item in pl_items:
                            ready = await self._ensure_media_downloaded(item)
                            if not ready:
                                all_downloads_ok = False
        except Exception as e:
            logger.warning(f"Sync Engine: Error syncing timetable schedules: {e}")

        # 4. Fetch assigned default playlist manifest
        playlist_manifest = await signage_api.get_assigned_playlist()
        if not playlist_manifest:
            logger.debug("Sync Engine: No default playlist on server for this device.")
            self.last_sync_error = None
            return True

        server_pl_id = playlist_manifest["id"]
        server_pl_version = playlist_manifest["version"]
        server_pl_name = playlist_manifest.get("name", "Playlist")
        items = playlist_manifest.get("items", [])

        # 5. Check active version in local SQLite
        active_pl = client_db.get_active_playlist()
        current_active_version = active_pl.get("version") if active_pl else None
        current_active_id = active_pl.get("id") if active_pl else None

        logger.debug(
            f"Sync Engine Check -> Server Default Playlist: '{server_pl_name}' (ID: {server_pl_id}, v{server_pl_version}) | "
            f"Local Active: ID {current_active_id}, v{current_active_version}"
        )

        # 6. Stage Pending Playlist in SQLite
        client_db.stage_pending_playlist(playlist_manifest, items)

        # 7. Register and Download all required media assets
        for item in items:
            ready = await self._ensure_media_downloaded(item)
            if not ready:
                all_downloads_ok = False

        # 8. Atomic Commit Decision for Default Playlist
        if all_downloads_ok and len(items) > 0:
            self.last_sync_error = None
            if current_active_id != server_pl_id or current_active_version != server_pl_version:
                logger.info(
                    f"Sync Engine: All media verified 100% READY! Performing ATOMIC COMMIT -> "
                    f"Upgrading from v{current_active_version} to '{server_pl_name}' v{server_pl_version}"
                )
                client_db.commit_active_playlist(server_pl_id)
                playback_engine.reload_active_playlist()
                return True
            else:
                logger.debug(f"Sync Engine: Default playlist is already up-to-date (v{server_pl_version}).")
                return True
        else:
            logger.warning(
                f"Sync Engine: Staging incomplete for Playlist '{server_pl_name}' v{server_pl_version}. "
                f"Active playlist remains on v{current_active_version}."
            )
            return False

    async def _ensure_media_downloaded(self, item: Dict[str, Any]) -> bool:
        """Register metadata and download media file if not ready."""
        media_info = item.get("media") or {}
        server_media_id = item.get("media_id") or item.get("server_media_id") or media_info.get("id")
        if not server_media_id:
            return True

        # Ensure media metadata is registered in SQLite
        if media_info:
            client_db.upsert_media({
                "server_media_id": server_media_id,
                "uuid": media_info.get("uuid"),
                "filename": media_info.get("filename"),
                "original_filename": media_info.get("original_filename"),
                "filesize": media_info.get("filesize", 0),
                "sha256": media_info.get("sha256", ""),
                "mime_type": media_info.get("mime_type"),
                "duration": media_info.get("duration"),
                "width": media_info.get("width"),
                "height": media_info.get("height"),
            })

        # Check if media is already READY on disk
        if not media_cache.is_media_ready(server_media_id):
            logger.info(f"Sync Engine: Downloading media asset {server_media_id}...")
            download_ok = await media_cache.download_media(server_media_id)
            if not download_ok:
                logger.error(f"Sync Engine: Failed to download media asset {server_media_id}.")
                self.last_sync_error = f"Failed to download media asset {server_media_id}"
                return False
        return True


sync_engine = SyncEngine()
