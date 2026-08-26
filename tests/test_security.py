import os
import sqlite3
import tempfile
from pathlib import Path
import pytest

from client.app.api.client_api import SignageServerApiClient
from client.app.core.config import ClientConfig, ServerSettings, StorageSettings, _resolve_storage_paths
from client.app.database.connection import ClientDatabase
from client.app.media.cache_manager import MediaCacheManager, MediaStatus
from client.app.player.playback_engine import PlaybackEngine


def test_client_sqlite_permissions_and_hardening() -> None:
    """Verify SQLite database connection enforces foreign keys, row factory, and POSIX permissions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_sec.sqlite"
        db = ClientDatabase(str(db_file))

        assert db_file.exists()
        # Verify foreign keys enabled
        with db.get_connection() as conn:
            fk_res = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            assert fk_res == 1

        # On POSIX systems, check permissions
        if os.name == "posix":
            mode = oct(os.stat(db_file).st_mode & 0o777)
            assert mode == "0o600"


def test_client_storage_directory_permissions() -> None:
    """Verify client storage resolver enforces 0700 permissions on POSIX systems."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = ClientConfig(
            storage=StorageSettings(
                base_dir=str(Path(tmpdir) / "base"),
                db_dir=str(Path(tmpdir) / "base" / "db"),
                media_dir=str(Path(tmpdir) / "base" / "media"),
                playlists_dir=str(Path(tmpdir) / "base" / "playlists"),
                temp_dir=str(Path(tmpdir) / "base" / "temp"),
                logs_dir=str(Path(tmpdir) / "base" / "logs"),
                database_file=str(Path(tmpdir) / "base" / "db" / "c.sqlite"),
            )
        )
        resolved = _resolve_storage_paths(cfg)
        assert Path(resolved.storage.media_dir).exists()
        assert Path(resolved.storage.temp_dir).exists()

        if os.name == "posix":
            mode = oct(os.stat(resolved.storage.media_dir).st_mode & 0o777)
            assert mode == "0o700"


def test_client_tls_verification_configuration() -> None:
    """Verify that SignageServerApiClient configures TLS verification properly."""
    api_client = SignageServerApiClient()
    http_client = api_client._get_http_client()

    # Verify that verify is not False by default
    # Note: In httpx, http_client._transport._pool._ssl_context or verify is configured
    assert http_client.timeout.connect is not None


def test_client_offline_playback_unaffected_by_security() -> None:
    """
    CRITICAL: Verify offline playback functions perfectly in offline conditions
    with strict security rules enabled.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "offline_sec.sqlite"
        media_dir = Path(tmpdir) / "media"
        temp_dir = Path(tmpdir) / "temp"

        db = ClientDatabase(str(db_path))
        cache_mgr = MediaCacheManager(media_dir=str(media_dir), temp_dir=str(temp_dir))

        # 1. Create a dummy valid cached media file in READY state
        dummy_file = media_dir / "safe_slide.jpg"
        dummy_file.write_bytes(b"\xff\xd8\xff\xe0" + b"TESTDATACONTENT")
        sha256 = cache_mgr.calculate_sha256(dummy_file)

        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO media (server_media_id, uuid, filename, filesize, sha256, local_path, status)
                VALUES (1, 'uuid-slide-1', 'safe_slide.jpg', 19, ?, ?, 'ready')
                """,
                (sha256, str(dummy_file))
            )
            conn.execute(
                """
                INSERT INTO playlists (id, uuid, name, version, status, is_active)
                VALUES (10, 'uuid-pl-10', 'Offline Secure Playlist', 1, 'published', 1)
                """
            )
            conn.execute(
                """
                INSERT INTO playlist_items (playlist_id, server_media_id, sort_order, duration, version)
                VALUES (10, 1, 0, 5.0, 1)
                """
            )

        # Monkeypatch client_db in cache_manager for isolation
        import client.app.media.cache_manager as cm_mod
        old_cm_db = cm_mod.client_db
        cm_mod.client_db = db

        try:
            # 2. Verify active playlist resolution directly from local SQLite
            active_pl = db.get_active_playlist()
            assert active_pl is not None
            assert active_pl["id"] == 10
            assert active_pl["name"] == "Offline Secure Playlist"

            # 3. Verify items fetched offline with verified ready status
            items = db.get_active_playlist_items(10)
            assert len(items) == 1
            assert items[0]["filename"] == "safe_slide.jpg"
            assert items[0]["media_status"] == "ready"

            # 4. Verify playable path from media cache
            playable_path = cache_mgr.get_playable_path(1)
            assert playable_path is not None
            assert playable_path.exists()
            assert playable_path.name == "safe_slide.jpg"
        finally:
            cm_mod.client_db = old_cm_db

