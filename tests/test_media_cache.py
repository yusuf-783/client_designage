import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest

from client.app.database.connection import ClientDatabase
from client.app.media.cache_manager import MediaCacheManager, MediaStatus
from client.app.player.mpv_player import MediaPlayer


@pytest.fixture
def temp_cache_env():
    """Create isolated temporary environment for media cache testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test_client.sqlite"
        media_dir = tmp_path / "media"
        temp_dir = tmp_path / "temp"

        media_dir.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)

        db = ClientDatabase(db_path=str(db_path))
        manager = MediaCacheManager(media_dir=str(media_dir), temp_dir=str(temp_dir))

        yield {
            "root": tmp_path,
            "db": db,
            "manager": manager,
            "media_dir": media_dir,
            "temp_dir": temp_dir,
        }


@pytest.mark.asyncio
async def test_successful_download_and_atomic_rename(temp_cache_env):
    """Test standard successful media download, SHA-256 verification, and atomic rename to ready."""
    env = temp_cache_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]
    temp_dir = env["temp_dir"]

    # Sample binary payload
    payload = b"DIGITAL SIGNAGE PROMO VIDEO CONTENT 2026"
    expected_sha256 = hashlib.sha256(payload).hexdigest().lower()

    # Register metadata
    media_data = {
        "server_media_id": 101,
        "uuid": "media-uuid-101",
        "filename": "promo_video.mp4",
        "original_filename": "promo.mp4",
        "filesize": len(payload),
        "sha256": expected_sha256,
        "status": MediaStatus.PENDING,
    }
    db.upsert_media(media_data)

    # Mock HTTP transport returning stream
    async def mock_stream_get(*args, **kwargs):
        req = httpx.Request("GET", "http://server/client/media/media-uuid-101/file")
        return httpx.Response(200, content=payload, request=req)

    with patch("httpx.AsyncClient.stream") as mock_stream, patch(
        "client.app.media.cache_manager.client_db", db
    ):
        mock_stream.return_value.__aenter__.return_value = httpx.Response(
            200, content=payload, request=httpx.Request("GET", "http://test")
        )

        ok = await manager.download_media(server_media_id=101, download_url="http://test/file")
        assert ok is True

        # Check target file exists in media_dir
        target_file = media_dir / "promo_video.mp4"
        assert target_file.exists()
        assert target_file.read_bytes() == payload

        # Check no temporary files left behind
        assert len(list(temp_dir.iterdir())) == 0

        # Check SQLite record status is READY
        record = db.get_media_by_server_id(101)
        assert record is not None
        assert record["status"] == MediaStatus.READY
        assert record["local_path"] == str(target_file.resolve())
        assert record["downloaded_at"] is not None


@pytest.mark.asyncio
async def test_failure_checksum_mismatch(temp_cache_env):
    """Test that download fails, temp file is deleted, and status becomes failed when checksum does not match."""
    env = temp_cache_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]
    temp_dir = env["temp_dir"]

    actual_payload = b"CORRUPTED OR TAMPERED BYTES"
    wrong_expected_sha256 = "1111111111111111111111111111111111111111111111111111111111111111"

    media_data = {
        "server_media_id": 102,
        "uuid": "media-uuid-102",
        "filename": "tampered_slide.png",
        "filesize": len(actual_payload),
        "sha256": wrong_expected_sha256,
        "status": MediaStatus.PENDING,
    }
    db.upsert_media(media_data)

    with patch("httpx.AsyncClient.stream") as mock_stream, patch(
        "client.app.media.cache_manager.client_db", db
    ):
        mock_stream.return_value.__aenter__.return_value = httpx.Response(
            200, content=actual_payload, request=httpx.Request("GET", "http://test")
        )

        ok = await manager.download_media(server_media_id=102, download_url="http://test/file")
        assert ok is False

        # Target file must NOT exist
        target_file = media_dir / "tampered_slide.png"
        assert not target_file.exists()

        # Temporary file must be purged
        assert len(list(temp_dir.iterdir())) == 0

        # Status must be FAILED
        record = db.get_media_by_server_id(102)
        assert record["status"] == MediaStatus.FAILED
        assert "Checksum mismatch" in record["error_message"]


@pytest.mark.asyncio
async def test_failure_network_interruption(temp_cache_env):
    """Test network failure or server error purges temp file and marks status failed."""
    env = temp_cache_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]
    temp_dir = env["temp_dir"]

    media_data = {
        "server_media_id": 103,
        "uuid": "media-uuid-103",
        "filename": "interrupted.mp4",
        "filesize": 5000,
        "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "status": MediaStatus.PENDING,
    }
    db.upsert_media(media_data)

    with patch("httpx.AsyncClient.stream") as mock_stream, patch(
        "client.app.media.cache_manager.client_db", db
    ):
        # Simulate HTTP 500 Internal Server Error
        mock_stream.return_value.__aenter__.return_value = httpx.Response(
            500, content=b"Server Internal Error", request=httpx.Request("GET", "http://test")
        )

        ok = await manager.download_media(server_media_id=103, download_url="http://test/file")
        assert ok is False

        assert not (media_dir / "interrupted.mp4").exists()
        assert len(list(temp_dir.iterdir())) == 0

        record = db.get_media_by_server_id(103)
        assert record["status"] == MediaStatus.FAILED


@pytest.mark.asyncio
async def test_failure_disk_full_prevention(temp_cache_env):
    """Test that download is rejected immediately when available disk space is insufficient."""
    env = temp_cache_env
    db = env["db"]
    manager = env["manager"]

    media_data = {
        "server_media_id": 104,
        "uuid": "media-uuid-104",
        "filename": "giant_video.mp4",
        "filesize": 100 * 1024 * 1024 * 1024,  # 100 GB
        "sha256": "abcdef",
        "status": MediaStatus.PENDING,
    }
    db.upsert_media(media_data)

    with patch.object(manager, "check_disk_space", return_value=False), patch(
        "client.app.media.cache_manager.client_db", db
    ):
        ok = await manager.download_media(server_media_id=104, download_url="http://test/file")
        assert ok is False

        record = db.get_media_by_server_id(104)
        assert record["status"] == MediaStatus.FAILED
        assert "Insufficient disk space" in record["error_message"]


def test_boot_crash_recovery(temp_cache_env):
    """Test crash recovery sweeps temp files, resets downloading status, and verifies ready files."""
    env = temp_cache_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]
    temp_dir = env["temp_dir"]

    # 1. Place orphaned temp files from an interrupted download
    stale_tmp = temp_dir / "999_orphaned.tmp"
    stale_tmp.write_bytes(b"INCOMPLETE DATA BEFORE CRASH")

    # 2. Insert record in 'downloading' status
    db.upsert_media({
        "server_media_id": 201,
        "uuid": "uuid-201",
        "filename": "interrupted_media.jpg",
        "filesize": 100,
        "sha256": "hash201",
        "status": MediaStatus.DOWNLOADING,
    })

    # 3. Insert record in 'ready' status whose physical file is missing
    db.upsert_media({
        "server_media_id": 202,
        "uuid": "uuid-202",
        "filename": "missing_on_disk.jpg",
        "filesize": 200,
        "sha256": "hash202",
        "local_path": str(media_dir / "missing_on_disk.jpg"),
        "status": MediaStatus.READY,
    })

    # 4. Insert record in 'ready' status whose physical file is corrupted
    valid_content = b"VALID IMAGE 203"
    valid_sha256 = hashlib.sha256(valid_content).hexdigest().lower()
    corrupted_file = media_dir / "corrupted.jpg"
    corrupted_file.write_bytes(b"CORRUPTED BYTES")  # Bad bytes!

    db.upsert_media({
        "server_media_id": 203,
        "uuid": "uuid-203",
        "filename": "corrupted.jpg",
        "filesize": len(valid_content),
        "sha256": valid_sha256,
        "local_path": str(corrupted_file),
        "status": MediaStatus.READY,
    })

    # 5. Insert record in 'ready' status with genuine file
    genuine_file = media_dir / "genuine.jpg"
    genuine_content = b"GENUINE GOOD MEDIA"
    genuine_sha256 = hashlib.sha256(genuine_content).hexdigest().lower()
    genuine_file.write_bytes(genuine_content)

    db.upsert_media({
        "server_media_id": 204,
        "uuid": "uuid-204",
        "filename": "genuine.jpg",
        "filesize": len(genuine_content),
        "sha256": genuine_sha256,
        "local_path": str(genuine_file),
        "status": MediaStatus.READY,
    })

    # Run Crash Recovery
    with patch("client.app.media.cache_manager.client_db", db):
        manager.recover_cache()

        # Check stale temp file is deleted
        assert not stale_tmp.exists()

        # Check downloading record was reset to pending
        rec_201 = db.get_media_by_server_id(201)
        assert rec_201["status"] == MediaStatus.PENDING

        # Check missing file record was reset to pending
        rec_202 = db.get_media_by_server_id(202)
        assert rec_202["status"] == MediaStatus.PENDING

        # Check corrupted file was purged and reset to pending
        rec_203 = db.get_media_by_server_id(203)
        assert rec_203["status"] == MediaStatus.PENDING
        assert not corrupted_file.exists()

        # Check genuine file remains READY
        rec_204 = db.get_media_by_server_id(204)
        assert rec_204["status"] == MediaStatus.READY
        assert genuine_file.exists()


def test_player_strictly_rejects_non_ready_media(temp_cache_env):
    """Test that player strictly refuses to play media with pending, downloading, or failed status."""
    env = temp_cache_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]

    # 1. Register PENDING media
    db.upsert_media({
        "server_media_id": 301,
        "filename": "pending.jpg",
        "filesize": 100,
        "sha256": "h1",
        "status": MediaStatus.PENDING,
    })

    # 2. Register DOWNLOADING media
    db.upsert_media({
        "server_media_id": 302,
        "filename": "downloading.jpg",
        "filesize": 100,
        "sha256": "h2",
        "status": MediaStatus.DOWNLOADING,
    })

    # 3. Register FAILED media
    db.upsert_media({
        "server_media_id": 303,
        "filename": "failed.jpg",
        "filesize": 100,
        "sha256": "h3",
        "status": MediaStatus.FAILED,
    })

    # 4. Register READY media with valid physical file
    ready_file = media_dir / "valid_ready.jpg"
    ready_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    db.upsert_media({
        "server_media_id": 304,
        "filename": "valid_ready.jpg",
        "filesize": 24,
        "sha256": hashlib.sha256(ready_file.read_bytes()).hexdigest(),
        "local_path": str(ready_file),
        "status": MediaStatus.READY,
    })

    player = MediaPlayer()

    with patch("client.app.media.cache_manager.client_db", db), patch(
        "client.app.player.mpv_player.media_cache", manager
    ):
        # Pending -> must return False
        assert player.play_cached_media(server_media_id=301) is False
        assert manager.get_playable_path(301) is None

        # Downloading -> must return False
        assert player.play_cached_media(server_media_id=302) is False
        assert manager.get_playable_path(302) is None

        # Failed -> must return False
        assert player.play_cached_media(server_media_id=303) is False
        assert manager.get_playable_path(303) is None

        # Ready -> must return True
        assert player.play_cached_media(server_media_id=304) is True
        assert manager.get_playable_path(304) == ready_file
