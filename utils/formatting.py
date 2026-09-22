"""
Aaruu Music - Formatting Utilities
Handles time formatting, progress bar rendering, and visual representations.
"""


def format_time(seconds: int | float | None) -> str:
    """Converts seconds into M:SS or H:MM:SS string."""
    if seconds is None or seconds < 0:
        return "0:00"

    total_secs = int(round(seconds))
    hours = total_secs // 3600
    minutes = (total_secs % 3600) // 60
    secs = total_secs % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def render_progress(
    current_seconds: int | float | None,
    total_seconds: int | float | None,
    bar_length: int = 16,
) -> str:
    """
    Renders a high-precision unicode progress line.
    Example: 2:25 ━━━━━━━━━━━●━━━━━━ 3:06
    """
    curr = max(0.0, float(current_seconds or 0.0))
    tot = max(0.0, float(total_seconds or 0.0))

    if tot > 0:
        curr = min(curr, tot)

    curr_str = format_time(curr)
    tot_str = format_time(tot)

    if tot <= 0.0 or bar_length <= 1:
        # Fallback for livestream or unknown duration
        bar = "━" * bar_length
        return f"{curr_str} {bar} {tot_str}"

    ratio = min(max(curr / tot, 0.0), 1.0)
    point_index = int(round(ratio * (bar_length - 1)))
    point_index = max(0, min(point_index, bar_length - 1))

    left_bars = "━" * point_index
    right_bars = "━" * (bar_length - 1 - point_index)
    bar = f"{left_bars}●{right_bars}"

    return f"{curr_str} {bar} {tot_str}"


def format_queue_badge(count: int) -> str:
    """Formats the queue button label, e.g. '☷ Queue · 0' or '☷ Queue · 5'."""
    return f"☷ Queue · {max(0, count)}"
