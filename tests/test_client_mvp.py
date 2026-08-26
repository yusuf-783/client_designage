import os
import tempfile
from pathlib import Path
import pytest

from client.app.auth.client_auth import ClientAuthManager
from client.app.core.config import ClientConfig, load_config
from client.app.database.connection import ClientDatabase
from client.app.player.mpv_player import MediaPlayer
from client.app.main import SignageClientApplication


def test_config_loading() -> None:
    """Test loading configuration with fallback and valid fields."""
    config = load_config()
    assert isinstance(config, ClientConfig)
    assert config.device.id is not None
    assert config.server.base_url.startswith("http")
    assert config.storage.database_file.endswith(".sqlite") or config.storage.database_file.endswith(".db")


def test_sqlite_schema_creation() -> None:
    """Test that all 5 required SQLite tables are created properly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = os.path.join(tmpdir, "test_client.sqlite")
        db = ClientDatabase(db_path=test_db_path)

        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}

        required_tables = {"devices", "settings", "media", "playlists", "playlist_items"}
        for table in required_tables:
            assert table in tables, f"Missing required SQLite table: {table}"


def test_settings_key_value_storage() -> None:
    """Test setting and retrieving key-value entries in local SQLite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = os.path.join(tmpdir, "test_settings.sqlite")
        db = ClientDatabase(db_path=test_db_path)

        db.set_setting("sync_token", "abc123xyz")
        assert db.get_setting("sync_token") == "abc123xyz"
        assert db.get_setting("non_existent", "default_val") == "default_val"

        db.delete_setting("sync_token")
        assert db.get_setting("sync_token") is None


def test_device_state_persistence() -> None:
    """Test storing and retrieving device state in SQLite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = os.path.join(tmpdir, "test_device.sqlite")
        db = ClientDatabase(db_path=test_db_path)

        device_data = {
            "id": 1,
            "uuid": "4c94b7f8-95ef-4d1a-9694-82fcf3e6396e",
            "device_id": "RPI-TEST-01",
            "device_name": "Test Screen",
            "status": "active",
            "location": "Room 101",
            "current_playlist_id": 5,
            "current_playlist_version": 2,
        }

        db.save_device_state(device_data)
        saved = db.get_device_state()

        assert saved is not None
        assert saved["device_id"] == "RPI-TEST-01"
        assert saved["device_name"] == "Test Screen"
        assert saved["current_playlist_id"] == 5


def test_mpv_player_controls() -> None:
    """Test MediaPlayer functions: play, pause, resume, and stop."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_img = Path(tmpdir) / "sample_slide.jpg"
        test_img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 30)  # Dummy JPEG header

        player = MediaPlayer()

        # 1. Play Media
        play_ok = player.play_media(str(test_img), duration=10.0)
        assert play_ok is True
        status = player.get_status()
        assert status["is_running"] is True
        assert status["current_file"] == str(test_img.resolve())

        # 2. Pause Media
        pause_ok = player.pause_media()
        assert pause_ok is True
        assert player.get_status()["is_paused"] is True

        # 3. Resume Media
        resume_ok = player.resume_media()
        assert resume_ok is True
        assert player.get_status()["is_paused"] is False

        # 4. Stop Media
        stop_ok = player.stop_media()
        assert stop_ok is True
        assert player.get_status()["is_running"] is False


def test_systemd_service_file_validity() -> None:
    """Test that systemd service file contains required directives for Raspberry Pi."""
    workspace_root = Path(__file__).resolve().parent.parent.parent
    service_path = workspace_root / "client" / "systemd" / "digitalsignage-client.service"

    assert service_path.exists(), f"Service file not found at {service_path}"

    content = service_path.read_text(encoding="utf-8")
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content
    assert "ExecStart=" in content
    assert "Restart=always" in content
    assert "RestartSec=" in content
    assert "WantedBy=graphical.target" in content


@pytest.mark.asyncio
async def test_client_bootstrap_initialization() -> None:
    """Test full client application bootstrap execution."""
    app = SignageClientApplication()
    init_ok = await app.initialize()
    assert init_ok is True
