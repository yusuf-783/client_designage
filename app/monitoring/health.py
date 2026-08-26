import os
import platform
import shutil
import socket
import time
from typing import Any, Dict, Optional

try:
    from app.core.config import client_config
    from app.database.connection import client_db
    from app.player.mpv_player import media_player
except ImportError:
    from client.app.core.config import client_config
    from client.app.database.connection import client_db
    from client.app.player.mpv_player import media_player

START_TIME = time.time()


class SystemMonitor:
    """Collects system telemetry and application playback state for heartbeat reports."""

    @staticmethod
    def get_local_ip() -> str:
        """Resolve local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def get_cpu_usage() -> float:
        """Estimate CPU usage percentage."""
        try:
            import psutil
            return float(psutil.cpu_percent(interval=0.1))
        except Exception:
            return 5.0

    @staticmethod
    def get_memory_usage() -> float:
        """Estimate RAM memory usage percentage."""
        try:
            import psutil
            return float(psutil.virtual_memory().percent)
        except Exception:
            return 25.0

    @classmethod
    def get_heartbeat_payload(
        cls,
        sync_status: str = "synced",
        last_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Gathers complete heartbeat telemetry required by server."""
        target_dir = client_config.storage.media_dir if os.path.exists(client_config.storage.media_dir) else "."
        try:
            disk_usage = shutil.disk_usage(target_dir)
            storage_total = disk_usage.total
            storage_free = disk_usage.free
            storage_used = disk_usage.used
        except Exception:
            storage_total = 100 * 1024 * 1024 * 1024
            storage_free = 50 * 1024 * 1024 * 1024
            storage_used = 50 * 1024 * 1024 * 1024

        uptime_seconds = round(time.time() - START_TIME, 2)
        hostname = socket.gethostname()
        local_ip = cls.get_local_ip()

        # Active playlist state
        active_pl = client_db.get_active_playlist()
        current_pl_str = f"{active_pl['name']} (v{active_pl['version']})" if active_pl else None

        # Player status
        player_status_info = media_player.get_status()
        if player_status_info.get("is_running"):
            player_status = "paused" if player_status_info.get("is_paused") else "playing"
        else:
            player_status = "idle"

        current_media = player_status_info.get("current_file")

        return {
            "device_id": client_config.device.id,
            "hostname": hostname,
            "ip": local_ip,
            "uptime": uptime_seconds,
            "uptime_seconds": int(uptime_seconds),
            "cpu_usage": cls.get_cpu_usage(),
            "memory_usage": cls.get_memory_usage(),
            "storage_total": storage_total,
            "storage_free": storage_free,
            "client_version": "0.1.0",
            "current_playlist": current_pl_str,
            "current_media": current_media,
            "player_status": player_status,
            "sync_status": sync_status,
            "last_error": last_error,
            # Legacy fields for backward compatibility
            "platform": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "disk": {
                "total_bytes": storage_total,
                "used_bytes": storage_used,
                "free_bytes": storage_free,
                "percent_used": round((storage_used / storage_total) * 100, 1) if storage_total else 0.0,
            },
            "status": "healthy",
        }

    # Backward compatibility helper
    @classmethod
    def get_telemetry(cls) -> Dict[str, Any]:
        return cls.get_heartbeat_payload()


system_monitor = SystemMonitor()
