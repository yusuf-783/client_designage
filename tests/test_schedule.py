import os
import tempfile
from datetime import datetime
from pathlib import Path
import pytest
from zoneinfo import ZoneInfo

from client.app.database.connection import ClientDatabase
from client.app.player.scheduler import ScheduleEvaluator


@pytest.fixture
def temp_client_db():
    """Create isolated SQLite database for testing scheduler logic."""
    temp_dir = tempfile.mkdtemp()
    db_file = os.path.join(temp_dir, "test_client.sqlite")
    db = ClientDatabase(db_path=db_file)
    yield db


def test_schedule_evaluator_active_match(temp_client_db: ClientDatabase):
    """Test matching an active timetable schedule within its valid date, time, and weekday window."""
    evaluator = ScheduleEvaluator(timezone_name="UTC")

    # 1. Setup local database state
    schedules = [
        {
            "id": 1,
            "uuid": "sched-1",
            "name": "Morning Promo (08:00 - 12:00)",
            "playlist_id": 101,
            "playlist_name": "Morning Playlist",
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "start_time": "08:00:00",
            "end_time": "12:00:00",
            "days_of_week": "0,1,2,3,4",  # Mon-Fri
            "priority": 5,
            "is_active": True,
        }
    ]
    temp_client_db.save_schedules(schedules)

    # Monkeypatch client_db in scheduler
    import client.app.player.scheduler as sched_module
    old_db = sched_module.client_db
    sched_module.client_db = temp_client_db

    try:
        # Monday 2026-03-02 at 09:30:00 UTC -> inside schedule window
        monday_dt = datetime(2026, 3, 2, 9, 30, 0, tzinfo=ZoneInfo("UTC"))
        decision = evaluator.evaluate_effective_playlist(custom_now=monday_dt, default_playlist_id=999)

        assert decision["source"] == "schedule"
        assert decision["playlist_id"] == 101
        assert decision["schedule_name"] == "Morning Promo (08:00 - 12:00)"
        assert decision["priority"] == 5

    finally:
        sched_module.client_db = old_db


