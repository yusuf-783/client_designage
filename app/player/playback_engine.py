import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from app.core.config import client_config
    from app.core.logging import logger
    from app.database.connection import client_db
    from app.media.cache_manager import media_cache
    from app.player.mpv_player import media_player
    from app.player.scheduler import schedule_evaluator
except ImportError:
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db
    from client.app.media.cache_manager import media_cache
    from client.app.player.mpv_player import media_player
    from client.app.player.scheduler import schedule_evaluator


class PlaybackEngine:
    """
    Independent Offline-First Playback Engine with Dynamic Timetable Scheduling:
    - Continuously loops active or scheduled playlist from local SQLite.
    - Evaluates timezone-aware timetable rules locally without requiring server connectivity.
    - Switches seamlessly between scheduled playlists (e.g. Morning vs Lunch vs Default).
    - Transitions smoothly when new playlist versions are committed.
    """

    def __init__(self) -> None:
        self.running = False
        self.current_playlist_id: Optional[int] = None
        self.current_playlist_version: Optional[int] = None
        self.current_playlist_name: Optional[str] = None
        self.current_schedule_name: Optional[str] = None
        self.active_items: List[Dict[str, Any]] = []
        self.current_index: int = 0
        self._task: Optional[asyncio.Task] = None

    def reload_active_playlist(self) -> bool:
        """Evaluate schedule rules and fetch playable READY items from SQLite."""
        # 1. Evaluate timetable schedule locally
        active_pl = client_db.get_active_playlist()
        default_pl_id = active_pl.get("id") if active_pl else None

        decision = schedule_evaluator.evaluate_effective_playlist(default_playlist_id=default_pl_id)
        target_pl_id = decision.get("playlist_id") or default_pl_id
        sched_name = decision.get("schedule_name", "Default Fallback")

        if not target_pl_id:
            self.current_playlist_id = None
            self.current_playlist_version = None
            self.current_playlist_name = None
            self.active_items = []
            return False

        # 2. Get playlist metadata & items
        pl_meta = client_db.get_playlist_by_id(target_pl_id) or active_pl
        pl_name = pl_meta.get("name", f"Playlist #{target_pl_id}") if pl_meta else f"Playlist #{target_pl_id}"
        pl_version = pl_meta.get("version", 1) if pl_meta else 1

        raw_items = client_db.get_playlist_items_for_playlist_id(target_pl_id)
        if not raw_items:
            # Fallback to active playlist items if specific target has no items
            raw_items = client_db.get_active_playlist_items(target_pl_id)

        # 3. Filter strictly for READY media with verified local file AND active slide schedule (valid_from <= now <= valid_to)
        now_dt = schedule_evaluator.get_current_time()
        ready_items = []
        for item in raw_items:
            server_media_id = item["server_media_id"]

            # Slide schedule date+time window check
            valid_from_str = item.get("valid_from")
            if valid_from_str:
                try:
                    vf_dt = datetime.fromisoformat(str(valid_from_str))
                    if vf_dt.tzinfo is None:
                        vf_dt = vf_dt.replace(tzinfo=now_dt.tzinfo)
                    if now_dt < vf_dt:
                        logger.debug(f"Playback Engine: Slide {item.get('item_id')} not yet active (starts {valid_from_str}). Skipping.")
                        continue
                except Exception as e:
                    logger.warning(f"Invalid valid_from on item {item.get('item_id')}: {e}")

            valid_to_str = item.get("valid_to")
            if valid_to_str:
                try:
                    vt_dt = datetime.fromisoformat(str(valid_to_str))
                    if vt_dt.tzinfo is None:
                        vt_dt = vt_dt.replace(tzinfo=now_dt.tzinfo)
                    if now_dt > vt_dt:
                        logger.debug(f"Playback Engine: Slide {item.get('item_id')} expired (ended {valid_to_str}). Skipping.")
                        continue
                except Exception as e:
                    logger.warning(f"Invalid valid_to on item {item.get('item_id')}: {e}")

            if media_cache.is_media_ready(server_media_id):
                ready_items.append(item)
            else:
                logger.warning(
                    f"Playback Engine: Item {item.get('item_id')} (Media ID: {server_media_id}) "
                    f"is not in READY state on disk, skipping."
                )

        if not ready_items:
            logger.debug(f"Playlist '{pl_name}' has no active/verified READY items on disk.")
            self.active_items = []
            return False

        pl_changed = (
            self.current_playlist_id != target_pl_id
            or self.current_playlist_version != pl_version
            or self.current_schedule_name != sched_name
        )
        if pl_changed:
            logger.info(
                f"Playback Engine: Active Rule -> [{sched_name}] playing '{pl_name}' "
                f"(ID: {target_pl_id}, Version: {pl_version}, Playable Items: {len(ready_items)})"
            )
            self.current_index = 0

        self.current_playlist_id = target_pl_id
        self.current_playlist_version = pl_version
        self.current_playlist_name = pl_name
        self.current_schedule_name = sched_name
        self.active_items = ready_items
        return True

    async def start(self) -> None:
        """Launch background playback loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self.run_loop())
        logger.info("Offline Playback Engine started.")

    def stop(self) -> None:
        """Stop playback loop and terminate media player."""
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
        media_player.stop_media()
        logger.info("Offline Playback Engine stopped.")

    async def run_loop(self) -> None:
        """
        Continuous playback sequence loop:
        1. Evaluates schedules and reads effective playlist from SQLite.
        2. Plays current item for specified duration.
        3. Advances to next item in sequence.
        4. Loops continuously 24/7 even when server is completely offline.
        """
        logger.info("Entering continuous playback loop...")

        while self.running:
            try:
                self.reload_active_playlist()

                if not self.active_items:
                    # No media ready yet, wait and retry
                    await asyncio.sleep(2)
                    continue

                if self.current_index >= len(self.active_items):
                    self.current_index = 0

                item = self.active_items[self.current_index]
                server_media_id = item["server_media_id"]
                duration = float(item.get("duration") or 10.0)
                filename = item.get("filename", f"media_{server_media_id}")

                logger.info(
                    f"Playing slide [{self.current_index + 1}/{len(self.active_items)}]: "
                    f"'{filename}' (duration: {duration}s)"
                )

                # Play media through player
                played = media_player.play_cached_media(server_media_id, duration=duration)
                if not played:
                    logger.warning(f"Failed to play media {server_media_id}, advancing to next slide.")
                    self.current_index = (self.current_index + 1) % len(self.active_items)
                    await asyncio.sleep(1)
                    continue

                # Sleep for duration with interruptible 0.5s ticks
                elapsed = 0.0
                while elapsed < duration and self.running:
                    await asyncio.sleep(0.5)
                    elapsed += 0.5

                # Advance to next slide
                self.current_index = (self.current_index + 1) % len(self.active_items)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in playback loop: {e}", exc_info=True)
                await asyncio.sleep(2)


playback_engine = PlaybackEngine()
