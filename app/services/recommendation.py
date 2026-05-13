from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.database import csv_to_list, list_to_csv

STATE_COORDS = {
    "AL": (32.37, -86.30), "AK": (58.30, -134.42), "AZ": (33.45, -112.07), "AR": (34.75, -92.29),
    "CA": (38.58, -121.49), "CO": (39.74, -104.99), "CT": (41.76, -72.68), "DE": (39.16, -75.52),
    "FL": (30.44, -84.28), "GA": (33.75, -84.39), "HI": (21.31, -157.86), "IA": (41.59, -93.62),
    "ID": (43.62, -116.20), "IL": (39.78, -89.64), "IN": (39.77, -86.16), "KS": (39.05, -95.68),
    "KY": (38.20, -84.87), "LA": (30.45, -91.19), "MA": (42.36, -71.06), "MD": (38.98, -76.49),
    "ME": (44.31, -69.78), "MI": (42.73, -84.55), "MN": (44.95, -93.09), "MO": (38.58, -92.17),
    "MS": (32.30, -90.18), "MT": (46.59, -112.04), "NC": (35.78, -78.64), "ND": (46.81, -100.78),
    "NE": (40.81, -96.68), "NH": (43.21, -71.54), "NJ": (40.22, -74.76), "NM": (35.69, -105.94),
    "NV": (39.16, -119.77), "NY": (42.65, -73.75), "OH": (39.96, -83.00), "OK": (35.47, -97.52),
    "OR": (44.94, -123.03), "PA": (40.27, -76.89), "RI": (41.82, -71.41), "SC": (34.00, -81.03),
    "SD": (44.37, -100.35), "TN": (36.16, -86.78), "TX": (30.27, -97.74), "UT": (40.76, -111.89),
    "VA": (37.54, -77.43), "VT": (44.26, -72.58), "WA": (47.04, -122.89), "WI": (43.07, -89.40),
    "WV": (38.35, -81.63), "WY": (41.14, -104.82),
}

STOPWORDS = {
    "the", "and", "or", "in", "on", "for", "with", "without", "a", "an",
    "installation", "install", "vehicle", "truck", "work", "job", "bring", "need", "should",
}


