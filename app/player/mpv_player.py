import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from app.core.config import client_config
    from app.core.logging import logger
    from app.media.cache_manager import media_cache
except ImportError:
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.media.cache_manager import media_cache


class MediaPlayer:
    """
    Controls mpv media playback subprocess with IPC socket support.
    Provides play_media, stop_media, pause_media, resume_media operations.
    """

    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.current_file: Optional[str] = None
        self.is_paused: bool = False
        self.is_running: bool = False
        self.is_mpv_available = shutil.which("mpv") is not None

        if not self.is_mpv_available:
            logger.warning("mpv executable not found in PATH. Player will run in simulated display mode.")

    def _send_ipc_command(self, command: List[Any]) -> Optional[Dict[str, Any]]:
        """Send JSON IPC command to running mpv process via Unix domain socket / pipe."""
        if not self.is_running or not self.is_mpv_available:
            return None

        ipc_path = client_config.player.ipc_socket
        if not ipc_path or sys.platform == "win32":
            return None

        try:
            if not Path(ipc_path).exists():
                return None

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(ipc_path)

            msg = json.dumps({"command": command}) + "\n"
            sock.sendall(msg.encode("utf-8"))

            response_data = sock.recv(4096).decode("utf-8")
            sock.close()

            for line in response_data.splitlines():
                if line.strip():
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.debug(f"IPC communication error: {e}")

        return None

    def play_cached_media(
        self,
        server_media_id: int,
        duration: Optional[float] = None,
    ) -> bool:
        """
        STRICT ENFORCEMENT:
        Play a media asset from local cache ONLY if its status is 'ready'.
        Rejects pending, downloading, or failed media.
        """
        playable_path = media_cache.get_playable_path(server_media_id)
        if not playable_path:
            logger.error(
                f"Playback rejected: Media asset {server_media_id} is not in READY state in local cache."
            )
            return False

        return self.play_media(str(playable_path), duration=duration)

    def play_media(
        self,
        file_path: str,
        duration: Optional[float] = None,
        is_video: bool = False
    ) -> bool:
        """
        Play an image or video file fullscreen.
        For images, loops infinitely or for specified duration.
        """
        p = Path(file_path)
        if not p.exists():
            logger.error(f"Cannot play media, file not found: {file_path}")
            return False

        # Reject any file from temp directory
        if "temp" in p.parts or p.suffix.lower() == ".tmp":
            logger.error(f"Playback rejected: Cannot play temporary incomplete file: {file_path}")
            return False

        self.current_file = str(p.resolve())
        self.is_paused = False

        if not self.is_mpv_available:
            logger.info(f"[SIMULATION] Playing media: '{p.name}' (duration: {duration}s)")
            self.is_running = True
            return True

        # Stop previous playback if running
        self.stop_media()

        # Build MPV arguments for kiosk display
        cmd = ["mpv"]
        if client_config.player.fullscreen:
            cmd.append("--fullscreen")

        cmd.extend([
            f"--volume={client_config.player.volume}",
            f"--input-ipc-server={client_config.player.ipc_socket}",
        ])
        cmd.extend(client_config.player.mpv_args)

        # For static images, keep displaying without auto-exit
        ext = p.suffix.lower()
        if ext in [".jpg", ".jpeg", ".png", ".webp"]:
            cmd.extend(["--image-display-duration=inf", "--loop-file=inf"])

        cmd.append(str(p.resolve()))

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.is_running = True
            logger.info(f"Launched MPV playback process (PID {self.process.pid}) for: {p.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to launch MPV playback: {e}")
            self.is_running = False
            return False

    def stop_media(self) -> bool:
        """Stop current media playback process."""
        if not self.is_running and self.process is None:
            return True

        logger.info(f"Stopping media playback: {self.current_file or 'None'}")

        # Try IPC quit first
        self._send_ipc_command(["quit"])

        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

        self.process = None
        self.is_running = False
        self.is_paused = False
        self.current_file = None

        # Clean IPC socket file if left behind
        try:
            ipc_file = Path(client_config.player.ipc_socket)
            if ipc_file.exists() and sys.platform != "win32":
                ipc_file.unlink()
        except Exception:
            pass

        return True

    def pause_media(self) -> bool:
        """Pause playback."""
        if not self.is_running:
            logger.warning("Cannot pause media, player is not running.")
            return False

        logger.info(f"Pausing playback: {self.current_file}")
        self.is_paused = True

        if self.is_mpv_available:
            self._send_ipc_command(["set_property", "pause", True])

        return True

    def resume_media(self) -> bool:
        """Resume playback."""
        if not self.is_running:
            logger.warning("Cannot resume media, player is not running.")
            return False

        logger.info(f"Resuming playback: {self.current_file}")
        self.is_paused = False

        if self.is_mpv_available:
            self._send_ipc_command(["set_property", "pause", False])

        return True

    def get_status(self) -> Dict[str, Any]:
        """Get current player playback state."""
        # Poll process if running
        if self.process:
            if self.process.poll() is not None:
                self.is_running = False

        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "current_file": self.current_file,
            "backend": "mpv" if self.is_mpv_available else "simulation",
        }


media_player = MediaPlayer()
