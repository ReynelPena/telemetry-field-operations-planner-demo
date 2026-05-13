from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.services.recommendation import has_conflict, rank_installers, row_to_dict

WORKDAY_START_HOUR = 8
WORKDAY_END_HOUR = 18
SLOT_HOURS = [8, 9, 10, 11, 12, 13, 14, 15, 16]
MAX_OPTIONS = 8


@dataclass
class ScheduleCandidate:
    start: datetime
    end: datetime
    installer_id: int
    installer_name: str
    score: float
    reasons: list[str]
    warnings: list[str]
    date_relation: str
    workload_count: int
    route_matches: int


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(str(value)[:16], fmt)
            if fmt == "%Y-%m-%d":
                return datetime.combine(parsed.date(), time(WORKDAY_START_HOUR, 0))
            return parsed
        except (TypeError, ValueError):
            continue
    return None


def fmt_dt(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M") if value else None


def normalize_date_mode(job: dict[str, Any]) -> str:
    mode = str(job.get("requested_date_type") or "exact").strip().lower()
    if mode not in {"exact", "range", "flexible"}:
        return "exact"
    return mode


def requested_window(job: dict[str, Any]) -> dict[str, Any]:
    requested = parse_date(job.get("requested_date")) or date.today()
    requested_end = parse_date(job.get("requested_end_date"))
    mode = normalize_date_mode(job)

    if mode == "range":
        start = requested
        end = requested_end or requested
        if end < start:
            end = start
        search_end = end
        label = f"Customer requested window: {start.isoformat()} to {end.isoformat()}"
    elif mode == "flexible":
        start = requested
        end = requested_end or (start + timedelta(days=21))
        if end < start:
            end = start + timedelta(days=21)
        search_end = end
        label = f"Customer is flexible from {start.isoformat()} through {end.isoformat()}"
    else:
        start = requested
        end = requested
        search_end = requested + timedelta(days=14)
        label = f"Customer requested exact date: {requested.isoformat()}"

    return {
        "mode": mode,
        "requested_date": requested.isoformat(),
        "requested_end_date": end.isoformat(),
        "search_start": start.isoformat(),
        "search_end": search_end.isoformat(),
        "label": label,
    }


def daterange(start: date, end: date) -> list[date]:
    if end < start:
        return [start]
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def workday_end(day: date) -> datetime:
    return datetime.combine(day, time(WORKDAY_END_HOUR, 0))


def load_installer_day_jobs(conn: sqlite3.Connection, installer_id: int, day: date, exclude_job_id: int | None = None) -> list[dict[str, Any]]:
    day_iso = day.isoformat()
    rows = conn.execute(
        """
        SELECT jobs.*, assigned.name AS assigned_installer_name, suggested.name AS suggested_installer_name
        FROM jobs
        LEFT JOIN installers assigned ON assigned.id = jobs.assigned_installer_id
        LEFT JOIN installers suggested ON suggested.id = jobs.suggested_installer_id
        WHERE jobs.status NOT IN ('cancelled', 'completed', 'no_installation_required')
          AND jobs.id != COALESCE(?, -1)
          AND (jobs.assigned_installer_id = ? OR jobs.suggested_installer_id = ?)
          AND (
                substr(COALESCE(jobs.scheduled_start, jobs.suggested_scheduled_start, jobs.requested_date), 1, 10) = ?
             OR substr(COALESCE(jobs.scheduled_end, jobs.suggested_scheduled_end, jobs.requested_date), 1, 10) = ?
          )
        ORDER BY COALESCE(jobs.scheduled_start, jobs.suggested_scheduled_start, jobs.requested_date)
        """,
        (exclude_job_id, installer_id, installer_id, day_iso, day_iso),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def existing_blocks_for_day(conn: sqlite3.Connection, installer_id: int, day: date, exclude_job_id: int | None = None) -> list[tuple[datetime, datetime]]:
    blocks: list[tuple[datetime, datetime]] = []
    for job in load_installer_day_jobs(conn, installer_id, day, exclude_job_id=exclude_job_id):
        start = parse_datetime(job.get("scheduled_start") or job.get("suggested_scheduled_start"))
        end = parse_datetime(job.get("scheduled_end") or job.get("suggested_scheduled_end"))
        if start and end:
            blocks.append((start, end))
    return blocks


def has_block_conflict(blocks: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    return any(start < existing_end and end > existing_start for existing_start, existing_end in blocks)


def count_route_matches(
    conn: sqlite3.Connection,
    installer_id: int,
    day: date,
    site_state: str,
    site_city: str,
    customer_name: str,
    exclude_job_id: int | None = None,
) -> tuple[int, list[str]]:
    reasons: list[str] = []
    matches = 0
    related_days = [day - timedelta(days=1), day, day + timedelta(days=1)]
    seen_job_ids: set[int] = set()
    for related_day in related_days:
        for job in load_installer_day_jobs(conn, installer_id, related_day, exclude_job_id=exclude_job_id):
            job_id = int(job.get("id") or 0)
            if not job_id or job_id in seen_job_ids:
                continue
            seen_job_ids.add(job_id)
            if site_state and str(job.get("site_state") or "").upper() == site_state.upper():
                matches += 1
                if related_day == day:
                    reasons.append(f"Pairs with existing {site_state.upper()} work on the same day.")
                else:
                    reasons.append(f"Pairs with nearby {site_state.upper()} work within one day.")
            if site_city and str(job.get("site_city") or "").strip().lower() == site_city.strip().lower():
                matches += 1
                reasons.append(f"Same-city opportunity in {site_city}.")
            if customer_name and str(job.get("customer_name") or "").strip().lower() == customer_name.strip().lower():
                matches += 1
                reasons.append("Same customer deployment can be coordinated together.")
    return matches, list(dict.fromkeys(reasons))


def find_first_slot_for_day(
    conn: sqlite3.Connection,
    installer_id: int,
    day: date,
    estimated_minutes: int,
    exclude_job_id: int | None = None,
) -> tuple[datetime | None, datetime | None, list[str]]:
    warnings: list[str] = []
    blocks = existing_blocks_for_day(conn, installer_id, day, exclude_job_id=exclude_job_id)
    single_day_minutes = min(max(30, estimated_minutes), (WORKDAY_END_HOUR - WORKDAY_START_HOUR) * 60)
    for hour in SLOT_HOURS:
        start = datetime.combine(day, time(hour, 0))
        end = start + timedelta(minutes=single_day_minutes)
        if end > workday_end(day):
            continue
        if not has_block_conflict(blocks, start, end):
            if estimated_minutes > single_day_minutes:
                warnings.append("Estimated labor may require multiple installers or a multi-day plan; coordinator should validate team size.")
            return start, end, warnings
    return None, None, ["No clean same-day slot found for this installer."]


def date_preference_score(mode: str, candidate_day: date, requested: date, requested_end: date) -> tuple[float, str, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    if mode == "exact":
        delta = (candidate_day - requested).days
        if delta == 0:
            reasons.append("Matches the exact date requested by the customer.")
            return 35.0, "exact requested date", reasons, warnings
        score = max(5.0, 26.0 - abs(delta) * 2.0)
        if delta > 0:
            warnings.append(f"Customer requested {requested.isoformat()}; this is {delta} day(s) later due to availability/routing.")
            return score, f"{delta} day(s) after requested date", reasons, warnings
        warnings.append(f"Before requested date by {abs(delta)} day(s); confirm customer approval.")
        return score - 6.0, f"{abs(delta)} day(s) before requested date", reasons, warnings

    if mode == "range":
        if requested <= candidate_day <= requested_end:
            closeness = (candidate_day - requested).days
            reasons.append("Inside the customer requested date window.")
            return max(22.0, 34.0 - closeness), "inside requested window", reasons, warnings
        warnings.append("Outside the requested window; confirm customer approval.")
        return 5.0, "outside requested window", reasons, warnings

    # flexible
    delta = max(0, (candidate_day - requested).days)
    if candidate_day <= requested_end:
        reasons.append("Inside the customer's flexible scheduling window.")
        return max(18.0, 30.0 - delta * 0.7), "inside flexible window", reasons, warnings
    warnings.append("After the flexible scheduling window; confirm customer approval.")
    return 6.0, "after flexible window", reasons, warnings


def build_schedule_candidate(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    installer_score: dict[str, Any],
    day: date,
    window: dict[str, Any],
    estimated_minutes: int,
) -> ScheduleCandidate | None:
    installer = installer_score.get("installer", {})
    installer_id = int(installer.get("id") or 0)
    if not installer_id:
        return None
    start, end, slot_warnings = find_first_slot_for_day(conn, installer_id, day, estimated_minutes, exclude_job_id=job.get("id"))
    if not start or not end:
        return None

    mode = str(window["mode"])
    requested = parse_date(window.get("requested_date")) or day
    requested_end = parse_date(window.get("requested_end_date")) or requested
    preference_points, date_relation, date_reasons, date_warnings = date_preference_score(mode, day, requested, requested_end)

    site_state = str(job.get("site_state") or "").upper()
    site_city = str(job.get("site_city") or "")
    customer_name = str(job.get("customer_name") or "")
    route_matches, route_reasons = count_route_matches(conn, installer_id, day, site_state, site_city, customer_name, exclude_job_id=job.get("id"))
    workload_count = len(load_installer_day_jobs(conn, installer_id, day, exclude_job_id=job.get("id")))

    score = float(installer_score.get("score") or 0) * 0.45
    score += preference_points
    if workload_count == 0:
        score += 12
        availability_reason = "Installer has no scheduled work that day."
    else:
        score += max(-18, 10 - workload_count * 6)
        availability_reason = f"Installer already has {workload_count} job(s) that day; slot still appears available."
    route_bonus = min(25, route_matches * 8)
    score += route_bonus
    if day.weekday() >= 5:
        score -= 18
        date_warnings.append("Weekend option; confirm site access and technician availability.")
    else:
        score += 4

    reasons = []
    reasons.extend(date_reasons)
    reasons.append(availability_reason)
    reasons.extend(route_reasons[:3])
    reasons.extend(installer_score.get("reasons", [])[:3])
    warnings = []
    warnings.extend(date_warnings)
    warnings.extend(slot_warnings)
    warnings.extend(installer_score.get("warnings", [])[:2])

    # Preserve a conservative fallback conflict check against confirmed assignments.
    if has_conflict(conn, installer_id, start, end):
        return None

    return ScheduleCandidate(
        start=start,
        end=end,
        installer_id=installer_id,
        installer_name=str(installer.get("name") or f"Installer {installer_id}"),
        score=round(score, 2),
        reasons=list(dict.fromkeys(reasons)),
        warnings=list(dict.fromkeys(warnings)),
        date_relation=date_relation,
        workload_count=workload_count,
        route_matches=route_matches,
    )


def recommend_schedule_options(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    recommendation: dict[str, Any] | None = None,
    max_options: int = MAX_OPTIONS,
) -> dict[str, Any]:
    window = requested_window(job)
    start_day = parse_date(window["search_start"]) or date.today()
    end_day = parse_date(window["search_end"]) or start_day
    estimated_minutes = int((recommendation or {}).get("estimated_minutes") or job.get("estimated_minutes") or 120)
    complexity = int((recommendation or {}).get("complexity") or job.get("complexity") or 3)
    quantity = int(job.get("quantity") or 1)
    required_tags = set((recommendation or {}).get("required_skill_tags") or [])
    ranked = (recommendation or {}).get("ranked_installers")
    if not ranked:
        ranked = [score.__dict__ for score in rank_installers(conn, str(job.get("site_state") or ""), str(job.get("requested_date") or ""), complexity, required_tags, quantity)]

    candidates: list[ScheduleCandidate] = []
    # Evaluate the top ranked installers first but keep enough alternatives for routing/availability.
    top_installers = ranked[: min(6, len(ranked))]
    for day in daterange(start_day, end_day):
        for installer_score in top_installers:
            candidate = build_schedule_candidate(conn, job, installer_score, day, window, estimated_minutes)
            if candidate:
                candidates.append(candidate)

    candidates.sort(key=lambda item: item.score, reverse=True)
    selected = candidates[:max_options]
    options = []
    for rank, item in enumerate(selected, start=1):
        options.append(
            {
                "rank": rank,
                "installer_id": item.installer_id,
                "installer_name": item.installer_name,
                "start": fmt_dt(item.start),
                "end": fmt_dt(item.end),
                "date": item.start.date().isoformat(),
                "score": item.score,
                "date_relation": item.date_relation,
                "workload_count": item.workload_count,
                "route_matches": item.route_matches,
                "reasons": item.reasons,
                "warnings": item.warnings,
            }
        )

    best = options[0] if options else None
    return {
        "window": window,
        "options": options,
        "best_option": best,
        "recommended_start": best.get("start") if best else None,
        "recommended_end": best.get("end") if best else None,
        "recommended_installer_id": best.get("installer_id") if best else None,
        "recommended_installer_name": best.get("installer_name") if best else None,
        "reason": build_schedule_reason(best, window, len(options)),
    }


def build_schedule_reason(best: dict[str, Any] | None, window: dict[str, Any], options_count: int) -> str:
    if not best:
        return f"No schedule option found for {window['label']}. Check installer availability, date range, and active technician coverage."
    reasons = best.get("reasons") or []
    first_reason = reasons[0] if reasons else "best combined installer/date/routing score"
    return (
        f"Recommended {best['start']} to {best['end']} with {best['installer_name']} "
        f"because it is the {first_reason.lower()} The system evaluated {options_count} feasible option(s) for {window['label']}."
    )


def schedule_options_to_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False)


def schedule_options_from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {"window": {}, "options": [], "best_option": None}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            parsed.setdefault("options", [])
            return parsed
    except json.JSONDecodeError:
        pass
    return {"window": {}, "options": [], "best_option": None}