def test_schedule_evaluator_expired_and_fallback(temp_client_db: ClientDatabase):
    """Test that out-of-window, expired, or disabled schedules fall back to default playlist."""
    evaluator = ScheduleEvaluator(timezone_name="UTC")

    # Setup expired schedule (ended 2025)
    schedules = [
        {
            "id": 1,
            "name": "Expired Promo 2025",
            "playlist_id": 201,
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "start_time": "08:00:00",
            "end_time": "18:00:00",
            "days_of_week": "0,1,2,3,4,5,6",
            "priority": 10,
            "is_active": True,
        }
    ]
    temp_client_db.save_schedules(schedules)

    import client.app.player.scheduler as sched_module
    old_db = sched_module.client_db
    sched_module.client_db = temp_client_db

    try:
        # Year 2026 -> schedule is expired
        now_dt = datetime(2026, 6, 1, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
        decision = evaluator.evaluate_effective_playlist(custom_now=now_dt, default_playlist_id=999)

        assert decision["source"] == "default"
        assert decision["playlist_id"] == 999
        assert decision["schedule_id"] is None

    finally:
        sched_module.client_db = old_db


def test_schedule_overlapping_priority_conflict(temp_client_db: ClientDatabase):
    """Test overlapping schedules: higher priority rule must win."""
    evaluator = ScheduleEvaluator(timezone_name="UTC")

    # Setup overlapping schedules:
    # Rule A: All Day (08:00-17:00), Priority 1
    # Rule B: Lunch Flash Promo (12:00-13:00), Priority 10
    schedules = [
        {
            "id": 1,
            "name": "All Day Standard",
            "playlist_id": 100,
            "start_time": "08:00:00",
            "end_time": "17:00:00",
            "days_of_week": "0,1,2,3,4,5,6",
            "priority": 1,
            "is_active": True,
        },
        {
            "id": 2,
            "name": "Lunch Flash Promo",
            "playlist_id": 200,
            "start_time": "12:00:00",
            "end_time": "13:00:00",
            "days_of_week": "0,1,2,3,4,5,6",
            "priority": 10,
            "is_active": True,
        },
    ]
    temp_client_db.save_schedules(schedules)

    import client.app.player.scheduler as sched_module
    old_db = sched_module.client_db
    sched_module.client_db = temp_client_db

    try:
        # At 10:00 -> Only Rule A matches
        dt_10am = datetime(2026, 3, 2, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
        dec_10am = evaluator.evaluate_effective_playlist(custom_now=dt_10am, default_playlist_id=999)
        assert dec_10am["playlist_id"] == 100
        assert dec_10am["schedule_name"] == "All Day Standard"

        # At 12:30 -> Both Rule A and Rule B match; Rule B (Priority 10) must win!
        dt_1230pm = datetime(2026, 3, 2, 12, 30, 0, tzinfo=ZoneInfo("UTC"))
        dec_1230pm = evaluator.evaluate_effective_playlist(custom_now=dt_1230pm, default_playlist_id=999)
        assert dec_1230pm["playlist_id"] == 200
        assert dec_1230pm["schedule_name"] == "Lunch Flash Promo"
        assert dec_1230pm["priority"] == 10

        # At 14:00 -> Rule B ended, returns to Rule A
        dt_2pm = datetime(2026, 3, 2, 14, 0, 0, tzinfo=ZoneInfo("UTC"))
        dec_2pm = evaluator.evaluate_effective_playlist(custom_now=dt_2pm, default_playlist_id=999)
        assert dec_2pm["playlist_id"] == 100

    finally:
        sched_module.client_db = old_db


def test_schedule_days_of_week_filter(temp_client_db: ClientDatabase):
    """Test that schedules only trigger on specified days of week."""
    evaluator = ScheduleEvaluator(timezone_name="UTC")

    # Schedule active ONLY on weekends (Saturday=5, Sunday=6)
    schedules = [
        {
            "id": 1,
            "name": "Weekend Special",
            "playlist_id": 300,
            "start_time": "09:00:00",
            "end_time": "21:00:00",
            "days_of_week": "5,6",
            "priority": 5,
            "is_active": True,
        }
    ]
    temp_client_db.save_schedules(schedules)

    import client.app.player.scheduler as sched_module
    old_db = sched_module.client_db
    sched_module.client_db = temp_client_db

    try:
        # Friday (weekday=4) at 14:00 -> should not match weekend rule
        friday_dt = datetime(2026, 3, 6, 14, 0, 0, tzinfo=ZoneInfo("UTC"))
        dec_fri = evaluator.evaluate_effective_playlist(custom_now=friday_dt, default_playlist_id=999)
        assert dec_fri["source"] == "default"
        assert dec_fri["playlist_id"] == 999

        # Saturday (weekday=5) at 14:00 -> matches weekend rule!
        saturday_dt = datetime(2026, 3, 7, 14, 0, 0, tzinfo=ZoneInfo("UTC"))
        dec_sat = evaluator.evaluate_effective_playlist(custom_now=saturday_dt, default_playlist_id=999)
        assert dec_sat["source"] == "schedule"
        assert dec_sat["playlist_id"] == 300

    finally:
        sched_module.client_db = old_db


def test_schedule_cross_midnight_window(temp_client_db: ClientDatabase):
    """Test overnight / cross-midnight schedule window (e.g. 22:00 to 04:00)."""
    evaluator = ScheduleEvaluator(timezone_name="UTC")

    schedules = [
        {
            "id": 1,
            "name": "Night Lounge",
            "playlist_id": 400,
            "start_time": "22:00:00",
            "end_time": "04:00:00",
            "days_of_week": "0,1,2,3,4,5,6",
            "priority": 5,
            "is_active": True,
        }
    ]
    temp_client_db.save_schedules(schedules)

    import client.app.player.scheduler as sched_module
    old_db = sched_module.client_db
    sched_module.client_db = temp_client_db

    try:
        # 23:30 (before midnight) -> active
        dt_2330 = datetime(2026, 3, 2, 23, 30, 0, tzinfo=ZoneInfo("UTC"))
        dec_2330 = evaluator.evaluate_effective_playlist(custom_now=dt_2330, default_playlist_id=999)
        assert dec_2330["playlist_id"] == 400

        # 02:15 (after midnight) -> active
        dt_0215 = datetime(2026, 3, 3, 2, 15, 0, tzinfo=ZoneInfo("UTC"))
        dec_0215 = evaluator.evaluate_effective_playlist(custom_now=dt_0215, default_playlist_id=999)
        assert dec_0215["playlist_id"] == 400

        # 12:00 (midday) -> not active, falls back to default
        dt_1200 = datetime(2026, 3, 3, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        dec_1200 = evaluator.evaluate_effective_playlist(custom_now=dt_1200, default_playlist_id=999)
        assert dec_1200["playlist_id"] == 999

    finally:
        sched_module.client_db = old_db


def test_schedule_timezone_conversion(temp_client_db: ClientDatabase):
    """Test timetable scheduling under configurable timezones."""
    evaluator_jkt = ScheduleEvaluator(timezone_name="Asia/Jakarta")  # UTC+7

    schedules = [
        {
            "id": 1,
            "name": "Jakarta Morning 08-10",
            "playlist_id": 500,
            "start_time": "08:00:00",
            "end_time": "10:00:00",
            "days_of_week": "0,1,2,3,4,5,6",
            "priority": 5,
            "is_active": True,
        }
    ]
    temp_client_db.save_schedules(schedules)

    import client.app.player.scheduler as sched_module
    old_db = sched_module.client_db
    sched_module.client_db = temp_client_db

    try:
        # UTC 01:30 is 08:30 in Asia/Jakarta -> should match!
        utc_dt = datetime(2026, 3, 2, 1, 30, 0, tzinfo=ZoneInfo("UTC"))
        decision = evaluator_jkt.evaluate_effective_playlist(custom_now=utc_dt, default_playlist_id=999)

        assert decision["source"] == "schedule"
        assert decision["playlist_id"] == 500

    finally:
        sched_module.client_db = old_db


def test_slide_level_schedule_filtering(temp_client_db: ClientDatabase, tmp_path: Path):
    """
    Test that slides with valid_from/valid_to are properly filtered at runtime:
    - Expired slides (valid_to in past) are automatically skipped.
    - Future slides (valid_from in future) are not played yet.
    - Active / permanent slides are included in playback.
    """
    from unittest.mock import MagicMock, patch
    from client.app.media.cache_manager import MediaStatus
    from client.app.player.playback_engine import PlaybackEngine

    # 1. Setup local mock media assets
    media_file_1 = tmp_path / "slide_active.jpg"
    media_file_1.write_bytes(b"ACTIVE")
    media_file_2 = tmp_path / "slide_expired.jpg"
    media_file_2.write_bytes(b"EXPIRED")
    media_file_3 = tmp_path / "slide_future.jpg"
    media_file_3.write_bytes(b"FUTURE")

    temp_client_db.upsert_media({
        "server_media_id": 1,
        "filename": "slide_active.jpg",
        "filesize": 6,
        "sha256": "hash1",
        "local_path": str(media_file_1),
        "status": MediaStatus.READY,
    })
    temp_client_db.upsert_media({
        "server_media_id": 2,
        "filename": "slide_expired.jpg",
        "filesize": 7,
        "sha256": "hash2",
        "local_path": str(media_file_2),
        "status": MediaStatus.READY,
    })
    temp_client_db.upsert_media({
        "server_media_id": 3,
        "filename": "slide_future.jpg",
        "filesize": 6,
        "sha256": "hash3",
        "local_path": str(media_file_3),
        "status": MediaStatus.READY,
    })

    # 2. Stage playlist with 3 slides:
    # Slide 1: Permanent (no valid_from / valid_to)
    # Slide 2: Expired (ended in 2025)
    # Slide 3: Future (starts in 2030)
    items = [
        {"id": 1, "media_id": 1, "sort_order": 0, "duration": 10.0, "valid_from": None, "valid_to": None},
        {"id": 2, "media_id": 2, "sort_order": 1, "duration": 10.0, "valid_from": "2025-01-01T00:00:00Z", "valid_to": "2025-12-31T23:59:59Z"},
        {"id": 3, "media_id": 3, "sort_order": 2, "duration": 10.0, "valid_from": "2030-01-01T00:00:00Z", "valid_to": "2030-12-31T23:59:59Z"},
    ]
    temp_client_db.stage_pending_playlist({"id": 10, "name": "Slide Timetable Test", "version": 1}, items)
    temp_client_db.commit_active_playlist(10)

    # 3. Test playback engine evaluation
    engine = PlaybackEngine()

    mock_cache = MagicMock()
    mock_cache.is_media_ready.return_value = True

    with patch("client.app.player.playback_engine.client_db", temp_client_db), \
         patch("client.app.player.playback_engine.media_cache", mock_cache):

        loaded = engine.reload_active_playlist()
        assert loaded is True
        # Only Slide 1 (active/permanent) should be retained. Slide 2 and 3 must be filtered out!
        assert len(engine.active_items) == 1
        assert engine.active_items[0]["server_media_id"] == 1