@dataclass
class InstallerScore:
    installer: dict[str, Any]
    score: float
    reasons: list[str]
    warnings: list[str]


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.lower().strip()
    replacements = {
        "\u00e1": "a", "\u00e9": "e", "\u00ed": "i", "\u00f3": "o", "\u00fa": "u", "\u00f1": "n",
        "\u00c1": "a", "\u00c9": "e", "\u00cd": "i", "\u00d3": "o", "\u00da": "u", "\u00d1": "n",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def tokenize(value: str | None) -> set[str]:
    normalized = normalize_text(value)
    words = set(re.findall(r"[a-z0-9][a-z0-9\-]{1,}", normalized))
    return {word for word in words if word not in STOPWORDS}


def split_csv_tokens(value: str | None) -> set[str]:
    tokens: set[str] = set()
    for item in csv_to_list(value):
        tokens.update(tokenize(item))
        phrase = normalize_text(item)
        if phrase and phrase not in STOPWORDS:
            tokens.add(phrase)
    return tokens


def miles_between_states(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    a = a.strip().upper()
    b = b.strip().upper()
    if a not in STATE_COORDS or b not in STATE_COORDS:
        return None
    lat1, lon1 = STATE_COORDS[a]
    lat2, lon2 = STATE_COORDS[b]
    radius_miles = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    hav = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_miles * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def find_vehicle_profile(conn: sqlite3.Connection, make: str, model: str, fuel_type: str) -> dict[str, Any] | None:
    make_n = normalize_text(make)
    model_n = normalize_text(model)
    fuel_n = normalize_text(fuel_type)
    rows = conn.execute("SELECT * FROM vehicles").fetchall()
    best: tuple[int, sqlite3.Row] | None = None
    for row in rows:
        score = 0
        r_make = normalize_text(row["make"])
        r_model = normalize_text(row["model"])
        r_fuel = normalize_text(row["fuel_type"])
        if make_n and make_n == r_make:
            score += 5
        elif make_n and (make_n in r_make or r_make in make_n):
            score += 3
        if model_n and (model_n == r_model or model_n in r_model or r_model in model_n):
            score += 5
        elif tokenize(model) & tokenize(row["model"]):
            score += 2
        if fuel_n and fuel_n == r_fuel:
            score += 3
        elif fuel_n and (fuel_n in r_fuel or r_fuel in fuel_n):
            score += 2
        if score > 0 and (best is None or score > best[0]):
            best = (score, row)
    if best and best[0] >= 4:
        return row_to_dict(best[1])
    return None


def find_matching_rules(conn: sqlite3.Connection, make: str, model: str, fuel_type: str, extra_text: str = "") -> list[dict[str, Any]]:
    make_n = normalize_text(make)
    model_n = normalize_text(model)
    fuel_n = normalize_text(fuel_type)
    combined = " ".join([make or "", model or "", fuel_type or "", extra_text or ""])
    combined_tokens = tokenize(combined)
    matches: list[tuple[int, dict[str, Any]]] = []

    for row in conn.execute("SELECT * FROM knowledge_rules WHERE active = 1").fetchall():
        rule = row_to_dict(row) or {}
        score = 0
        r_make = normalize_text(rule.get("vehicle_make"))
        r_model = normalize_text(rule.get("vehicle_model"))
        r_fuel = normalize_text(rule.get("fuel_type"))
        if r_make and make_n and (r_make == make_n or r_make in make_n or make_n in r_make):
            score += 5
        if r_model and model_n and (r_model == model_n or r_model in model_n or model_n in r_model):
            score += 5
        if r_fuel and fuel_n and (r_fuel == fuel_n or r_fuel in fuel_n or fuel_n in r_fuel):
            score += 4
        trigger_tokens = split_csv_tokens(rule.get("triggers"))
        overlap = combined_tokens & trigger_tokens
        score += min(8, len(overlap) * 2)
        title_overlap = tokenize(rule.get("title")) & combined_tokens
        score += min(4, len(title_overlap))
        if score > 0:
            rule["match_score"] = score
            rule["matched_terms"] = ", ".join(sorted((overlap | title_overlap)))
            matches.append((score, rule))

    matches.sort(key=lambda item: item[0], reverse=True)
    return [rule for _, rule in matches]


def dedupe_join(values: list[str]) -> str:
    parts: list[str] = []
    for value in values:
        for item in csv_to_list(value):
            if item not in parts:
                parts.append(item)
    return list_to_csv(parts)


def paragraph_join(values: list[str]) -> str:
    clean = []
    for value in values:
        if value and value.strip() and value.strip() not in clean:
            clean.append(value.strip())
    return "\n".join(clean)


def build_required_skill_tags(vehicle: dict[str, Any] | None, rules: list[dict[str, Any]], job: dict[str, Any]) -> set[str]:
    fields = [job.get("vehicle_make", ""), job.get("vehicle_model", ""), job.get("fuel_type", ""), job.get("installation_type", "")]
    if vehicle:
        fields.append(vehicle.get("skill_tags", ""))
    for rule in rules:
        fields.extend([rule.get("triggers", ""), rule.get("title", "")])
    tags: set[str] = set()
    for field in fields:
        tags.update(split_csv_tokens(field))
        tags.update(tokenize(field))
    return {tag for tag in tags if tag not in STOPWORDS}


def recommend_for_job(conn: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any]:
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

    quantity = int(job.get("quantity") or 1)
    base_complexity = int(vehicle["complexity"]) if vehicle else 3
    base_minutes = int(vehicle["estimated_minutes"]) if vehicle else 120
    complexity = base_complexity + sum(int(rule.get("complexity_adjustment") or 0) for rule in rules)
    if quantity >= 4:
        complexity += 1
    complexity = max(1, min(5, complexity))
    estimated_minutes = max(45, base_minutes * max(1, quantity))

    tools = []
    materials = []
    checklists = []
    risks = []
    if vehicle and vehicle.get("notes"):
        risks.append(f"Vehicle profile: {vehicle['notes']}")
    for rule in rules:
        tools.append(rule.get("required_tools", ""))
        materials.append(rule.get("required_materials", ""))
        checklists.append(rule.get("checklist", ""))
        risks.append(rule.get("risk_notes", ""))

    required_tags = build_required_skill_tags(vehicle, rules, job)
    ranked = rank_installers(
        conn=conn,
        site_state=str(job.get("site_state", "")),
        requested_date=str(job.get("requested_date", "")),
        complexity=complexity,
        required_tags=required_tags,
        quantity=quantity,
    )

    return {
        "vehicle_profile": vehicle,
        "matching_rules": rules,
        "complexity": complexity,
        "estimated_minutes": estimated_minutes,
        "required_tools": dedupe_join(tools),
        "required_materials": dedupe_join(materials),
        "checklist": paragraph_join(checklists),
        "risk_notes": paragraph_join(risks),
        "required_skill_tags": sorted(required_tags),
        "ranked_installers": [score.__dict__ for score in ranked],
    }


def installer_workload(conn: sqlite3.Connection, installer_id: int, requested_date: str) -> int:
    if not requested_date:
        return 0
    return conn.execute(
        """
        SELECT COUNT(*) FROM jobs
        WHERE assigned_installer_id = ?
          AND requested_date = ?
          AND status NOT IN ('cancelled', 'completed')
        """,
        (installer_id, requested_date),
    ).fetchone()[0]


def rank_installers(
    conn: sqlite3.Connection,
    site_state: str,
    requested_date: str,
    complexity: int,
    required_tags: set[str],
    quantity: int = 1,
) -> list[InstallerScore]:
    rows = conn.execute("SELECT * FROM installers WHERE active = 1").fetchall()
    scores: list[InstallerScore] = []
    site_state_u = (site_state or "").strip().upper()

    for row in rows:
        installer = row_to_dict(row) or {}
        score = 0.0
        reasons: list[str] = []
        warnings: list[str] = []
        home_state = str(installer.get("home_state") or "").strip().upper()
        coverage_states = {state.strip().upper() for state in csv_to_list(installer.get("coverage_states"))}
        skills = split_csv_tokens(installer.get("skill_tags"))
        workload = installer_workload(conn, int(installer["id"]), requested_date)

        if site_state_u and site_state_u == home_state:
            score += 35
            reasons.append(f"Primary base in {site_state_u}.")
        elif site_state_u and site_state_u in coverage_states:
            score += 28
            reasons.append(f"Covers state {site_state_u}.")
        elif site_state_u:
            score -= 12
            warnings.append(f"Not listed as primary coverage for {site_state_u}.")

        miles = miles_between_states(home_state, site_state_u)
        if miles is not None:
            distance_points = max(-18, 18 - (miles / 90))
            score += distance_points
            if miles <= 250:
                reasons.append(f"Favorable approximate state distance ({int(miles)} mi).")
            elif miles >= 900:
                warnings.append(f"High approximate state distance ({int(miles)} mi).")

        max_complexity = int(installer.get("max_complexity") or 1)
        if max_complexity >= complexity:
            score += 18 + (max_complexity - complexity) * 3
            reasons.append(f"Can handle difficulty {complexity}/5.")
        else:
            score -= 40 + (complexity - max_complexity) * 8
            warnings.append(f"Installer max is {max_complexity}/5, below job difficulty {complexity}/5.")

        skill_overlap = required_tags & skills
        if skill_overlap:
            skill_points = min(35, len(skill_overlap) * 7)
            score += skill_points
            shown = ", ".join(sorted(list(skill_overlap))[:5])
            reasons.append(f"Skill match: {shown}.")
        else:
            score -= 5
            warnings.append("No strong technical skill matches.")

        if quantity > 1 and max_complexity >= 4:
            score += 5
            reasons.append("Strong fit for multi-unit jobs.")

        if workload == 0:
            score += 10
            reasons.append("No jobs assigned that day.")
        else:
            penalty = min(28, workload * 9)
            score -= penalty
            warnings.append(f"Already has {workload} job(s) that day.")

        scores.append(InstallerScore(installer=installer, score=round(score, 2), reasons=reasons, warnings=warnings))

    scores.sort(key=lambda item: item.score, reverse=True)
    return scores


def has_conflict(conn: sqlite3.Connection, installer_id: int, start: datetime, end: datetime) -> bool:
    rows = conn.execute(
        """
        SELECT scheduled_start, scheduled_end FROM jobs
        WHERE assigned_installer_id = ?
          AND scheduled_start IS NOT NULL
          AND scheduled_end IS NOT NULL
          AND status NOT IN ('cancelled', 'completed')
        """,
        (installer_id,),
    ).fetchall()
    for row in rows:
        try:
            existing_start = datetime.strptime(row["scheduled_start"], "%Y-%m-%d %H:%M")
            existing_end = datetime.strptime(row["scheduled_end"], "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            continue
        if start < existing_end and end > existing_start:
            return True
    return False


def suggest_time_slot(conn: sqlite3.Connection, installer_id: int | None, requested_date: str, estimated_minutes: int) -> tuple[str | None, str | None]:
    if not installer_id or not requested_date:
        return None, None
    try:
        base_date = datetime.strptime(requested_date, "%Y-%m-%d")
    except ValueError:
        return None, None

    slot_hours = [8, 10, 12, 14, 16]
    for day_offset in range(0, 10):
        day = base_date + timedelta(days=day_offset)
        for hour in slot_hours:
            start = day.replace(hour=hour, minute=0)
            end = start + timedelta(minutes=estimated_minutes)
            if end.hour > 19 or end.date() != start.date():
                continue
            if not has_conflict(conn, installer_id, start, end):
                return start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")
    start = base_date.replace(hour=8, minute=0)
    end = start + timedelta(minutes=estimated_minutes)
    return start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")
