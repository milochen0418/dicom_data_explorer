"""Human-readable numbers for the UI (sizes are decimal, like IDC's series_size_MB)."""

from __future__ import annotations


def size_mb(mb: float | int | None) -> str:
    mb = float(mb or 0)
    if mb <= 0:
        return "—"
    if mb < 1:
        return f"{mb * 1000:.0f} KB"
    if mb < 10:
        return f"{mb:.1f} MB"
    if mb < 1000:
        return f"{mb:.0f} MB"
    if mb < 1e6:
        return f"{mb / 1000:.1f} GB"
    return f"{mb / 1e6:.1f} TB"


def size_bytes(n: int | float | None) -> str:
    return size_mb(float(n or 0) / 1e6)


def speed(bytes_per_s: float) -> str:
    if bytes_per_s <= 0:
        return ""
    return f"{size_bytes(bytes_per_s)}/s"


def duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{max(seconds, 1)} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def count(n: int) -> str:
    return f"{int(n):,}"
