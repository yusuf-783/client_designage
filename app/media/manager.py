import hashlib
import os
from pathlib import Path
from typing import List, Optional

try:
    from app.core.config import client_config
    from app.core.logging import logger
except ImportError:
    from client.app.core.config import client_config
    from client.app.core.logging import logger


class MediaManager:
    """Manages local media cache storage and checksum verification."""

    def __init__(self, media_dir: Optional[str] = None) -> None:
        self.media_dir = Path(media_dir or client_config.storage.media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def get_local_path(self, filename: str) -> Path:
        """Get absolute path for a cached media file."""
        return self.media_dir / filename

    def is_cached(self, filename: str, expected_md5: Optional[str] = None) -> bool:
        """Check if file exists and matches expected checksum."""
        file_path = self.get_local_path(filename)
        if not file_path.exists():
            return False

        if expected_md5:
            return self.calculate_md5(file_path) == expected_md5.lower()

        return True

    @staticmethod
    def calculate_md5(file_path: Path) -> str:
        """Calculate MD5 hash of local file."""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest().lower()

    def list_cached_files(self) -> List[str]:
        """Return list of filenames in cache directory."""
        if not self.media_dir.exists():
            return []
        return [f.name for f in self.media_dir.iterdir() if f.is_file()]


media_manager = MediaManager()
