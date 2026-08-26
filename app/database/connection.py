import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional

try:
    from app.core.config import client_config
    from app.core.logging import logger
except ImportError:
    from client.app.core.config import client_config
    from client.app.core.logging import logger


class ClientDatabase:
    """Manages SQLite database for offline playlist and media cache metadata."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        path_str = db_path or client_config.storage.database_file
        self.db_path = Path(path_str)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._enforce_permissions()
        self.init_schema()

    def _enforce_permissions(self) -> None:
        """Enforce restrictive permissions (0700 dir, 0600 file) on POSIX/Linux systems."""
        if os.name == "posix":
            try:
                if self.db_path.parent.exists():
                    os.chmod(self.db_path.parent, 0o700)
                if self.db_path.exists():
                    os.chmod(self.db_path, 0o600)
            except Exception as e:
                logger.debug(f"SQLite permissions notice: {e}")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding SQLite connection with Row factory and foreign keys enabled."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"SQLite database transaction error: {e}")
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Initialize local SQLite tables for client state, settings, media cache, and playlists."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Devices Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY,
                    uuid TEXT UNIQUE,
                    device_id TEXT UNIQUE NOT NULL,
                    device_name TEXT NOT NULL,
                    status TEXT DEFAULT 'active',
                    location TEXT,
                    last_seen TIMESTAMP,
                    last_ip TEXT,
                    client_version TEXT,
                    current_playlist_id INTEGER,
                    current_playlist_version INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            cursor.execute("PRAGMA table_info(devices);")
            existing_dev_cols = {row["name"] for row in cursor.fetchall()}
            for col_name, col_def in [
                ("last_seen", "TIMESTAMP"),
                ("last_ip", "TEXT"),
                ("client_version", "TEXT"),
                ("current_playlist_id", "INTEGER"),
                ("current_playlist_version", "INTEGER"),
            ]:
                if col_name not in existing_dev_cols:
                    try:
                        cursor.execute(f"ALTER TABLE devices ADD COLUMN {col_name} {col_def};")
                    except Exception:
                        pass

            # 2. Settings Key-Value Table (stores auth tokens, active version)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 3. Local Media Files Cache Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_media_id INTEGER UNIQUE NOT NULL,
                    uuid TEXT UNIQUE,
                    filename TEXT NOT NULL,
                    original_filename TEXT,
                    filesize INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    local_path TEXT,
                    mime_type TEXT,
                    duration REAL,
                    width INTEGER,
                    height INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    downloaded_at TIMESTAMP,
                    error_message TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # Ensure columns exist if table was initialized with old schema
            cursor.execute("PRAGMA table_info(media);")
            existing_media_cols = {row["name"] for row in cursor.fetchall()}
            if "server_media_id" not in existing_media_cols:
                cursor.execute("DROP TABLE IF EXISTS media;")
                cursor.execute(
                    """
                    CREATE TABLE media (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_media_id INTEGER UNIQUE NOT NULL,
                        uuid TEXT UNIQUE,
                        filename TEXT NOT NULL,
                        original_filename TEXT,
                        filesize INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        local_path TEXT,
                        mime_type TEXT,
                        duration REAL,
                        width INTEGER,
                        height INTEGER,
                        status TEXT NOT NULL DEFAULT 'pending',
                        downloaded_at TIMESTAMP,
                        error_message TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
            else:
                if "error_message" not in existing_media_cols:
                    try:
                        cursor.execute("ALTER TABLE media ADD COLUMN error_message TEXT;")
                    except Exception:
                        pass
                if "downloaded_at" not in existing_media_cols:
                    try:
                        cursor.execute("ALTER TABLE media ADD COLUMN downloaded_at TIMESTAMP;")
                    except Exception:
                        pass

            # 4. Playlists Table (with is_active and pending_version for Staging)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY,
                    uuid TEXT UNIQUE,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    pending_version INTEGER,
                    is_active INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'published',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            cursor.execute("PRAGMA table_info(playlists);")
            existing_pl_cols = {row["name"] for row in cursor.fetchall()}
            if "is_active" not in existing_pl_cols:
                try:
                    cursor.execute("ALTER TABLE playlists ADD COLUMN is_active INTEGER DEFAULT 0;")
                except Exception:
                    pass
            if "pending_version" not in existing_pl_cols:
                try:
                    cursor.execute("ALTER TABLE playlists ADD COLUMN pending_version INTEGER;")
                except Exception:
                    pass

            # 5. Playlist Items Table (with versioning per item)
            cursor.execute("PRAGMA table_info(playlist_items);")
            existing_pi_cols = {row["name"] for row in cursor.fetchall()}

            if not existing_pi_cols:
                cursor.execute(
                    """
                    CREATE TABLE playlist_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        playlist_id INTEGER NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        server_media_id INTEGER NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        duration REAL NOT NULL DEFAULT 10.0,
                        valid_from TEXT,
                        valid_to TEXT,
                        configuration TEXT DEFAULT '{}',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
                    );
                    """
                )
            elif "media_id" in existing_pi_cols or "server_media_id" not in existing_pi_cols:
                # Recreate table cleanly to eliminate old NOT NULL media_id constraint
                cursor.execute("DROP TABLE IF EXISTS playlist_items;")
                cursor.execute(
                    """
                    CREATE TABLE playlist_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        playlist_id INTEGER NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        server_media_id INTEGER NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        duration REAL NOT NULL DEFAULT 10.0,
                        valid_from TEXT,
                        valid_to TEXT,
                        configuration TEXT DEFAULT '{}',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
                    );
                    """
                )
            else:
                if "version" not in existing_pi_cols:
                    try:
                        cursor.execute("ALTER TABLE playlist_items ADD COLUMN version INTEGER DEFAULT 1;")
                    except Exception:
                        pass
                if "duration" not in existing_pi_cols:
                    try:
                        cursor.execute("ALTER TABLE playlist_items ADD COLUMN duration REAL DEFAULT 10.0;")
                    except Exception:
                        pass
                if "valid_from" not in existing_pi_cols:
                    try:
                        cursor.execute("ALTER TABLE playlist_items ADD COLUMN valid_from TEXT;")
                    except Exception:
                        pass
                if "valid_to" not in existing_pi_cols:
                    try:
                        cursor.execute("ALTER TABLE playlist_items ADD COLUMN valid_to TEXT;")
                    except Exception:
                        pass
                if "configuration" not in existing_pi_cols:
                    try:
                        cursor.execute("ALTER TABLE playlist_items ADD COLUMN configuration TEXT DEFAULT '{}';")
                    except Exception:
                        pass

            # 6. Timetable Schedules Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_schedule_id INTEGER UNIQUE NOT NULL,
                    uuid TEXT UNIQUE,
                    name TEXT NOT NULL,
                    playlist_id INTEGER NOT NULL,
                    playlist_name TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    days_of_week TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
                    priority INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            logger.debug(f"SQLite schema initialized successfully at {self.db_path}")

        self._enforce_permissions()

    def set_setting(self, key: str, value: str) -> None:
        """Store or update a setting entry."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (key, value),
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve setting value by key."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def delete_setting(self, key: str) -> None:
        """Remove setting entry."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))

    # --- Device State Helpers ---

    def save_device_state(self, device_data: Dict[str, Any]) -> None:
        """Upsert device profile metadata into local SQLite."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO devices (
                    id, uuid, device_id, device_name, status, location,
                    last_seen, last_ip, client_version, current_playlist_id,
                    current_playlist_version, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(device_id) DO UPDATE SET
                    id = excluded.id,
                    uuid = excluded.uuid,
                    device_name = excluded.device_name,
                    status = excluded.status,
                    location = excluded.location,
                    last_seen = excluded.last_seen,
                    last_ip = excluded.last_ip,
                    client_version = excluded.client_version,
                    current_playlist_id = excluded.current_playlist_id,
                    current_playlist_version = excluded.current_playlist_version,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (
                    device_data.get("id"),
                    device_data.get("uuid"),
                    device_data.get("device_id"),
                    device_data.get("device_name", "Raspberry Pi Client"),
                    device_data.get("status", "active"),
                    device_data.get("location"),
                    device_data.get("last_seen"),
                    device_data.get("last_ip"),
                    device_data.get("client_version"),
                    device_data.get("current_playlist_id"),
                    device_data.get("current_playlist_version"),
                ),
            )

    def get_device_state(self) -> Optional[Dict[str, Any]]:
        """Fetch latest cached device record."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM devices LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None

    # --- Media Cache SQLite Helpers ---

    def upsert_media(self, media_dict: Dict[str, Any]) -> None:
        """Register or update media metadata received from server."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO media (
                    server_media_id, uuid, filename, original_filename,
                    filesize, sha256, local_path, mime_type, duration,
                    width, height, status, downloaded_at, error_message, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server_media_id) DO UPDATE SET
                    uuid = excluded.uuid,
                    filename = excluded.filename,
                    original_filename = excluded.original_filename,
                    filesize = excluded.filesize,
                    sha256 = excluded.sha256,
                    mime_type = excluded.mime_type,
                    duration = excluded.duration,
                    width = excluded.width,
                    height = excluded.height,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (
                    media_dict.get("server_media_id") or media_dict.get("id"),
                    media_dict.get("uuid"),
                    media_dict.get("filename"),
                    media_dict.get("original_filename"),
                    media_dict.get("filesize", 0),
                    media_dict.get("sha256", "").lower(),
                    media_dict.get("local_path"),
                    media_dict.get("mime_type"),
                    media_dict.get("duration"),
                    media_dict.get("width"),
                    media_dict.get("height"),
                    media_dict.get("status", "pending"),
                    media_dict.get("downloaded_at"),
                    media_dict.get("error_message"),
                ),
            )

    def get_media_by_server_id(self, server_media_id: int) -> Optional[Dict[str, Any]]:
        """Fetch media record by server_media_id."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media WHERE server_media_id = ?", (server_media_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_media_status(
        self,
        server_media_id: int,
        status: str,
        local_path: Optional[str] = None,
        error_message: Optional[str] = None,
        downloaded_at: Optional[str] = None,
    ) -> None:
        """Update media download lifecycle status (pending, downloading, ready, failed)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE media
                SET status = ?,
                    local_path = COALESCE(?, local_path),
                    error_message = ?,
                    downloaded_at = COALESCE(?, downloaded_at),
                    updated_at = CURRENT_TIMESTAMP
                WHERE server_media_id = ?;
                """,
                (status, local_path, error_message, downloaded_at, server_media_id),
            )

    def get_all_media(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all cached media records, optionally filtered by status."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT * FROM media WHERE status = ? ORDER BY id ASC", (status,))
            else:
                cursor.execute("SELECT * FROM media ORDER BY id ASC")
            return [dict(row) for row in cursor.fetchall()]

    def reset_downloading_to_pending(self) -> int:
        """Reset any interrupted 'downloading' states back to 'pending' after restart/crash."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE media
                SET status = 'pending',
                    error_message = 'Interrupted during previous session',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'downloading';
                """
            )
            return cursor.rowcount

    # --- Playlist Staging & Atomic Commit Helpers ---

    def stage_pending_playlist(
        self,
        playlist_data: Dict[str, Any],
        items_data: List[Dict[str, Any]],
    ) -> None:
        """
        Stage a playlist version in SQLite without modifying current active playback version.
        If playlist exists and is active, keeps active version unchanged and stores pending_version.
        """
        playlist_id = playlist_data.get("id")
        uuid_str = playlist_data.get("uuid") or f"pl-uuid-{playlist_id}"
        name = playlist_data.get("name", "Playlist")
        new_version = playlist_data.get("version", 1)
        pl_status = playlist_data.get("status", "published")

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Check existing record
            cursor.execute("SELECT id, version, is_active FROM playlists WHERE id = ?", (playlist_id,))
            existing = cursor.fetchone()

            if existing:
                if existing["is_active"] == 1:
                    # Active playlist already running: DO NOT overwrite active version, set pending_version
                    active_version = existing["version"]
                    cursor.execute(
                        """
                        UPDATE playlists
                        SET uuid = ?,
                            name = ?,
                            pending_version = ?,
                            status = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                        """,
                        (uuid_str, name, new_version, pl_status, playlist_id),
                    )
                else:
                    # Not active: update pending version
                    cursor.execute(
                        """
                        UPDATE playlists
                        SET uuid = ?,
                            name = ?,
                            version = ?,
                            pending_version = ?,
                            status = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?;
                        """,
                        (uuid_str, name, new_version, new_version, pl_status, playlist_id),
                    )
            else:
                # Brand new playlist, initial stage as inactive
                cursor.execute(
                    """
                    INSERT INTO playlists (id, uuid, name, version, pending_version, is_active, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, CURRENT_TIMESTAMP);
                    """,
                    (playlist_id, uuid_str, name, new_version, new_version, pl_status),
                )

            # Insert staged playlist items tagged with their specific version
            cursor.execute(
                "DELETE FROM playlist_items WHERE playlist_id = ? AND version = ?;",
                (playlist_id, new_version),
            )
            for item in items_data:
                media_id = item.get("media_id") or (item.get("media", {}).get("id") if isinstance(item.get("media"), dict) else None)
                if not media_id and "server_media_id" in item:
                    media_id = item["server_media_id"]

                sort_order = item.get("sort_order", 0)
                duration = item.get("duration", 10.0)
                valid_from = item.get("valid_from")
                valid_to = item.get("valid_to")
                config_str = json.dumps(item.get("configuration", {})) if isinstance(item.get("configuration"), dict) else str(item.get("configuration", "{}"))

                cursor.execute(
                    """
                    INSERT INTO playlist_items (playlist_id, version, server_media_id, sort_order, duration, valid_from, valid_to, configuration, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                    """,
                    (playlist_id, new_version, media_id, sort_order, duration, valid_from, valid_to, config_str),
                )

    def commit_active_playlist(self, playlist_id: int, version: Optional[int] = None) -> None:
        """
        ATOMIC COMMIT:
        Atomically switch active playlist to the specified playlist_id and version in a single SQLite transaction.
        Sets is_active = 0 for all other playlists, updates target version, sets is_active = 1,
        and saves active_playlist_id / active_playlist_version to settings.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Verify target playlist exists
            cursor.execute("SELECT id, version, pending_version, name FROM playlists WHERE id = ?", (playlist_id,))
            pl = cursor.fetchone()
            if not pl:
                raise ValueError(f"Cannot commit active playlist: Playlist ID {playlist_id} does not exist.")

            target_version = version or pl["pending_version"] or pl["version"]
            name = pl["name"]

            # Atomic switch
            cursor.execute("UPDATE playlists SET is_active = 0 WHERE id != ?", (playlist_id,))
            cursor.execute(
                """
                UPDATE playlists
                SET is_active = 1,
                    version = ?,
                    pending_version = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (target_version, playlist_id),
            )

            # Clean up older item versions
            cursor.execute(
                "DELETE FROM playlist_items WHERE playlist_id = ? AND version < ?;",
                (playlist_id, target_version),
            )

            # Update settings
            cursor.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES ('active_playlist_id', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP;
                """,
                (str(playlist_id),),
            )
            cursor.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES ('active_playlist_version', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP;
                """,
                (str(target_version),),
            )

            logger.info(f"ATOMIC COMMIT SUCCESS: Active playlist switched to '{name}' (ID: {playlist_id}, Version: {target_version})")

    def get_active_playlist(self) -> Optional[Dict[str, Any]]:
        """Retrieve current active playlist metadata."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM playlists
                WHERE is_active = 1
                ORDER BY updated_at DESC
                LIMIT 1;
                """
            )
            row = cursor.fetchone()
            if not row:
                # Fallback to any latest playlist if none explicitly active
                cursor.execute("SELECT * FROM playlists ORDER BY version DESC LIMIT 1;")
                row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_playlist_items(self, playlist_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieve ordered playlist items for active version joined with local media status for playback.
        """
        active_pl = self.get_active_playlist()
        if not active_pl:
            return []

        target_id = playlist_id or active_pl["id"]
        target_version = active_pl.get("version", 1)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    pi.id AS item_id,
                    pi.playlist_id,
                    pi.version,
                    pi.server_media_id,
                    pi.sort_order,
                    pi.duration,
                    pi.valid_from,
                    pi.valid_to,
                    pi.configuration,
                    m.filename,
                    m.local_path,
                    m.sha256,
                    m.status AS media_status,
                    m.mime_type
                FROM playlist_items pi
                LEFT JOIN media m ON pi.server_media_id = m.server_media_id
                WHERE pi.playlist_id = ? AND pi.version = ?
                ORDER BY pi.sort_order ASC, pi.id ASC;
                """,
                (target_id, target_version),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_playlist_by_id(self, playlist_id: int) -> Optional[Dict[str, Any]]:
        """Get playlist by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # --- Timetable Schedules Helpers ---

    def save_schedules(self, schedules_data: List[Dict[str, Any]]) -> None:
        """Store or update active schedule rules from server sync in a single transaction."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM schedules;")
            for s in schedules_data:
                cursor.execute(
                    """
                    INSERT INTO schedules (
                        server_schedule_id, uuid, name, playlist_id, playlist_name,
                        start_date, end_date, start_time, end_time, days_of_week,
                        priority, is_active, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                    """,
                    (
                        s.get("id") or s.get("server_schedule_id"),
                        s.get("uuid"),
                        s.get("name", "Untitled Schedule"),
                        s.get("playlist_id"),
                        s.get("playlist_name"),
                        s.get("start_date"),
                        s.get("end_date"),
                        s.get("start_time"),
                        s.get("end_time"),
                        s.get("days_of_week", "0,1,2,3,4,5,6"),
                        s.get("priority", 0),
                        1 if s.get("is_active", True) else 0,
                    ),
                )

    def get_active_schedules(self) -> List[Dict[str, Any]]:
        """Retrieve all active schedule rules ordered by priority descending."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM schedules
                WHERE is_active = 1
                ORDER BY priority DESC, start_time ASC;
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_playlist_items_for_playlist_id(self, playlist_id: int) -> List[Dict[str, Any]]:
        """
        Retrieve ordered playlist items for a specific playlist ID joined with local media cache.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Find latest version for this playlist
            cursor.execute("SELECT version FROM playlists WHERE id = ?;", (playlist_id,))
            pl = cursor.fetchone()
            version = pl["version"] if pl else 1

            cursor.execute(
                """
                SELECT
                    pi.id AS item_id,
                    pi.playlist_id,
                    pi.version,
                    pi.server_media_id,
                    pi.sort_order,
                    pi.duration,
                    pi.valid_from,
                    pi.valid_to,
                    pi.configuration,
                    m.filename,
                    m.local_path,
                    m.sha256,
                    m.status AS media_status,
                    m.mime_type
                FROM playlist_items pi
                LEFT JOIN media m ON pi.server_media_id = m.server_media_id
                WHERE pi.playlist_id = ? AND pi.version = ?
                ORDER BY pi.sort_order ASC, pi.id ASC;
                """,
                (playlist_id, version),
            )
            return [dict(row) for row in cursor.fetchall()]


client_db = ClientDatabase()
