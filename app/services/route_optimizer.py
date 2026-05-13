from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from app.database import csv_to_list
from app.services.recommendation import (
    STATE_COORDS,
    build_required_skill_tags,
    find_matching_rules,
    find_vehicle_profile,
    normalize_text,
    rank_installers,
)

CITY_COORDS: dict[tuple[str, str], tuple[float, float]] = {
    ("tx", "dallas"): (32.7767, -96.7970),
    ("tx", "fort worth"): (32.7555, -97.3308),
    ("tx", "arlington"): (32.7357, -97.1081),
    ("tx", "plano"): (33.0198, -96.6989),
    ("tx", "houston"): (29.7604, -95.3698),
    ("tx", "austin"): (30.2672, -97.7431),
    ("tx", "san antonio"): (29.4241, -98.4936),
    ("tx", "el paso"): (31.7619, -106.4850),
    ("ca", "riverside"): (33.9806, -117.3755),
    ("ca", "los angeles"): (34.0522, -118.2437),
    ("ca", "ontario"): (34.0633, -117.6509),
    ("ca", "san bernardino"): (34.1083, -117.2898),
    ("ca", "san diego"): (32.7157, -117.1611),
    ("ca", "sacramento"): (38.5816, -121.4944),
    ("ca", "san jose"): (37.3382, -121.8863),
    ("ca", "fresno"): (36.7378, -119.7871),
    ("ca", "bakersfield"): (35.3733, -119.0187),
    ("fl", "miami"): (25.7617, -80.1918),
    ("fl", "orlando"): (28.5383, -81.3792),
    ("fl", "tampa"): (27.9506, -82.4572),
    ("ga", "atlanta"): (33.7490, -84.3880),
    ("il", "chicago"): (41.8781, -87.6298),
    ("az", "phoenix"): (33.4484, -112.0740),
    ("nv", "las vegas"): (36.1699, -115.1398),
    ("ok", "oklahoma city"): (35.4676, -97.5164),
    ("la", "new orleans"): (29.9511, -90.0715),
    ("nc", "charlotte"): (35.2271, -80.8431),
    ("tn", "nashville"): (36.1627, -86.7816),
}

DEFAULT_COST_PER_MILE = 0.75
DEFAULT_FLIGHT_COST = 450.0
DEFAULT_DRIVE_SPEED_MPH = 45.0
DEFAULT_DAILY_CREW_MINUTES = 420


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    radius_miles = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    hav = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_miles * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))


def coords_for(city: str | None, state: str | None) -> tuple[float, float]:
    state_n = normalize_text(state).upper()
    city_n = normalize_text(city)
    if (state_n.lower(), city_n) in CITY_COORDS:
        return CITY_COORDS[(state_n.lower(), city_n)]
    if state_n in STATE_COORDS:
        return STATE_COORDS[state_n]
    return (39.8283, -98.5795)


def row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def job_location(job: dict[str, Any]) -> tuple[float, float]:
    return coords_for(job.get("site_city"), job.get("site_state"))


def installer_location(installer: dict[str, Any] | None) -> tuple[float, float] | None:
    if not installer:
        return None
    return coords_for(installer.get("home_city"), installer.get("home_state"))


