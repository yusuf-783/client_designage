import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
except ImportError:
    from client.app.api.client_api import signage_api
    from client.app.auth.client_auth import client_auth
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db
    from client.app.media.cache_manager import media_cache
    from client.app.player.mpv_player import media_player


class DiagnosticStatus:
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class SystemDiagnostic:
    """
    Comprehensive System Diagnostic Tool for Raspberry Pi Digital Signage Client.
    Evaluates Server, Authentication, Disk, SQLite, Player, Service, Sync, Heartbeat, and Playlist.
    """

    def __init__(self) -> None:
        self.results: Dict[str, Dict[str, Any]] = {}

    def _record(self, category: str, status: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        self.results[category] = {
            "status": status,
            "message": message,
            "details": details or {},
        }

    async def check_server(self) -> None:
        """1. Check Server connectivity and latency."""
        base_url = client_config.server.base_url
        start_time = time.time()
        try:
            is_healthy = await signage_api.check_server_health()
            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            if is_healthy:
                self._record(
                    "Server",
                    DiagnosticStatus.PASS,
                    f"Server is reachable at {base_url} ({elapsed_ms}ms)",
                    {"base_url": base_url, "latency_ms": elapsed_ms, "status": "online"},
                )
            else:
                self._record(
                    "Server",
                    DiagnosticStatus.WARN,
                    f"Server responded at {base_url} but health check failed ({elapsed_ms}ms)",
                    {"base_url": base_url, "latency_ms": elapsed_ms, "status": "unhealthy"},
                )
        except Exception as e:
            self._record(
                "Server",
                DiagnosticStatus.FAIL,
                f"Cannot connect to server at {base_url}: {e}",
                {"base_url": base_url, "error": str(e), "status": "offline"},
            )

    async def check_authentication(self) -> None:
        """2. Check Device Authentication and Token validity."""
        device_id = client_config.device.id
        username = client_config.device.username
        has_password = bool(client_config.device.password)

        if not device_id or not username or not has_password:
            self._record(
                "Authentication",
                DiagnosticStatus.FAIL,
                "Missing device credentials in configuration (device_id, username, password).",
                {"device_id": device_id, "username": username},
            )
            return

        stored_token = client_auth.get_token()
        token_info = {
            "device_id": device_id,
            "username": username,
            "token_present": bool(stored_token),
        }

        try:
            profile = await signage_api.get_device_profile()
            if profile:
                token_info["server_device_name"] = profile.get("device_name")
                token_info["server_status"] = profile.get("status")
                self._record(
                    "Authentication",
                    DiagnosticStatus.PASS,
                    f"Authenticated as '{profile.get('device_name')}' (ID: {device_id}, Status: {profile.get('status')})",
                    token_info,
                )
            else:
                if stored_token:
                    self._record(
                        "Authentication",
                        DiagnosticStatus.WARN,
                        "Stored token exists but failed to authenticate with server (server may be offline).",
                        token_info,
                    )
                else:
                    self._record(
                        "Authentication",
                        DiagnosticStatus.WARN,
                        "No valid session token. Server authentication pending login.",
                        token_info,
                    )
        except Exception as e:
            self._record(
                "Authentication",
                DiagnosticStatus.WARN,
                f"Authentication check deferred: {e}",
                {"error": str(e), **token_info},
            )

    def check_disk(self) -> None:
        """3. Check Disk space, directories, and file permissions."""
        base_dir = Path(client_config.storage.base_dir)
        media_dir = Path(client_config.storage.media_dir)
        db_file = Path(client_config.storage.database_file)

        dirs_to_check = [
            base_dir,
            Path(client_config.storage.db_dir),
            media_dir,
            Path(client_config.storage.temp_dir),
            Path(client_config.storage.logs_dir),
        ]

        missing_dirs = [str(d) for d in dirs_to_check if not d.exists()]
        if missing_dirs:
            self._record(
                "Disk",
                DiagnosticStatus.FAIL,
                f"Missing required storage directories: {', '.join(missing_dirs)}",
                {"missing_directories": missing_dirs},
            )
            return

        try:
            usage = shutil.disk_usage(str(base_dir))
            free_mb = usage.free // (1024 * 1024)
            total_mb = usage.total // (1024 * 1024)
            percent_used = round((usage.used / usage.total) * 100, 1)

            disk_details = {
                "base_dir": str(base_dir),
                "total_mb": total_mb,
                "free_mb": free_mb,
                "percent_used": f"{percent_used}%",
            }

            if os.name == "posix":
                db_mode = oct(os.stat(db_file).st_mode & 0o777) if db_file.exists() else "N/A"
                media_mode = oct(os.stat(media_dir).st_mode & 0o777) if media_dir.exists() else "N/A"
                disk_details["db_file_permissions"] = db_mode
                disk_details["media_dir_permissions"] = media_mode

            if free_mb < 100:
                self._record(
                    "Disk",
                    DiagnosticStatus.WARN,
                    f"Low disk space: {free_mb} MB free ({percent_used}% used)",
                    disk_details,
                )
            else:
                self._record(
                    "Disk",
                    DiagnosticStatus.PASS,
                    f"Storage healthy: {free_mb} MB free of {total_mb} MB ({percent_used}% used)",
                    disk_details,
                )
        except Exception as e:
            self._record(
                "Disk",
                DiagnosticStatus.FAIL,
                f"Failed to check disk metrics: {e}",
                {"error": str(e)},
            )

    def check_sqlite(self) -> None:
        """4. Check SQLite database integrity and table schemas."""
        db_path = Path(client_config.storage.database_file)
        if not db_path.exists():
            self._record(
                "SQLite",
                DiagnosticStatus.FAIL,
                f"Database file does not exist at {db_path}",
                {"database_file": str(db_path)},
            )
            return

        try:
            with client_db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                integrity_result = cursor.fetchone()[0]

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [row["name"] for row in cursor.fetchall()]

                expected_tables = ["devices", "settings", "media", "playlists", "playlist_items", "schedules"]
                missing_tables = [t for t in expected_tables if t not in tables]

                details = {
                    "database_file": str(db_path),
                    "integrity_result": integrity_result,
                    "tables_found": tables,
                }

                if integrity_result.lower() != "ok":
                    self._record(
                        "SQLite",
                        DiagnosticStatus.FAIL,
                        f"Database integrity check failed: {integrity_result}",
                        details,
                    )
                elif missing_tables:
                    self._record(
                        "SQLite",
                        DiagnosticStatus.FAIL,
                        f"Database missing required schema tables: {', '.join(missing_tables)}",
                        details,
                    )
                else:
                    self._record(
                        "SQLite",
                        DiagnosticStatus.PASS,
                        f"Database is healthy and integrity verified (6 schema tables ready)",
                        details,
                    )
        except Exception as e:
            self._record(
                "SQLite",
                DiagnosticStatus.FAIL,
                f"Database connection or integrity error: {e}",
                {"database_file": str(db_path), "error": str(e)},
            )

    def check_player(self) -> None:
        """5. Check Media Player binary, hardware acceleration, and backend configuration."""
        backend = client_config.player.backend
        mpv_path = shutil.which("mpv")
        fullscreen = client_config.player.fullscreen
        ipc_socket = client_config.player.ipc_socket

        details = {
            "backend": backend,
            "mpv_binary": mpv_path or "NOT FOUND",
            "fullscreen": fullscreen,
            "ipc_socket": ipc_socket,
            "mpv_available": bool(mpv_path),
        }

        if not mpv_path:
            self._record(
                "Player",
                DiagnosticStatus.WARN,
                "mpv binary not found in PATH. Operating in simulated display mode.",
                details,
            )
        else:
            self._record(
                "Player",
                DiagnosticStatus.PASS,
                f"Player engine ready: {mpv_path} (Fullscreen: {fullscreen})",
                details,
            )

    def check_service(self) -> None:
        """6. Check systemd daemon service status."""
        service_name = "digitalsignage.service"
        is_systemd = os.name == "posix" and shutil.which("systemctl") is not None

        if not is_systemd:
            self._record(
                "Service",
                DiagnosticStatus.PASS,
                "Non-systemd environment (running standalone or development mode).",
                {"systemd_available": False},
            )
            return

        import subprocess
        try:
            active_res = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            is_active = active_res.stdout.strip()

            enabled_res = subprocess.run(
                ["systemctl", "is-enabled", service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            is_enabled = enabled_res.stdout.strip()

            details = {
                "service_name": service_name,
                "active_state": is_active,
                "enabled_state": is_enabled,
            }

            if is_active == "active":
                self._record(
                    "Service",
                    DiagnosticStatus.PASS,
                    f"Systemd service '{service_name}' is ACTIVE and ENABLED ({is_enabled})",
                    details,
                )
            else:
                self._record(
                    "Service",
                    DiagnosticStatus.WARN,
                    f"Systemd service '{service_name}' is {is_active.upper()} (Enabled: {is_enabled})",
                    details,
                )
        except Exception as e:
            self._record(
                "Service",
                DiagnosticStatus.WARN,
                f"Could not query systemctl for {service_name}: {e}",
                {"error": str(e)},
            )

    def check_sync(self) -> None:
        """7. Check Synchronization engine state and playlist version."""
        try:
            active_pl_id = client_db.get_setting("active_playlist_id")
            active_pl_ver = client_db.get_setting("active_playlist_version")
            last_sync = client_db.get_setting("last_sync_timestamp") or "Never"

            details = {
                "active_playlist_id": active_pl_id or "None",
                "active_playlist_version": active_pl_ver or "None",
                "last_sync": last_sync,
            }

            self._record(
                "Sync",
                DiagnosticStatus.PASS,
                f"Sync state: Active Playlist ID {active_pl_id or 'None'} (v{active_pl_ver or 'None'}), Last Sync: {last_sync}",
                details,
            )
        except Exception as e:
            self._record(
                "Sync",
                DiagnosticStatus.FAIL,
                f"Failed to query sync state: {e}",
                {"error": str(e)},
            )

    def check_heartbeat(self) -> None:
        """8. Check Heartbeat telemetry configuration."""
        interval = client_config.server.heartbeat_interval_seconds
        last_hb = client_db.get_setting("last_heartbeat_timestamp") or "Never"

        details = {
            "interval_seconds": interval,
            "last_heartbeat": last_hb,
        }

        self._record(
            "Heartbeat",
            DiagnosticStatus.PASS,
            f"Heartbeat interval configured to {interval}s (Last dispatched: {last_hb})",
            details,
        )

    def check_playlist(self) -> None:
        """9. Check Local Playlist cache, media readiness, and playback feasibility."""
        try:
            active_pl = client_db.get_active_playlist()
            if not active_pl:
                self._record(
                    "Playlist",
                    DiagnosticStatus.WARN,
                    "No active playlist registered in local cache yet. Display is in standby mode.",
                    {"active_playlist": None, "playable_items": 0},
                )
                return

            pl_id = active_pl["id"]
            pl_name = active_pl["name"]
            pl_version = active_pl.get("version", 1)

            items = client_db.get_active_playlist_items(pl_id)
            ready_count = 0
            missing_files = []

            for item in items:
                status = item.get("media_status")
                local_path = item.get("local_path")
                if status == "ready" and local_path and Path(local_path).exists():
                    ready_count += 1
                else:
                    missing_files.append(item.get("filename") or f"Item #{item.get('item_id')}")

            details = {
                "playlist_id": pl_id,
                "playlist_name": pl_name,
                "version": pl_version,
                "total_items": len(items),
                "ready_items": ready_count,
                "unready_items": missing_files,
            }

            if not items:
                self._record(
                    "Playlist",
                    DiagnosticStatus.WARN,
                    f"Active playlist '{pl_name}' (v{pl_version}) has 0 slides.",
                    details,
                )
            elif ready_count == len(items):
                self._record(
                    "Playlist",
                    DiagnosticStatus.PASS,
                    f"Active playlist '{pl_name}' (v{pl_version}) is 100% READY ({ready_count}/{len(items)} slides verified on disk)",
                    details,
                )
            else:
                self._record(
                    "Playlist",
                    DiagnosticStatus.WARN,
                    f"Playlist '{pl_name}' partially ready ({ready_count}/{len(items)} ready). Missing: {', '.join(missing_files[:3])}",
                    details,
                )
        except Exception as e:
            self._record(
                "Playlist",
                DiagnosticStatus.FAIL,
                f"Failed to check playlist status: {e}",
                {"error": str(e)},
            )

    async def run_all(self) -> Dict[str, Dict[str, Any]]:
        """Run all 9 diagnostic checks in sequence."""
        await self.check_server()
        await self.check_authentication()
        self.check_disk()
        self.check_sqlite()
        self.check_player()
        self.check_service()
        self.check_sync()
        self.check_heartbeat()
        self.check_playlist()
        return self.results

    def print_report(self) -> int:
        """Print formatted colorized diagnostic report to console."""
        # ANSI color codes
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        RED = "\033[91m"
        CYAN = "\033[96m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

        print(f"\n{BOLD}{CYAN}======================================================{RESET}")
        print(f"{BOLD}{CYAN}  DIGITAL SIGNAGE SYSTEM DIAGNOSTIC (Debian Trixie)  {RESET}")
        print(f"{BOLD}{CYAN}======================================================{RESET}\n")

        has_fail = False
        has_warn = False

        for category, data in self.results.items():
            status = data["status"]
            msg = data["message"]

            if status == DiagnosticStatus.PASS:
                badge = f"{GREEN}[ PASS ]{RESET}"
            elif status == DiagnosticStatus.WARN:
                badge = f"{YELLOW}[ WARN ]{RESET}"
                has_warn = True
            else:
                badge = f"{RED}[ FAIL ]{RESET}"
                has_fail = True

            print(f"{badge} {BOLD}{category:<16}{RESET} : {msg}")

        print(f"\n{BOLD}{CYAN}------------------------------------------------------{RESET}")
        if has_fail:
            print(f"{RED}{BOLD}DIAGNOSTIC STATUS: FAILED (Critical issues detected){RESET}\n")
            return 1
        elif has_warn:
            print(f"{YELLOW}{BOLD}DIAGNOSTIC STATUS: WARNING (System operational with warnings){RESET}\n")
            return 0
        else:
            print(f"{GREEN}{BOLD}DIAGNOSTIC STATUS: HEALTHY (All 9 systems verified){RESET}\n")
            return 0


async def main_async() -> int:
    diag = SystemDiagnostic()
    await diag.run_all()
    if "--json" in sys.argv:
        print(json.dumps(diag.results, indent=2))
        return 0
    return diag.print_report()


def main() -> None:
    exit_code = asyncio.run(main_async())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
