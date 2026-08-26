import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

try:
    from app.api.client_api import signage_api
    from app.auth.client_auth import client_auth
    from app.core.config import client_config
    from app.core.logging import logger
    from app.database.connection import client_db
except ImportError:
    from client.app.api.client_api import signage_api
    from client.app.auth.client_auth import client_auth
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db


class MediaCacheError(Exception):
    """Base exception for media caching failures."""
    pass


class ChecksumMismatchError(MediaCacheError):
    """Raised when downloaded file SHA-256 does not match server metadata."""
    pass


class DiskFullError(MediaCacheError):
    """Raised when storage space is insufficient for download."""
    pass


class MediaStatus:
    PENDING = "pending"
    DOWNLOADING = "downloading"
    READY = "ready"
    FAILED = "failed"


class MediaCacheManager:
    """
    Manages client media download lifecycle:
    Server -> Temp -> SHA256 Verification -> Atomic Rename -> READY.
    Enforces integrity, disk space checking, boot crash recovery, and ready-only playback.
    """

    MIN_DISK_MARGIN_BYTES = 10 * 1024 * 1024  # 10 MB safety margin

    def __init__(
        self,
        media_dir: Optional[str] = None,
        temp_dir: Optional[str] = None,
    ) -> None:
        self.media_dir = Path(media_dir or client_config.storage.media_dir)
        self.temp_dir = Path(temp_dir or client_config.storage.temp_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def calculate_sha256(file_path: Path) -> str:
        """Compute SHA-256 hex digest of local file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest().lower()

    def check_disk_space(self, required_bytes: int) -> bool:
        """Verify available disk space before initiating download."""
        try:
            usage = shutil.disk_usage(str(self.media_dir))
            return usage.free >= (required_bytes + self.MIN_DISK_MARGIN_BYTES)
        except Exception:
            return True

    def register_media_metadata(self, media_data: Dict[str, Any]) -> None:
        """Register or update media item in local SQLite database."""
        client_db.upsert_media(media_data)

    async def download_media(
        self,
        server_media_id: int,
        download_url: Optional[str] = None,
    ) -> bool:
        """
        Download media asset from server with real-time SHA-256 verification and atomic move.
        Flow:
        1. Fetch & validate media record from SQLite.
        2. Check disk space.
        3. Set status: 'downloading'.
        4. Stream to temporary file in temp_dir.
        5. Verify SHA-256 hash.
        6. Atomic rename into media_dir.
        7. Set status: 'ready'.
        """
        media_record = client_db.get_media_by_server_id(server_media_id)
        if not media_record:
            logger.error(f"Cannot download media {server_media_id}: Not found in local database.")
            return False

        filename = media_record["filename"]
        expected_sha256 = (media_record.get("sha256") or "").lower()
        filesize = media_record.get("filesize", 0)
        uuid_str = media_record.get("uuid") or f"id-{server_media_id}"

        # 1. Target and Temp paths
        target_path = self.media_dir / filename
        temp_file_path = self.temp_dir / f"{server_media_id}_{uuid_str}.tmp"

        # If already ready and file exists with valid hash, skip download
        if (
            media_record.get("status") == MediaStatus.READY
            and target_path.exists()
            and self.calculate_sha256(target_path) == expected_sha256
        ):
            logger.debug(f"Media '{filename}' is already cached and valid. Skipping download.")
            return True

        # 2. Check Disk Space
        if not self.check_disk_space(filesize):
            err_msg = f"Insufficient disk space to download '{filename}' ({filesize} bytes required)."
            logger.error(err_msg)
            client_db.update_media_status(
                server_media_id=server_media_id,
                status=MediaStatus.FAILED,
                error_message=err_msg,
            )
            return False

        # 3. Mark status as DOWNLOADING
        client_db.update_media_status(
            server_media_id=server_media_id,
            status=MediaStatus.DOWNLOADING,
            error_message=None,
        )

        # Build download URL if not provided
        if not download_url:
            base_url = f"{client_config.server.base_url.rstrip('/')}{client_config.server.api_prefix}"
            download_url = f"{base_url}/client/media/{uuid_str}/file"

        headers = client_auth.get_auth_headers()
        hasher = hashlib.sha256()

        try:
            logger.info(f"Starting download for '{filename}' -> '{temp_file_path.name}'...")
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
            verify_opt = (
                client_config.server.ca_bundle
                if client_config.server.ca_bundle
                else client_config.server.verify_ssl
            )

            async with httpx.AsyncClient(timeout=timeout, verify=verify_opt) as client:
                async with client.stream("GET", download_url, headers=headers) as response:
                    if response.status_code != 200:
                        raise MediaCacheError(f"Server returned HTTP {response.status_code}: {response.text}")

                    with open(temp_file_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                                hasher.update(chunk)

            # 4. Verify SHA-256 Checksum
            computed_sha256 = hasher.hexdigest().lower()
            if expected_sha256 and computed_sha256 != expected_sha256:
                raise ChecksumMismatchError(
                    f"Checksum mismatch for '{filename}': expected {expected_sha256}, got {computed_sha256}"
                )

            # 5. Atomic Rename from Temp to Permanent Media Directory
            if temp_file_path.exists():
                os.replace(str(temp_file_path), str(target_path))

            now_iso = datetime.now(timezone.utc).isoformat()

            # 6. Update Status to READY
            client_db.update_media_status(
                server_media_id=server_media_id,
                status=MediaStatus.READY,
                local_path=str(target_path.resolve()),
                downloaded_at=now_iso,
                error_message=None,
            )

            logger.info(f"Media '{filename}' successfully cached and verified [READY] at {target_path}")
            return True

        except ChecksumMismatchError as e:
            logger.error(f"Checksum verification failed: {e}")
            if temp_file_path.exists():
                try:
                    temp_file_path.unlink()
                except Exception:
                    pass
            client_db.update_media_status(
                server_media_id=server_media_id,
                status=MediaStatus.FAILED,
                error_message=str(e),
            )
            return False

        except Exception as e:
            logger.error(f"Download failure for media {server_media_id} ('{filename}'): {e}")
            # Ensure invalid temporary file is purged
            if temp_file_path.exists():
                try:
                    temp_file_path.unlink()
                except Exception:
                    pass
            client_db.update_media_status(
                server_media_id=server_media_id,
                status=MediaStatus.FAILED,
                error_message=str(e),
            )
            return False

    def recover_cache(self) -> None:
        """
        Execute crash recovery during client startup/boot:
        1. Purge all orphaned '.tmp' files in temp directory.
        2. Reset any interrupted 'downloading' database statuses back to 'pending'.
        3. Verify all 'ready' media files on disk; revert to 'pending' if missing or corrupted.
        """
        logger.info("Executing Media Cache Recovery & Integrity Check...")

        # 1. Sweep temp directory
        try:
            if self.temp_dir.exists():
                for tmp_file in self.temp_dir.iterdir():
                    if tmp_file.is_file():
                        try:
                            tmp_file.unlink()
                            logger.debug(f"Removed orphaned temp file: {tmp_file.name}")
                        except Exception as e:
                            logger.warning(f"Could not remove temp file {tmp_file.name}: {e}")
        except Exception as e:
            logger.error(f"Error sweeping temp directory: {e}")

        # 2. Reset interrupted 'downloading' states
        reset_count = client_db.reset_downloading_to_pending()
        if reset_count > 0:
            logger.info(f"Reset {reset_count} interrupted media downloads from 'downloading' to 'pending'.")

        # 3. Validate integrity of existing 'ready' files
        ready_items = client_db.get_all_media(status=MediaStatus.READY)
        for item in ready_items:
            server_id = item["server_media_id"]
            local_path_str = item.get("local_path")
            expected_sha256 = (item.get("sha256") or "").lower()

            if not local_path_str or not Path(local_path_str).exists():
                logger.warning(f"Media '{item.get('filename')}' marked READY but missing on disk. Resetting to PENDING.")
                client_db.update_media_status(
                    server_media_id=server_id,
                    status=MediaStatus.PENDING,
                    error_message="Physical file missing on disk after reboot",
                )
                continue

            # Verify sha256 checksum of physical file
            physical_file = Path(local_path_str)
            actual_hash = self.calculate_sha256(physical_file)
            if expected_sha256 and actual_hash != expected_sha256:
                logger.warning(
                    f"Media '{item.get('filename')}' checksum mismatch on disk. "
                    f"Expected {expected_sha256}, got {actual_hash}. Resetting to PENDING."
                )
                try:
                    physical_file.unlink()
                except Exception:
                    pass
                client_db.update_media_status(
                    server_media_id=server_id,
                    status=MediaStatus.PENDING,
                    error_message="Physical file corrupted on disk, re-queued",
                )

        logger.info("Media Cache Recovery completed successfully.")

    def get_playable_path(self, server_media_id: int) -> Optional[Path]:
        """
        STRICT ENFORCEMENT:
        Returns local Path ONLY if media status is READY and physical file exists.
        Returns None if pending, downloading, failed, or corrupted.
        """
        media_record = client_db.get_media_by_server_id(server_media_id)
        if not media_record:
            logger.warning(f"Media {server_media_id} not registered in local database.")
            return None

        status = media_record.get("status")
        if status != MediaStatus.READY:
            logger.warning(
                f"Cannot play media {server_media_id} ('{media_record.get('filename')}'): "
                f"Status is '{status}' (Must be '{MediaStatus.READY}')."
            )
            return None

        local_path_str = media_record.get("local_path")
        if not local_path_str:
            return None

        p = Path(local_path_str)
        if not p.exists():
            logger.error(f"Cannot play media {server_media_id}: File not found at {local_path_str}")
            return None

        return p

    def is_media_ready(self, server_media_id: int) -> bool:
        """Check if media is ready for playback."""
        return self.get_playable_path(server_media_id) is not None


media_cache = MediaCacheManager()
