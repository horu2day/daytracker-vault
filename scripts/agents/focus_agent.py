"""
scripts/agents/focus_agent.py - DayTracker Phase 6 Focus Analysis Agent.

Analyzes work patterns over the last N days and produces insights:
  - Peak productive hours (when most file events occur)
  - Most productive day of week
  - Context-switch patterns (rapid project switching)

CLI:
    python scripts/agents/focus_agent.py [--days 30]

Triggered manually or weekly.

Sample output:
    +-------------------------------------------+
    |  Focus Analysis (최근 30일)                |
    +-------------------------------------------+

    집중 시간대
      최고 생산성: 21:00-23:00 (파일 변경 65%)
      평균 작업 시작: 20:30

    요일별 생산성
      토요일 ########  45%
      일요일 ####      20%
      기타   ##        10%

    컨텍스트 전환
      평균 프로젝트 전환: 2.3회/일
      최다 전환일: 2026-02-21 (5회)
"""

from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Windows console UTF-8
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Bootstrap sys.path
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from scripts.config import Config  # type: ignore
    _cfg = Config()
except Exception:
    try:
        from config import Config  # type: ignore
        _cfg = Config()
    except Exception:
        _cfg = None  # type: ignore


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None


def _to_local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone()


def _utc_cutoff(days: int) -> str:
    """Return an ISO UTC timestamp for `days` ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _query_file_events(db_path: str, days: int) -> list[dict]:
    """Fetch file_events from the last N days."""
    db = Path(db_path)
    if not db.exists():
        return []
    cutoff = _utc_cutoff(days)
    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT fe.timestamp, fe.file_path, fe.event_type,
                       p.name AS project_name
                FROM file_events fe
                LEFT JOIN projects p ON fe.project_id = p.id
                WHERE fe.timestamp >= ?
                ORDER BY fe.timestamp ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"[focus_agent] DB error (file_events): {exc}", file=sys.stderr)
        return []


def _query_activity_log(db_path: str, days: int) -> list[dict]:
    """Fetch activity_log rows from the last N days."""
    db = Path(db_path)
    if not db.exists():
        return []
    cutoff = _utc_cutoff(days)
    try:
        with sqlite3.connect(str(db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT al.timestamp, al.event_type, al.app_name, al.summary,
                       p.name AS project_name
                FROM activity_log al
                LEFT JOIN projects p ON al.project_id = p.id
                WHERE al.timestamp >= ?
                ORDER BY al.timestamp ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        print(f"[focus_agent] DB error (activity_log): {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def _analyze_peak_hours(file_rows: list[dict]) -> dict:
    """
    Find peak productive hours by counting file events per hour.

    Returns
    -------
    dict with keys:
        hour_counts (dict hour(int) -> count),
        peak_hour_start (int or None),
        peak_two_hour_block (str or None),   e.g. "21:00-23:00"
        peak_percentage (float),
        avg_work_start_hour (float or None),
    """
    hour_counts: dict[int, int] = defaultdict(int)
    active_days: dict[str, int] = defaultdict(lambda: 25)  # day -> earliest hour

    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt is None:
            continue
        hour = dt.hour
        hour_counts[hour] += 1
        day_str = dt.strftime("%Y-%m-%d")
        if hour < active_days[day_str]:
            active_days[day_str] = hour

    if not hour_counts:
        return {
            "hour_counts": {},
            "peak_hour_start": None,
            "peak_two_hour_block": None,
            "peak_percentage": 0.0,
            "avg_work_start_hour": None,
        }

    total_events = sum(hour_counts.values())

    # Find the best 2-hour contiguous block
    best_block_start = 0
    best_block_count = 0
    for h in range(24):
        block_count = hour_counts.get(h, 0) + hour_counts.get((h + 1) % 24, 0)
        if block_count > best_block_count:
            best_block_count = block_count
            best_block_start = h

    peak_two_hour = (
        f"{best_block_start:02d}:00-{(best_block_start + 2) % 24:02d}:00"
        if total_events > 0
        else None
    )
    peak_percentage = (best_block_count / total_events * 100) if total_events > 0 else 0.0

    # Average work start time
    start_hours = [h for h in active_days.values() if h < 25]
    avg_work_start = sum(start_hours) / len(start_hours) if start_hours else None

    return {
        "hour_counts": dict(hour_counts),
        "peak_hour_start": best_block_start,
        "peak_two_hour_block": peak_two_hour,
        "peak_percentage": round(peak_percentage, 1),
        "avg_work_start_hour": avg_work_start,
    }


def _analyze_day_of_week(file_rows: list[dict]) -> dict:
    """
    Analyze productivity by day of week.

    Returns
    -------
    dict with keys:
        dow_counts (dict weekday_index -> count, 0=Monday),
        total_events (int),
        dow_percentages (dict weekday_index -> float),
        most_productive_dow (int or None),
    """
    DAYS_KR = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

    dow_counts: dict[int, int] = defaultdict(int)
    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt is None:
            continue
        dow_counts[dt.weekday()] += 1

    total = sum(dow_counts.values())
    dow_percentages: dict[int, float] = {}
    if total > 0:
        for dow, count in dow_counts.items():
            dow_percentages[dow] = round(count / total * 100, 1)

    most_productive_dow: Optional[int] = None
    if dow_counts:
        most_productive_dow = max(dow_counts, key=lambda d: dow_counts[d])

    return {
        "dow_counts": dict(dow_counts),
        "total_events": total,
        "dow_percentages": dow_percentages,
        "most_productive_dow": most_productive_dow,
        "day_labels": DAYS_KR,
    }


def _analyze_context_switches(file_rows: list[dict], days: int) -> dict:
    """
    Detect context switches: how often the user switched between projects per day.

    A "switch" occurs when consecutive file events belong to different projects.

    Returns
    -------
    dict with keys:
        day_switch_counts (dict date_str -> int),
        avg_switches_per_day (float),
        max_switch_day (str or None),
        max_switch_count (int),
    """
    # Group events by day, then count project switches in sequence
    day_events: dict[str, list[str]] = defaultdict(list)

    for row in file_rows:
        dt = _to_local(_parse_ts(row.get("timestamp", "")))
        if dt is None:
            continue
        proj = (row.get("project_name") or "unknown").strip() or "unknown"
        day_str = dt.strftime("%Y-%m-%d")
        day_events[day_str].append(proj)

    day_switch_counts: dict[str, int] = {}
    for day_str, projects in day_events.items():
        switches = 0
        prev = None
        for proj in projects:
            if prev is not None and proj != prev:
                switches += 1
            prev = proj
        day_switch_counts[day_str] = switches

    if day_switch_counts:
        avg_switches = sum(day_switch_counts.values()) / len(day_switch_counts)
        max_switch_day = max(day_switch_counts, key=lambda d: day_switch_counts[d])
        max_switch_count = day_switch_counts[max_switch_day]
    else:
        avg_switches = 0.0
        max_switch_day = None
        max_switch_count = 0

    return {
        "day_switch_counts": day_switch_counts,
        "avg_switches_per_day": round(avg_switches, 1),
        "max_switch_day": max_switch_day,
        "max_switch_count": max_switch_count,
    }


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------

def _bar(percentage: float, bar_width: int = 8) -> str:
    """Render a simple ASCII bar for a percentage."""
    filled = int(round(percentage / 100 * bar_width))
    return "#" * filled + " " * (bar_width - filled)


def _format_hour(h: Optional[float]) -> str:
    """Format a float hour (e.g. 20.5) to HH:MM."""
    if h is None:
        return "N/A"
    hour_int = int(h)
    minute_int = int((h - hour_int) * 60)
    return f"{hour_int:02d}:{minute_int:02d}"


def generate_focus_report(
    days: int,
    peak_hours: dict,
    dow_analysis: dict,
    context_switches: dict,
) -> str:
    """Build the formatted focus analysis report."""
    title = f"Focus Analysis (최근 {days}일)"
    box_width = max(len(title) + 6, 48)
    border = "+" + "-" * (box_width - 2) + "+"
    title_line = "|  " + title + " " * (box_width - 4 - len(title)) + "|"

    lines = [
        border,
        title_line,
        border,
        "",
    ]

    # Peak hours section
    lines.append("집중 시간대")
    peak_block = peak_hours.get("peak_two_hour_block")
    peak_pct = peak_hours.get("peak_percentage", 0.0)
    if peak_block:
        lines.append(f"  최고 생산성: {peak_block} (파일 변경 {peak_pct}%)")
    else:
        lines.append("  최고 생산성: (데이터 없음)")

    avg_start = peak_hours.get("avg_work_start_hour")
    lines.append(f"  평균 작업 시작: {_format_hour(avg_start)}")
    lines.append("")

    # Day-of-week section
    DAYS_KR = dow_analysis.get("day_labels", [
        "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"
    ])
    lines.append("요일별 생산성")
    dow_percentages = dow_analysis.get("dow_percentages", {})

    if dow_percentages:
        # Show top 3 days + "기타" catch-all
        sorted_days = sorted(dow_percentages.items(), key=lambda x: x[1], reverse=True)
        shown_count = 0
        others_pct = 0.0
        for dow, pct in sorted_days:
            if shown_count < 3:
                bar = _bar(pct)
                label = DAYS_KR[dow] if dow < len(DAYS_KR) else f"Day{dow}"
                lines.append(f"  {label:<6} {bar}  {pct:.0f}%")
                shown_count += 1
            else:
                others_pct += pct
        if others_pct > 0:
            bar = _bar(others_pct)
            lines.append(f"  기타     {bar}  {others_pct:.0f}%")
    else:
        lines.append("  (데이터 없음)")
    lines.append("")

    # Context switch section
    lines.append("컨텍스트 전환")
    avg_switches = context_switches.get("avg_switches_per_day", 0.0)
    lines.append(f"  평균 프로젝트 전환: {avg_switches:.1f}회/일")

    max_switch_day = context_switches.get("max_switch_day")
    max_switch_count = context_switches.get("max_switch_count", 0)
    if max_switch_day:
        lines.append(f"  최다 전환일: {max_switch_day} ({max_switch_count}회)")
    else:
        lines.append("  최다 전환일: (데이터 없음)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(days: int = 30, config=None) -> None:
    """
    Run the focus analysis agent and print insights to the terminal.

    Parameters
    ----------
    days:
        Number of days to look back.
    config:
        Optional Config instance.
    """
    if config is None:
        config = _cfg

    if config is None:
        print("[focus_agent] ERROR: No config available.", file=sys.stderr)
        return

    try:
        db_path = config.get_db_path()
    except Exception as exc:
        print(f"[focus_agent] ERROR getting db_path: {exc}", file=sys.stderr)
        return

    if not Path(db_path).exists():
        print(
            f"[focus_agent] DB not found at {db_path}. "
            "Run: python scripts/init_db.py",
            file=sys.stderr,
        )
        return

    print(f"[focus_agent] Analyzing last {days} days of activity...")

    file_rows = _query_file_events(db_path, days)
    total_events = len(file_rows)

    if total_events == 0:
        print(f"[focus_agent] No file events found in the last {days} days.")
        print("  Run the watcher daemon to collect data first.")
        return

    peak_hours = _analyze_peak_hours(file_rows)
    dow_analysis = _analyze_day_of_week(file_rows)
    context_switches = _analyze_context_switches(file_rows, days)

    report = generate_focus_report(days, peak_hours, dow_analysis, context_switches)
    print("\n" + report + "\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DayTracker Focus Analysis Agent. Analyzes work patterns and productivity."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        metavar="N",
        help="Number of days to analyze (default: 30).",
    )
    args = parser.parse_args()

    run(days=args.days)


if __name__ == "__main__":
    main()
