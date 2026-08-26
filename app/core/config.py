import os
import sys
from pathlib import Path
from typing import List, Optional
import yaml
from pydantic import BaseModel, Field


class DeviceSettings(BaseModel):
    id: str = Field(default="RPI-LOBBY-01", description="Registered Device ID")
    name: str = Field(default="Main Lobby Display", description="Device Display Name")
    username: str = Field(default="rpi_lobby_01", description="Authentication username")
    password: str = Field(default="StrongPiPassword123!", description="Authentication password")
    location: Optional[str] = Field(default="Building A Floor 1", description="Physical location")
    timezone: str = Field(default="Asia/Jakarta", description="Device local timezone (e.g. 'Asia/Jakarta', 'UTC')")


class ServerSettings(BaseModel):
    base_url: str = Field(default="http://127.0.0.1:8000", description="Backend Server Base URL")
    api_prefix: str = Field(default="/api/v1", description="API Version Prefix")
    heartbeat_interval_seconds: int = Field(default=30, description="Heartbeat Interval")
    sync_interval_seconds: int = Field(default=60, description="Sync Interval")
    timeout_seconds: int = Field(default=10, description="HTTP Request Timeout")
    verify_ssl: bool = Field(default=True, description="Enforce TLS certificate verification")
    ca_bundle: Optional[str] = Field(default=None, description="Optional path to custom CA certificate bundle")


class StorageSettings(BaseModel):
    base_dir: str = Field(default="/var/lib/digitalsignage", description="Root storage directory")
    db_dir: str = Field(default="/var/lib/digitalsignage/db", description="Database directory")
    media_dir: str = Field(default="/var/lib/digitalsignage/media", description="Media cache directory")
    playlists_dir: str = Field(default="/var/lib/digitalsignage/playlists", description="Playlists directory")
    temp_dir: str = Field(default="/var/lib/digitalsignage/temp", description="Temporary staging directory")
    logs_dir: str = Field(default="/var/lib/digitalsignage/logs", description="Logs directory")
    database_file: str = Field(default="/var/lib/digitalsignage/db/client.sqlite", description="SQLite database file")


class PlayerSettings(BaseModel):
    backend: str = Field(default="mpv", description="Player engine")
    fullscreen: bool = Field(default=True, description="Launch in fullscreen mode")
    volume: int = Field(default=100, description="Audio volume percentage")
    ipc_socket: str = Field(default="/tmp/mpv-socket", description="MPV IPC socket path")
    mpv_args: List[str] = Field(
        default_factory=lambda: [
            "--no-osc",
            "--no-osd-bar",
            "--no-input-default-bindings",
            "--loop-file=inf",
            "--hwdec=auto",
            "--cursor-autohide=always",
        ]
    )


class LoggingSettings(BaseModel):
    level: str = Field(default="INFO", description="Log level")
    log_file: Optional[str] = Field(default="/var/lib/digitalsignage/logs/client.log", description="Log file path")


class ClientConfig(BaseModel):
    device: DeviceSettings = Field(default_factory=DeviceSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    player: PlayerSettings = Field(default_factory=PlayerSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


def _resolve_storage_paths(cfg: ClientConfig) -> ClientConfig:
    """Ensure directory paths are accessible or fallback to local ./data folder on Windows/dev."""
    try:
        # Test creating standard Raspberry Pi path
        test_dir = Path(cfg.storage.base_dir)
        test_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        # Fallback to local development directory
        workspace_root = Path(__file__).resolve().parent.parent.parent
        fallback_base = workspace_root / "data" / "digitalsignage"
        fallback_base.mkdir(parents=True, exist_ok=True)

        cfg.storage.base_dir = str(fallback_base)
        cfg.storage.db_dir = str(fallback_base / "db")
        cfg.storage.media_dir = str(fallback_base / "media")
        cfg.storage.playlists_dir = str(fallback_base / "playlists")
        cfg.storage.temp_dir = str(fallback_base / "temp")
        cfg.storage.logs_dir = str(fallback_base / "logs")
        cfg.storage.database_file = str(fallback_base / "db" / "client.sqlite")
        cfg.logging.log_file = str(fallback_base / "logs" / "client.log")
        if sys.platform == "win32":
            cfg.player.ipc_socket = r"\\.\pipe\mpv-pipe"

    # Create all required subdirectories and enforce POSIX 0700 permissions
    for d in [
        cfg.storage.base_dir,
        cfg.storage.db_dir,
        cfg.storage.media_dir,
        cfg.storage.playlists_dir,
        cfg.storage.temp_dir,
        cfg.storage.logs_dir,
    ]:
        try:
            p = Path(d)
            p.mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                os.chmod(p, 0o700)
        except Exception:
            pass

    return cfg


def load_config(config_path: Optional[str] = None) -> ClientConfig:
    """
    Load client configuration following hierarchy:
    1. Explicit path parameter
    2. Environment variable DIGITALSIGNAGE_CONFIG
    3. Production path: /etc/digitalsignage/config.yaml
    4. Local development fallbacks: ./config.yaml, client/config.yaml
    """
    resolved_path: Optional[Path] = None

    if config_path:
        p = Path(config_path)
        if p.exists():
            resolved_path = p
    elif os.getenv("DIGITALSIGNAGE_CONFIG"):
        p = Path(os.getenv("DIGITALSIGNAGE_CONFIG"))
        if p.exists():
            resolved_path = p
    else:
        # Check standard Raspberry Pi location then local fallbacks
        candidates = [
            Path("/etc/digitalsignage/config.yaml"),
            Path("config.yaml"),
            Path("client/config.yaml"),
            Path(__file__).parent.parent.parent / "config.yaml",
            Path(__file__).parent.parent.parent / "config.example.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                resolved_path = candidate
                break

    if resolved_path and resolved_path.exists():
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                cfg = ClientConfig.model_validate(data)
                return _resolve_storage_paths(cfg)
        except Exception:
            pass

    return _resolve_storage_paths(ClientConfig())


client_config = load_config()
