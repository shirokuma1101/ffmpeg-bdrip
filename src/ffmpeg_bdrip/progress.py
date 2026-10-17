"""Parse FFmpeg progress and estimate a conversion's output duration."""

from __future__ import annotations

import math
from argparse import Namespace


def output_duration(data: dict | None, args: Namespace) -> float | None:
    duration = None
    if data is not None:
        info = data.get("format") or {}
        try:
            duration = float(info["duration"])
            start_time = float(info.get("start_time", 0))
            if 0 < start_time < duration:
                duration -= start_time
        except (KeyError, TypeError, ValueError):
            duration = None
    if duration is not None and args.start is not None:
        duration = max(0.0, duration - args.start)
    if args.duration is not None:
        duration = min(duration, args.duration) if duration is not None else args.duration
    return duration if duration is not None and math.isfinite(duration) and duration > 0 else None


def parse_out_time(value: str) -> float | None:
    try:
        hours, minutes, seconds = value.split(":")
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def format_time(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def estimate_remaining(total: float | None, current: float, speed_text: str) -> float | None:
    if total is None or not speed_text.endswith("x"):
        return None
    try:
        speed = float(speed_text[:-1])
    except ValueError:
        return None
    if not math.isfinite(speed) or speed <= 0:
        return None
    return max(0.0, total - current) / speed


def batch_percent(durations: list[float | None], finished: int, current: float = 0) -> float:
    """Overall batch progress; use equal file weights if any duration is unknown."""
    if not durations:
        return 0.0
    finished = min(max(finished, 0), len(durations))
    if all(duration is not None and duration > 0 for duration in durations):
        total = sum(durations)
        elapsed = sum(durations[:finished])
        if finished < len(durations):
            elapsed += min(max(current, 0), durations[finished])
        return min(100.0, elapsed / total * 100)
    fraction = 0.0
    if finished < len(durations) and durations[finished] is not None:
        fraction = min(1.0, max(0.0, current / durations[finished]))
    return (finished + fraction) / len(durations) * 100


def gui_command(command: list[str]) -> list[str]:
    return [command[0], "-progress", "pipe:1", "-stats_period", "0.5", "-nostats", *command[1:]]
