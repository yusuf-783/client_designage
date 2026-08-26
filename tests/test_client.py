import os
import tempfile
from pathlib import Path
import pytest

from app.core.config import ClientConfig, load_config
from app.database.connection import ClientDatabase
from app.media.manager import MediaManager
from app.monitoring.health import SystemMonitor
from app.playlist.manager import PlaylistManager


def test_client_config_defaults() -> None:
    """Test client config initializes with valid defaults."""
    config = load_config()
    assert isinstance(config, ClientConfig)
    assert config.device.id is not None
    assert config.server.base_url.startswith("http")
    assert config.player.backend == "mpv"


def test_sqlite_database_schema(temp_db_path: str) -> None:
    """Test SQLite initialization and schema creation."""
    db = ClientDatabase(db_path=temp_db_path)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row["name"] for row in cursor.fetchall()]
        assert "devices" in tables
        assert "settings" in tables
        assert "media" in tables
        assert "playlists" in tables
        assert "playlist_items" in tables


def test_media_manager_md5(tmp_path: Path) -> None:
    """Test media manager checksum verification."""
    manager = MediaManager(media_dir=str(tmp_path))
    test_file = tmp_path / "sample.txt"
    test_file.write_text("Hello Digital Signage", encoding="utf-8")

    md5_hash = manager.calculate_md5(test_file)
    assert len(md5_hash) == 32
    assert manager.is_cached("sample.txt", expected_md5=md5_hash) is True
    assert manager.is_cached("nonexistent.mp4") is False


def test_system_monitoring_telemetry() -> None:
    """Test telemetry generation contains expected keys."""
    telemetry = SystemMonitor.get_telemetry()
    assert "platform" in telemetry
    assert "disk" in telemetry
    assert "uptime_seconds" in telemetry
    assert telemetry["status"] == "healthy"
