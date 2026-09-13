"""Presentation-only helpers shared by HTML and plain-text notifications."""

from datetime import datetime


def event_count(count: int, french: bool) -> str:
    if french:
        return f"{count} changement{'s' if count != 1 else ''} détecté{'s' if count != 1 else ''}"
    return f"{count} change{'s' if count != 1 else ''} detected"


def display_time(value: str, french: bool) -> str:
    """Localize wording while preserving the timestamp's explicit time zone."""
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return str(value)
    months = (
        "janvier février mars avril mai juin juillet août septembre octobre novembre décembre"
        if french
        else "January February March April May June July August September October November December"
    ).split()
    date = (
        f"{timestamp.day} {months[timestamp.month - 1]} {timestamp.year} à"
        if french
        else f"{months[timestamp.month - 1]} {timestamp.day}, {timestamp.year} at"
    )
    zone = timestamp.strftime("%z")
    suffix = (" UTC" if zone == "+0000" else f" UTC{zone[:3]}:{zone[3:]}") if zone else ""
    return f"{date} {timestamp:%H:%M}{suffix}"
