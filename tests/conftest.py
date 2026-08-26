import os
import sys
import tempfile
from pathlib import Path
import pytest

client_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if client_dir not in sys.path:
    sys.path.insert(0, client_dir)


@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    """Fixture providing temporary SQLite db file."""
    return str(tmp_path / "test_client.db")
