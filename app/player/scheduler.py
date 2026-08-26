from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from app.core.config import client_config
    from app.core.logging import logger
    from app.database.connection import client_db
except ImportError:
    from client.app.core.config import client_config
    from client.app.core.logging import logger
    from client.app.database.connection import client_db


class ScheduleEvaluator:
    """
    Offline-First Timetable Schedule Engine:
    Evaluates local SQLite schedule rules against current clock & configurable timezone.
    Resolves overlaps using priority and falls back to default playlist when no schedule matches.
    """

    def __init__(self, timezone_name: Optional[str] = None) -> None:
        self.tz_name = timezone_name or client_config.device.timezone or "Asia/Jakarta"
        try:
            self.tz = ZoneInfo(self.tz_name)
        except Exception as e:
            logger.warning(f"Timezone '{self.tz_name}' fallback to UTC: {e}")
            self.tz_name = "UTC"
            try:
                self.tz = ZoneInfo("UTC")
            except Exception:
                self.tz = timezone.utc

    def set_timezone(self, tz_name: str) -> None:
        """Update active timezone dynamically."""
        try:
            self.tz = ZoneInfo(tz_name)
            self.tz_name = tz_name
        except Exception as e:
            logger.error(f"Failed to set timezone to '{tz_name}': {e}")

    def get_current_time(self, custom_now: Optional[datetime] = None) -> datetime:
        """Get timezone-aware datetime."""
        if custom_now:
            if custom_now.tzinfo is None:
                return custom_now.replace(tzinfo=self.tz)
            return custom_now.astimezone(self.tz)
        return datetime.now(self.tz)

    def is_schedule_active(self, schedule: Dict[str, Any], current_dt: datetime) -> bool:
        """
        Evaluate if a schedule rule matches current datetime.
        Checks:
        1. Date range (start_date <= current_date <= end_date)
        2. Day of week (0=Mon, 6=Sun)
        3. Time window (start_time <= current_time < end_time, supporting cross-midnight)
        """
        if not schedule.get("is_active", True):
            return False

        cur_date = current_dt.date()
        cur_time = current_dt.time()
        cur_weekday = str(current_dt.weekday())

        # 1. Date Range
        start_date_str = schedule.get("start_date")
        if start_date_str:
            try:
                start_d = date.fromisoformat(str(start_date_str)[:10])
                if cur_date < start_d:
                    return False
            except ValueError:
                pass

        end_date_str = schedule.get("end_date")
        if end_date_str:
            try:
                end_d = date.fromisoformat(str(end_date_str)[:10])
                if cur_date > end_d:
                    return False
            except ValueError:
                pass

        # 2. Day of Week
        days_str = str(schedule.get("days_of_week", "0,1,2,3,4,5,6"))
        allowed_days = [d.strip() for d in days_str.split(",") if d.strip()]
        if allowed_days and cur_weekday not in allowed_days:
            return False

        # 3. Time Window
        start_t_str = str(schedule.get("start_time", "00:00:00"))
        end_t_str = str(schedule.get("end_time", "23:59:59"))

        try:
            start_t = time.fromisoformat(start_t_str)
            end_t = time.fromisoformat(end_t_str)
        except ValueError:
            return False

        if start_t <= end_t:
            # Standard window (e.g. 08:00 - 12:00)
            return start_t <= cur_time < end_t
        else:
            # Cross-midnight window (e.g. 22:00 - 04:00)
            return cur_time >= start_t or cur_time < end_t

    def evaluate_effective_playlist(
        self,
        custom_now: Optional[datetime] = None,
        default_playlist_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate all local SQLite schedule rules and return the winning playlist.
        Conflict resolution: Highest priority wins. If tied, highest ID wins.
        Fallback: If no schedule matches, returns default fallback playlist.
        """
        now_dt = self.get_current_time(custom_now)
        schedules = client_db.get_active_schedules()

        matching_schedules = []
        for s in schedules:
            if self.is_schedule_active(s, now_dt):
                matching_schedules.append(s)

        if matching_schedules:
            # Sort by priority descending, then ID descending
            matching_schedules.sort(
                key=lambda s: (int(s.get("priority", 0)), int(s.get("id", 0))),
                reverse=True,
            )
            winning_schedule = matching_schedules[0]
            logger.debug(
                f"Schedule Match: '{winning_schedule.get('name')}' (Playlist ID: {winning_schedule['playlist_id']}, Priority: {winning_schedule.get('priority')})"
            )
            return {
                "source": "schedule",
                "schedule_id": winning_schedule.get("id"),
                "schedule_name": winning_schedule.get("name"),
                "playlist_id": winning_schedule["playlist_id"],
                "priority": winning_schedule.get("priority", 0),
                "timestamp": now_dt.isoformat(),
            }

        # Fallback to default playlist
        fallback_id = default_playlist_id
        if fallback_id is None:
            active_pl = client_db.get_active_playlist()
            if active_pl:
                fallback_id = active_pl["id"]

        return {
            "source": "default",
            "schedule_id": None,
            "schedule_name": "Default Fallback",
            "playlist_id": fallback_id,
            "priority": -1,
            "timestamp": now_dt.isoformat(),
        }


schedule_evaluator = ScheduleEvaluator()