def fetch_planning_jobs(
    conn: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    state: str | None = None,
    customer: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["jobs.status NOT IN ('cancelled', 'completed', 'no_installation_required')"]
    params: list[Any] = []
    if start_date:
        clauses.append("jobs.requested_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("jobs.requested_date <= ?")
        params.append(end_date)
    if state:
        clauses.append("UPPER(jobs.site_state) = ?")
        params.append(state.upper())
    if customer:
        clauses.append("LOWER(jobs.customer_name) LIKE ?")
        params.append(f"%{customer.lower()}%")

    query = f"""
        SELECT jobs.*, installers.name AS installer_name
        FROM jobs
        LEFT JOIN installers ON installers.id = jobs.assigned_installer_id
        WHERE {' AND '.join(clauses)}
        ORDER BY jobs.site_state, jobs.requested_date, jobs.site_city, jobs.customer_name
    """
    rows = conn.execute(query, params).fetchall()
    jobs = [row_to_dict(row) for row in rows]
    for job in jobs:
        lat, lng = job_location(job)
        job["lat"] = round(lat, 6)
        job["lng"] = round(lng, 6)
    return jobs


def enrich_job_for_planning(conn: Any, job: dict[str, Any]) -> dict[str, Any]:
    vehicle = find_vehicle_profile(
        conn,
        str(job.get("vehicle_make", "")),
        str(job.get("vehicle_model", "")),
        str(job.get("fuel_type", "")),
    )
    rules = find_matching_rules(
        conn,
        str(job.get("vehicle_make", "")),
        str(job.get("vehicle_model", "")),
        str(job.get("fuel_type", "")),
        str(job.get("notes", "")),
    )
    job_copy = dict(job)
    required_tags = build_required_skill_tags(vehicle, rules, job_copy)
    job_copy["planning_tags"] = sorted(required_tags)
    job_copy["planning_vehicle_profile"] = vehicle
    job_copy["planning_rules"] = rules[:3]
    return job_copy


def average_coords(jobs: list[dict[str, Any]]) -> tuple[float, float]:
    if not jobs:
        return (39.8283, -98.5795)
    lat = sum(float(job["lat"]) for job in jobs) / len(jobs)
    lng = sum(float(job["lng"]) for job in jobs) / len(jobs)
    return (lat, lng)


def can_join_cluster(job: dict[str, Any], cluster: list[dict[str, Any]], max_radius_miles: float, max_days_apart: int) -> bool:
    if not cluster:
        return True
    if str(job.get("site_state", "")).upper() != str(cluster[0].get("site_state", "")).upper():
        return False

    job_date = str(job.get("requested_date") or "")
    dates = [str(item.get("requested_date") or "") for item in cluster]
    try:
        jd = datetime.strptime(job_date, "%Y-%m-%d").date()
        parsed_dates = [datetime.strptime(value, "%Y-%m-%d").date() for value in dates if value]
        if parsed_dates:
            if min(abs((jd - existing).days) for existing in parsed_dates) > max_days_apart:
                return False
    except ValueError:
        pass

    job_point = (float(job["lat"]), float(job["lng"]))
    min_distance = min(haversine_miles(job_point, (float(item["lat"]), float(item["lng"]))) for item in cluster)
    if min_distance <= max_radius_miles:
        return True

    # Same-customer work in the same state deserves a little more planning tolerance.
    same_customer = normalize_text(job.get("customer_name")) == normalize_text(cluster[0].get("customer_name"))
    return bool(same_customer and min_distance <= max_radius_miles * 1.5)


def cluster_jobs(jobs: list[dict[str, Any]], max_radius_miles: float = 175.0, max_days_apart: int = 7) -> list[list[dict[str, Any]]]:
    sorted_jobs = sorted(jobs, key=lambda item: (item.get("site_state", ""), item.get("requested_date", ""), item.get("site_city", ""), item.get("customer_name", "")))
    clusters: list[list[dict[str, Any]]] = []
    for job in sorted_jobs:
        best_idx: int | None = None
        best_distance = float("inf")
        job_point = (float(job["lat"]), float(job["lng"]))
        for idx, cluster in enumerate(clusters):
            if not can_join_cluster(job, cluster, max_radius_miles=max_radius_miles, max_days_apart=max_days_apart):
                continue
            center = average_coords(cluster)
            distance = haversine_miles(job_point, center)
            if distance < best_distance:
                best_distance = distance
                best_idx = idx
        if best_idx is None:
            clusters.append([job])
        else:
            clusters[best_idx].append(job)
    return clusters


def nearest_neighbor_order(jobs: list[dict[str, Any]], start_point: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    if not jobs:
        return []
    remaining = [dict(job) for job in jobs]
    current = start_point or average_coords(remaining)
    ordered: list[dict[str, Any]] = []
    while remaining:
        next_idx = min(
            range(len(remaining)),
            key=lambda idx: haversine_miles(current, (float(remaining[idx]["lat"]), float(remaining[idx]["lng"]))),
        )
        selected = remaining.pop(next_idx)
        ordered.append(selected)
        current = (float(selected["lat"]), float(selected["lng"]))
    return ordered


def path_miles(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(haversine_miles(points[idx], points[idx + 1]) for idx in range(len(points) - 1))


def estimate_team_size(total_minutes: int, total_units: int, max_difficulty: int, job_count: int) -> int:
    size = max(1, math.ceil(total_minutes / DEFAULT_DAILY_CREW_MINUTES))
    if total_units >= 10:
        size = max(size, 2)
    if total_units >= 16 or total_minutes >= 900:
        size = max(size, 3)
    if max_difficulty >= 5:
        size = max(size, 2)
    if job_count >= 4:
        size = max(size, 2)
    return min(size, 4)


def summarize_date_range(jobs: list[dict[str, Any]]) -> str:
    dates = sorted({str(job.get("requested_date")) for job in jobs if job.get("requested_date")})
    if not dates:
        return "No date"
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} to {dates[-1]}"


def cluster_name(jobs: list[dict[str, Any]]) -> str:
    states = sorted({str(job.get("site_state", "")).upper() for job in jobs if job.get("site_state")})
    cities = sorted({str(job.get("site_city", "")).title() for job in jobs if job.get("site_city")})
    customers = sorted({str(job.get("customer_name", "")) for job in jobs if job.get("customer_name")})
    state_label = ", ".join(states) if states else "US"
    city_label = ", ".join(cities[:3]) + ("..." if len(cities) > 3 else "")
    customer_label = customers[0] if len(customers) == 1 else "Multiple customers"
    return f"{state_label} deployment - {city_label} - {customer_label}"


def build_cluster_plan(
    conn: Any,
    cluster: list[dict[str, Any]],
    group_number: int,
    cost_per_mile: float = DEFAULT_COST_PER_MILE,
    flight_cost: float = DEFAULT_FLIGHT_COST,
) -> dict[str, Any]:
    enriched = [enrich_job_for_planning(conn, job) for job in cluster]
    total_units = sum(int(job.get("quantity") or 1) for job in enriched)
    total_minutes = sum(int(job.get("estimated_minutes") or 0) for job in enriched)
    max_difficulty = max([int(job.get("complexity") or 1) for job in enriched] or [1])
    required_tags: set[str] = set()
    for job in enriched:
        required_tags.update(job.get("planning_tags", []))
    state_counts = defaultdict(int)
    for job in enriched:
        state_counts[str(job.get("site_state", "")).upper()] += 1
    cluster_state = max(state_counts.items(), key=lambda item: item[1])[0] if state_counts else ""
    requested_date = min([str(job.get("requested_date")) for job in enriched if job.get("requested_date")] or [date.today().isoformat()])

    ranked_scores = rank_installers(
        conn=conn,
        site_state=cluster_state,
        requested_date=requested_date,
        complexity=max_difficulty,
        required_tags=required_tags,
        quantity=total_units,
    )
    lead = ranked_scores[0].installer if ranked_scores else None
    lead_point = installer_location(lead)
    ordered_jobs = nearest_neighbor_order(enriched, start_point=lead_point)

    route_points = [(float(job["lat"]), float(job["lng"])) for job in ordered_jobs]
    if lead_point and route_points:
        optimized_miles = path_miles([lead_point] + route_points + [lead_point])
        baseline_miles = sum(path_miles([lead_point, point, lead_point]) for point in route_points)
        one_way_home_to_center = haversine_miles(lead_point, average_coords(ordered_jobs))
    else:
        optimized_miles = path_miles(route_points)
        baseline_miles = 0.0
        one_way_home_to_center = 0.0

    route_only_miles = path_miles(route_points)
    miles_saved = max(0.0, baseline_miles - optimized_miles)
    avoided_trips = max(0, len(ordered_jobs) - 1)
    likely_flight = one_way_home_to_center >= 550
    flight_savings = flight_cost * avoided_trips if likely_flight else 0.0
    mileage_savings = miles_saved * cost_per_mile
    projected_savings = mileage_savings + flight_savings

    team_size = estimate_team_size(total_minutes, total_units, max_difficulty, len(enriched))
    team = [score.installer for score in ranked_scores[:team_size]]
    labor_elapsed_minutes = math.ceil(total_minutes / max(1, team_size))
    travel_minutes = math.ceil((route_only_miles / DEFAULT_DRIVE_SPEED_MPH) * 60) if route_only_miles else 0
    buffer_minutes = max(30, len(ordered_jobs) * 20)
    total_elapsed_minutes = labor_elapsed_minutes + travel_minutes + buffer_minutes
    days_required = max(1, math.ceil(total_elapsed_minutes / DEFAULT_DAILY_CREW_MINUTES))

    reasons = []
    if len(enriched) > 1:
        reasons.append(f"Groups {len(enriched)} nearby jobs into one coordinated deployment.")
    if len({job.get("customer_name") for job in enriched}) == 1:
        reasons.append("Same customer work can be planned as one campaign instead of isolated work orders.")
    if miles_saved > 0:
        reasons.append(f"Avoids about {round(miles_saved)} duplicate travel miles compared with separate trips.")
    if likely_flight and avoided_trips:
        reasons.append(f"Likely long-distance deployment: consolidating can avoid about {avoided_trips} extra flight/trip(s).")
    if max_difficulty >= 4:
        reasons.append("Requires an advanced lead because the cluster includes high-difficulty work.")
    else:
        reasons.append("Difficulty profile can be covered by an installer whose capacity meets the highest job in the cluster.")

    warnings = []
    if len(enriched) == 1:
        warnings.append("Single-job group. Add nearby jobs or increase the planning window to create consolidation opportunities.")
    if not team:
        warnings.append("No active installer is available for this route group.")
    elif int(team[0].get("max_complexity") or 1) < max_difficulty:
        warnings.append("The top ranked installer is below the required difficulty level. Assign an advanced lead manually.")
    if total_elapsed_minutes > DEFAULT_DAILY_CREW_MINUTES and team_size == 1:
        warnings.append("One installer may exceed a practical one-day workload. Consider adding support.")
    blocked_jobs = [job for job in enriched if job.get("status") == "waiting_required_info" or int(job.get("info_completion_percent") or 0) < 100]
    if blocked_jobs:
        warnings.append(f"{len(blocked_jobs)} job(s) in this group still need mandatory information before confirmation.")

    return {
        "group_id": group_number,
        "name": cluster_name(enriched),
        "date_range": summarize_date_range(enriched),
        "state": cluster_state,
        "job_count": len(enriched),
        "total_units": total_units,
        "total_field_minutes": total_minutes,
        "estimated_elapsed_minutes": total_elapsed_minutes,
        "days_required": days_required,
        "max_difficulty": max_difficulty,
        "required_skill_tags": sorted(required_tags),
        "route_order": ordered_jobs,
        "optimized_route_miles": round(optimized_miles, 1),
        "route_only_miles": round(route_only_miles, 1),
        "baseline_separate_trip_miles": round(baseline_miles, 1),
        "miles_saved": round(miles_saved, 1),
        "projected_mileage_savings": round(mileage_savings, 2),
        "projected_flight_savings": round(flight_savings, 2),
        "projected_total_savings": round(projected_savings, 2),
        "likely_flight_required": bool(likely_flight),
        "recommended_team_size": team_size,
        "recommended_team": team,
        "ranked_installers": [score.__dict__ for score in ranked_scores[:5]],
        "reasons": reasons,
        "warnings": warnings,
    }


def build_customer_campaigns(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    campaigns: dict[str, dict[str, Any]] = {}
    for group in groups:
        for job in group["route_order"]:
            customer = str(job.get("customer_name") or "Unknown Customer")
            campaign = campaigns.setdefault(
                customer,
                {
                    "customer_name": customer,
                    "job_count": 0,
                    "total_units": 0,
                    "total_minutes": 0,
                    "max_difficulty": 1,
                    "states": set(),
                    "groups": [],
                },
            )
            campaign["job_count"] += 1
            campaign["total_units"] += int(job.get("quantity") or 1)
            campaign["total_minutes"] += int(job.get("estimated_minutes") or 0)
            campaign["max_difficulty"] = max(campaign["max_difficulty"], int(job.get("complexity") or 1))
            if job.get("site_state"):
                campaign["states"].add(str(job.get("site_state")).upper())
            if group["group_id"] not in campaign["groups"]:
                campaign["groups"].append(group["group_id"])

    result = []
    for campaign in campaigns.values():
        total_minutes = int(campaign["total_minutes"])
        result.append(
            {
                "customer_name": campaign["customer_name"],
                "job_count": campaign["job_count"],
                "total_units": campaign["total_units"],
                "total_hours": round(total_minutes / 60, 1),
                "max_difficulty": campaign["max_difficulty"],
                "states": sorted(campaign["states"]),
                "groups": campaign["groups"],
                "planning_note": campaign_note(campaign),
            }
        )
    result.sort(key=lambda item: (item["total_units"], item["job_count"]), reverse=True)
    return result


def campaign_note(campaign: dict[str, Any]) -> str:
    group_count = len(campaign.get("groups", []))
    states = sorted(campaign.get("states", []))
    if group_count <= 1:
        return "Plan as a single coordinated deployment if site access and customer availability align."
    return f"Split into {group_count} geographic phase(s) across {', '.join(states)} and assign each phase to the best regional team."


def optimize_open_jobs(
    conn: Any,
    start_date: str | None = None,
    end_date: str | None = None,
    state: str | None = None,
    customer: str | None = None,
    max_radius_miles: float = 175.0,
    max_days_apart: int = 7,
    cost_per_mile: float = DEFAULT_COST_PER_MILE,
    flight_cost: float = DEFAULT_FLIGHT_COST,
) -> dict[str, Any]:
    jobs = fetch_planning_jobs(conn, start_date=start_date, end_date=end_date, state=state, customer=customer)
    clusters = cluster_jobs(jobs, max_radius_miles=max_radius_miles, max_days_apart=max_days_apart)
    groups = [
        build_cluster_plan(conn, cluster, idx + 1, cost_per_mile=cost_per_mile, flight_cost=flight_cost)
        for idx, cluster in enumerate(clusters)
    ]
    groups.sort(key=lambda item: (item["projected_total_savings"], item["job_count"], item["total_units"]), reverse=True)
    for idx, group in enumerate(groups, start=1):
        group["group_id"] = idx

    total_projected_savings = sum(float(group["projected_total_savings"]) for group in groups)
    consolidated_groups = sum(1 for group in groups if group["job_count"] > 1)
    return {
        "filters": {
            "start_date": start_date,
            "end_date": end_date,
            "state": state,
            "customer": customer,
            "max_radius_miles": max_radius_miles,
            "max_days_apart": max_days_apart,
            "cost_per_mile": cost_per_mile,
            "flight_cost": flight_cost,
        },
        "summary": {
            "job_count": len(jobs),
            "group_count": len(groups),
            "consolidated_groups": consolidated_groups,
            "projected_total_savings": round(total_projected_savings, 2),
            "total_units": sum(int(job.get("quantity") or 1) for job in jobs),
        },
        "groups": groups,
        "customer_campaigns": build_customer_campaigns(groups),
    }
