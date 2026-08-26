import pytest
from client.app.diagnostic import DiagnosticStatus, SystemDiagnostic


@pytest.mark.asyncio
async def test_system_diagnostic_run_all() -> None:
    """Verify SystemDiagnostic runs all 9 check categories and records status."""
    diag = SystemDiagnostic()
    results = await diag.run_all()

    required_categories = [
        "Server",
        "Authentication",
        "Disk",
        "SQLite",
        "Player",
        "Service",
        "Sync",
        "Heartbeat",
        "Playlist",
    ]

    for cat in required_categories:
        assert cat in results, f"Missing diagnostic category: {cat}"
        assert "status" in results[cat]
        assert results[cat]["status"] in [DiagnosticStatus.PASS, DiagnosticStatus.WARN, DiagnosticStatus.FAIL]
        assert "message" in results[cat]
        assert "details" in results[cat]


def test_system_diagnostic_print_report() -> None:
    """Verify print_report output code returns 0 when healthy or warnings."""
    diag = SystemDiagnostic()
    diag._record("Server", DiagnosticStatus.PASS, "Server healthy")
    diag._record("Disk", DiagnosticStatus.PASS, "Disk healthy")
    diag._record("Player", DiagnosticStatus.WARN, "Simulated mode")

    exit_code = diag.print_report()
    assert exit_code == 0

    # Add a failure and verify exit code is 1
    diag._record("SQLite", DiagnosticStatus.FAIL, "Corrupt DB")
    exit_code_fail = diag.print_report()
    assert exit_code_fail == 1
