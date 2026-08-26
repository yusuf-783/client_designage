import json
from typing import Any, Dict, List, Optional

try:
    from app.database.connection import client_db
    from app.core.logging import logger
except ImportError:
    from client.app.database.connection import client_db
    from client.app.core.logging import logger


class PlaylistManager:
    """Manages local playlist persistence and retrieval."""

    def save_playlist(self, playlist_id: str, name: str, items: List[Dict[str, Any]], version: int = 1) -> None:
        """Store or update playlist JSON in local SQLite database."""
        with client_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO playlists (id, name, content_json, version, is_active, updated_at)
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    content_json=excluded.content_json,
                    version=excluded.version,
                    is_active=1,
                    updated_at=CURRENT_TIMESTAMP;
                """,
                (playlist_id, name, json.dumps(items), version),
            )
            logger.info(f"Saved playlist '{name}' (ID: {playlist_id}) locally")

    def get_active_playlist(self) -> Optional[Dict[str, Any]]:
        """Retrieve current active playlist from SQLite."""
        with client_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, name, content_json, version, updated_at
                FROM playlists
                WHERE is_active = 1
                ORDER BY updated_at DESC
                LIMIT 1;
                """
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "name": row["name"],
                    "items": json.loads(row["content_json"]),
                    "version": row["version"],
                    "updated_at": row["updated_at"],
                }
            return None


playlist_manager = PlaylistManager()
