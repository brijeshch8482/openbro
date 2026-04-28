"""Tests for storage manager."""

from openbro.utils.storage import format_size, get_available_drives


def test_format_size_bytes():
    assert format_size(500) == "500 B"


def test_format_size_kb():
    assert "KB" in format_size(2048)


def test_format_size_mb():
    assert "MB" in format_size(5 * 1024 * 1024)


def test_format_size_gb():
    assert "GB" in format_size(3 * 1024 * 1024 * 1024)


def test_get_available_drives():
    drives = get_available_drives()
    assert len(drives) > 0
    for drive in drives:
        assert "path" in drive
        assert "free_gb" in drive
        assert "total_gb" in drive
        assert drive["free_gb"] >= 0
