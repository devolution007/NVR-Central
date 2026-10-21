"""Dahua `NTP.TimeZone` enum -> UTC offset in minutes.

CAUTION: only code 7 (UTC+05:00, "Islamabad") has been confirmed on hardware. The rest follows Dahua's published
GMT list from memory and is used ONLY as a cross-check against the offset measured from the NVR clock, never as
the source of truth. A mismatch produces a warning, not a silent correction.
"""

DAHUA_TZ_OFFSET_MINUTES: dict[int, int] = {
    0: 0, 1: 60, 2: 120, 3: 180, 4: 210, 5: 240, 6: 270, 7: 300, 8: 330, 9: 345, 10: 360, 11: 390, 12: 420,
    13: 480, 14: 540, 15: 570, 16: 600, 17: 660, 18: 720, 19: 780,
    20: -60, 21: -120, 22: -180, 23: -210, 24: -240, 25: -300, 26: -360, 27: -420, 28: -480, 29: -540,
    30: -600, 31: -660, 32: -720,
}  # fmt: skip

VERIFIED_CODES = frozenset({7})


def offset_from_clock(device_local: "object", utc_now: "object") -> int:
    """Round (device local time - real UTC) to the nearest 15 minutes. Tolerates up to 7 minutes of clock drift."""
    from datetime import datetime

    assert isinstance(device_local, datetime) and isinstance(utc_now, datetime)
    delta_minutes = (device_local - utc_now.replace(tzinfo=None)).total_seconds() / 60
    return int(round(delta_minutes / 15.0)) * 15
