import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from client.app.database.connection import ClientDatabase
from client.app.media.cache_manager import MediaCacheManager, MediaStatus
from client.app.player.mpv_player import MediaPlayer
from client.app.player.playback_engine import PlaybackEngine
from client.app.sync.service import SyncEngine


@pytest.fixture
def offline_env():
    """Create isolated temporary environment for offline-first testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test_offline.sqlite"
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


def _create_dummy_media_file(media_dir: Path, filename: str, content: bytes) -> str:
    """Helper to create dummy media on disk and return its SHA-256."""
    file_path = media_dir / filename
    file_path.write_bytes(content)
    return hashlib.sha256(content).hexdigest().lower()


def test_instant_boot_offline_playback(offline_env):
    """
    Test Instant Boot:
    Raspberry Pi boots while server is completely offline/dead.
    Player must load active playlist from SQLite and play local media immediately without waiting for server.
    """
    env = offline_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]

    # 1. Pre-seed local SQLite with Active Playlist Version 10
    hash1 = _create_dummy_media_file(media_dir, "slide1.jpg", b"SLIDE ONE CONTENT")
    hash2 = _create_dummy_media_file(media_dir, "slide2.jpg", b"SLIDE TWO CONTENT")

    db.upsert_media({
        "server_media_id": 1,
        "uuid": "u1",
        "filename": "slide1.jpg",
        "filesize": 17,
        "sha256": hash1,
        "local_path": str(media_dir / "slide1.jpg"),
        "status": MediaStatus.READY,
    })
    db.upsert_media({
        "server_media_id": 2,
        "uuid": "u2",
        "filename": "slide2.jpg",
        "filesize": 17,
        "sha256": hash2,
        "local_path": str(media_dir / "slide2.jpg"),
        "status": MediaStatus.READY,
    })

    # Stage and commit Version 10
    db.stage_pending_playlist(
        {"id": 100, "uuid": "pl-100", "name": "Morning Promo", "version": 10},
        [
            {"id": 1, "media_id": 1, "sort_order": 0, "duration": 5.0},
            {"id": 2, "media_id": 2, "sort_order": 1, "duration": 5.0},
        ],
    )
    db.commit_active_playlist(100)

    # 2. Boot Playback Engine without server connection
    engine = PlaybackEngine()

    with patch("client.app.player.playback_engine.client_db", db), patch(
        "client.app.player.playback_engine.media_cache", manager
    ), patch("client.app.media.cache_manager.client_db", db):
        # Reload active playlist directly from SQLite
        loaded = engine.reload_active_playlist()
        assert loaded is True
        assert engine.current_playlist_version == 10
        assert len(engine.active_items) == 2
        assert engine.active_items[0]["filename"] == "slide1.jpg"
        assert engine.active_items[1]["filename"] == "slide2.jpg"


@pytest.mark.asyncio
async def test_atomic_commit_playlist_version_upgrade(offline_env):
    """
    Test Atomic Commit:
    Active playlist is Version 10.
    Server publishes Version 11.
    All Version 11 media downloads succeed and verify SHA-256.
    Atomic commit triggers and upgrades active playlist to Version 11 seamlessly.
    """
    env = offline_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]

    # 1. Setup Active Playlist Version 10
    hash1 = _create_dummy_media_file(media_dir, "slide1.jpg", b"SLIDE 1")
    db.upsert_media({"server_media_id": 1, "filename": "slide1.jpg", "filesize": 7, "sha256": hash1, "local_path": str(media_dir / "slide1.jpg"), "status": MediaStatus.READY})
    db.stage_pending_playlist({"id": 100, "name": "Promo", "version": 10}, [{"id": 1, "media_id": 1, "sort_order": 0, "duration": 5.0}])
    db.commit_active_playlist(100)

    assert db.get_active_playlist()["version"] == 10

    # 2. Server offers Version 11 with a new media item (server_media_id: 2)
    new_payload = b"NEW PROMO SLIDE FOR VERSION 11"
    new_hash = hashlib.sha256(new_payload).hexdigest().lower()

    server_manifest = {
        "id": 100,
        "uuid": "pl-100",
        "name": "Promo",
        "version": 11,
        "items": [
            {
                "id": 10,
                "media_id": 1,
                "sort_order": 0,
                "duration": 5.0,
                "media": {"id": 1, "uuid": "u1", "filename": "slide1.jpg", "filesize": 7, "sha256": hash1},
            },
            {
                "id": 11,
                "media_id": 2,
                "sort_order": 1,
                "duration": 10.0,
                "media": {"id": 2, "uuid": "u2", "filename": "slide_v11.jpg", "filesize": len(new_payload), "sha256": new_hash},
            },
        ],
    }

    sync = SyncEngine()
    engine = PlaybackEngine()

    with patch("client.app.sync.service.client_db", db), patch(
        "client.app.sync.service.media_cache", manager
    ), patch("client.app.sync.service.signage_api") as mock_api, patch(
        "client.app.player.playback_engine.client_db", db
    ), patch("client.app.player.playback_engine.media_cache", manager), patch(
        "client.app.media.cache_manager.client_db", db
    ), patch("httpx.AsyncClient.stream") as mock_stream:
        # Mock server health and manifest
        mock_api.check_server_health = AsyncMock(return_value=True)
        mock_api.get_assigned_playlist = AsyncMock(return_value=server_manifest)

        # Mock download response for media 2
        mock_stream.return_value.__aenter__.return_value = httpx.Response(
            200, content=new_payload, request=httpx.Request("GET", "http://test")
        )

        # Run sync cycle
        ok = await sync.run_sync_cycle()
        assert ok is True

        # Verify Atomic Commit: Active playlist is now Version 11
        active = db.get_active_playlist()
        assert active["version"] == 11

        # Verify playback engine loaded Version 11
        engine.reload_active_playlist()
        assert engine.current_playlist_version == 11
        assert len(engine.active_items) == 2


@pytest.mark.asyncio
async def test_partial_failure_preserves_active_playlist(offline_env):
    """
    Test Partial Failure Resilience:
    Active playlist is Version 10.
    Server publishes Version 11 with 2 items, but 1 item fails download or fails checksum.
    Active playlist MUST REMAIN Version 10 without disruption.
    """
    env = offline_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]

    # 1. Setup Active Playlist Version 10
    hash1 = _create_dummy_media_file(media_dir, "slide1.jpg", b"SLIDE 1")
    db.upsert_media({"server_media_id": 1, "filename": "slide1.jpg", "filesize": 7, "sha256": hash1, "local_path": str(media_dir / "slide1.jpg"), "status": MediaStatus.READY})
    db.stage_pending_playlist({"id": 100, "name": "Promo", "version": 10}, [{"id": 1, "media_id": 1, "sort_order": 0, "duration": 5.0}])
    db.commit_active_playlist(100)

    assert db.get_active_playlist()["version"] == 10

    # 2. Server offers Version 11 with media item 3 that will fail download (500 error)
    server_manifest = {
        "id": 100,
        "uuid": "pl-100",
        "name": "Promo",
        "version": 11,
        "items": [
            {
                "id": 10,
                "media_id": 1,
                "sort_order": 0,
                "duration": 5.0,
                "media": {"id": 1, "uuid": "u1", "filename": "slide1.jpg", "filesize": 7, "sha256": hash1},
            },
            {
                "id": 12,
                "media_id": 3,
                "sort_order": 1,
                "duration": 10.0,
                "media": {"id": 3, "uuid": "u3", "filename": "failed_media.mp4", "filesize": 5000, "sha256": "badhash"},
            },
        ],
    }

    sync = SyncEngine()
    engine = PlaybackEngine()

    with patch("client.app.sync.service.client_db", db), patch(
        "client.app.sync.service.media_cache", manager
    ), patch("client.app.sync.service.signage_api") as mock_api, patch(
        "client.app.player.playback_engine.client_db", db
    ), patch("client.app.player.playback_engine.media_cache", manager), patch(
        "client.app.media.cache_manager.client_db", db
    ), patch("httpx.AsyncClient.stream") as mock_stream:
        mock_api.check_server_health = AsyncMock(return_value=True)
        mock_api.get_assigned_playlist = AsyncMock(return_value=server_manifest)

        # Mock download failure (500 Internal Server Error)
        mock_stream.return_value.__aenter__.return_value = httpx.Response(
            500, content=b"Server error", request=httpx.Request("GET", "http://test")
        )

        ok = await sync.run_sync_cycle()
        assert ok is False

        # CRITICAL VERIFICATION: Active playlist REMAINS Version 10!
        active = db.get_active_playlist()
        assert active["version"] == 10

        # Playback engine continues running on Version 10
        engine.reload_active_playlist()
        assert engine.current_playlist_version == 10
        assert len(engine.active_items) == 1
        assert engine.active_items[0]["filename"] == "slide1.jpg"


@pytest.mark.asyncio
async def test_server_down_24h_continuous_playback(offline_env):
    """
    Test 24H Offline Continuity:
    Server becomes completely offline (connection refused).
    Sync engine fails gracefully without crash, player NEVER stops,
    and media files & active playlist are NEVER deleted.
    """
    env = offline_env
    db = env["db"]
    manager = env["manager"]
    media_dir = env["media_dir"]

    # Pre-seed active playlist
    h = _create_dummy_media_file(media_dir, "loop_slide.jpg", b"LOOP CONTENT")
    db.upsert_media({"server_media_id": 5, "filename": "loop_slide.jpg", "filesize": 12, "sha256": h, "local_path": str(media_dir / "loop_slide.jpg"), "status": MediaStatus.READY})
    db.stage_pending_playlist({"id": 50, "name": "Offline Loop", "version": 5}, [{"id": 5, "media_id": 5, "sort_order": 0, "duration": 5.0}])
    db.commit_active_playlist(50)

    sync = SyncEngine()
    engine = PlaybackEngine()

    with patch("client.app.sync.service.client_db", db), patch(
        "client.app.sync.service.media_cache", manager
    ), patch("client.app.sync.service.signage_api") as mock_api, patch(
        "client.app.player.playback_engine.client_db", db
    ), patch("client.app.player.playback_engine.media_cache", manager), patch(
        "client.app.media.cache_manager.client_db", db
    ):
        # Server is dead for 5 consecutive sync cycles
        mock_api.check_server_health = AsyncMock(return_value=False)

        for _ in range(5):
            res = await sync.run_sync_cycle()
            assert res is False

        # Active playlist still intact
        active = db.get_active_playlist()
        assert active is not None
        assert active["version"] == 5

        # Playback engine continues running without interruption
        loaded = engine.reload_active_playlist()
        assert loaded is True
        assert len(engine.active_items) == 1
        assert (media_dir / "loop_slide.jpg").exists()
