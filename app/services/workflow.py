from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from typing import Any

from app.services.recommendation import recommend_for_job, suggest_time_slot
from app.services.scheduling import recommend_schedule_options, schedule_options_to_json

TERMINAL_STATUSES = {"completed", "cancelled"}
CONFIRMED_STATUSES = {"confirmed", "in_progress", "completed"}

REQUIRED_FIELDS: list[dict[str, str]] = [
    {"field": "po_number", "label": "Purchase order number", "owner": "internal"},
    {"field": "customer_name", "label": "Customer name", "owner": "internal"},
    {"field": "customer_contact_email", "label": "Customer or dealer email", "owner": "customer"},
    {"field": "sales_rep_email", "label": "Sales rep email", "owner": "internal"},
    {"field": "requested_date", "label": "Requested installation date or window start", "owner": "customer"},
    {"field": "site_address", "label": "Installation address", "owner": "customer"},
    {"field": "site_city", "label": "Installation city", "owner": "customer"},
    {"field": "site_state", "label": "Installation state", "owner": "customer"},
    {"field": "site_zip", "label": "Installation ZIP code", "owner": "customer"},
    {"field": "site_contact_name", "label": "On-site contact name", "owner": "customer"},
    {"field": "site_contact_phone", "label": "On-site contact phone", "owner": "customer"},
    {"field": "vehicle_make", "label": "Vehicle make", "owner": "customer"},
    {"field": "vehicle_model", "label": "Vehicle model", "owner": "customer"},
    {"field": "fuel_type", "label": "Fuel / power type", "owner": "customer"},
    {"field": "vehicle_voltage", "label": "Vehicle voltage", "owner": "customer"},
    {"field": "vehicle_asset_ids", "label": "Vehicle serial numbers or asset IDs", "owner": "customer"},
    {"field": "quantity", "label": "Unit quantity", "owner": "customer"},
    {"field": "shipment_tracking_number", "label": "Shipment tracking number", "owner": "production"},
]


def generate_portal_token() -> str:
    return secrets.token_urlsafe(24)


def row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, int | float):
        return value > 0
    return bool(value)


def required_field_definitions() -> list[dict[str, str]]:
    return [dict(item) for item in REQUIRED_FIELDS]


def missing_required_fields(job: sqlite3.Row | dict[str, Any]) -> list[dict[str, str]]:
    data = row_to_dict(job)
    missing: list[dict[str, str]] = []
    installation_required = int(data.get("installation_required") or 0)
    if not installation_required:
        return missing
    for item in REQUIRED_FIELDS:
        value = data.get(item["field"])
        if not has_value(value):
            missing.append(dict(item))
    if str(data.get("requested_date_type") or "exact").lower() == "range" and not has_value(data.get("requested_end_date")):
        missing.append({"field": "requested_end_date", "label": "Requested window end date", "owner": "customer"})
    return missing


def readiness_report(job: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = row_to_dict(job)
    missing = missing_required_fields(data)
    total = len(REQUIRED_FIELDS)
    completed = max(0, total - len(missing))
    percent = int(round((completed / total) * 100)) if total else 100
    approved = bool(data.get("coordinator_approved_at"))
    suggested_installer_ready = bool(data.get("suggested_installer_id") or data.get("assigned_installer_id"))
    can_confirm = not missing and suggested_installer_ready
    return {
        "is_complete": not missing,
        "can_confirm": can_confirm,
        "completion_percent": percent,
        "completed_fields": completed,
        "total_required_fields": total,
        "missing_fields": missing,
        "missing_labels": [item["label"] for item in missing],
        "missing_field_names": [item["field"] for item in missing],
        "approved": approved,
        "suggested_installer_ready": suggested_installer_ready,
    }


def compact_missing_labels(missing: list[dict[str, str]]) -> str:
    return json.dumps([{"field": item["field"], "label": item["label"], "owner": item["owner"]} for item in missing])


def derive_status(job: sqlite3.Row | dict[str, Any]) -> str:
    data = row_to_dict(job)
    current = str(data.get("status") or "po_received")
    if current in TERMINAL_STATUSES:
        return current
    if not int(data.get("installation_required") or 0):
        return "no_installation_required"
    report = readiness_report(data)
    if report["missing_fields"]:
        return "waiting_required_info"
    if data.get("coordinator_approved_at") or current in {"confirmed", "in_progress"}:
        return "confirmed" if current not in {"in_progress"} else current
    return "pending_approval"


def refresh_job_recommendation(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ValueError(f"Job {job_id} not found")
    job_data = row_to_dict(job)
    recommendation = recommend_for_job(conn, job_data)
    schedule_plan = recommend_schedule_options(conn, job_data, recommendation)
    best_schedule = schedule_plan.get("best_option") or {}
    suggested_installer_id = best_schedule.get("installer_id")
    if not suggested_installer_id and recommendation.get("ranked_installers"):
        suggested_installer_id = recommendation["ranked_installers"][0]["installer"]["id"]
    suggested_start = best_schedule.get("start")
    suggested_end = best_schedule.get("end")
    if not suggested_start and suggested_installer_id:
        suggested_start, suggested_end = suggest_time_slot(
            conn,
            suggested_installer_id,
            job_data.get("requested_date") or datetime.utcnow().date().isoformat(),
            int(recommendation.get("estimated_minutes") or 120),
        )
    summary = build_recommendation_summary(recommendation, schedule_plan)
    conn.execute(
        """
        UPDATE jobs
        SET complexity = ?, estimated_minutes = ?, required_tools = ?, required_materials = ?,
            checklist = ?, risk_notes = ?, suggested_installer_id = ?, suggested_scheduled_start = ?,
            suggested_scheduled_end = ?, suggested_schedule_reason = ?, suggested_schedule_options = ?,
            ai_recommendation_summary = ?, ai_recommendation_generated_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            recommendation["complexity"],
            recommendation["estimated_minutes"],
            recommendation["required_tools"],
            recommendation["required_materials"],
            recommendation["checklist"],
            recommendation["risk_notes"],
            suggested_installer_id,
            suggested_start,
            suggested_end,
            schedule_plan.get("reason", ""),
            schedule_options_to_json(schedule_plan),
            summary,
            job_id,
        ),
    )
    refresh_job_readiness(conn, job_id)
    return recommendation


def build_recommendation_summary(recommendation: dict[str, Any], schedule_plan: dict[str, Any]) -> str:
    top = recommendation.get("ranked_installers", [])[:3]
    installer_lines = []
    for idx, score in enumerate(top, start=1):
        installer = score.get("installer", {})
        installer_lines.append(f"{idx}. {installer.get('name')} - score {score.get('score')}")
    best = schedule_plan.get("best_option") or {}
    lines = [
        f"Difficulty: {recommendation.get('complexity')}/5",
        f"Estimated labor: {recommendation.get('estimated_minutes')} minutes",
        f"Customer date request: {schedule_plan.get('window', {}).get('label', 'not specified')}",
        f"Recommended schedule: {best.get('start', 'not available')} to {best.get('end', '')}",
        f"Recommended installer: {best.get('installer_name', 'not available')}",
        f"Schedule reason: {schedule_plan.get('reason', 'not available')}",
        "Top installer options:",
        *installer_lines,
    ]
    if recommendation.get("required_materials"):
        lines.append(f"Required materials: {recommendation['required_materials']}")
    if recommendation.get("risk_notes"):
        lines.append(f"Risks / notes: {recommendation['risk_notes']}")
    return "\n".join(lines)


def refresh_job_readiness(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ValueError(f"Job {job_id} not found")
    data = row_to_dict(job)
    report = readiness_report(data)
    new_status = derive_status(data)
    completed_at_expr = "CURRENT_TIMESTAMP" if report["is_complete"] else "NULL"
    conn.execute(
        f"""
        UPDATE jobs
        SET info_completion_percent = ?, missing_required_fields = ?, status = ?,
            required_info_completed_at = {completed_at_expr}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (report["completion_percent"], compact_missing_labels(report["missing_fields"]), new_status, job_id),
    )
    return report


def approve_recommendation(
    conn: sqlite3.Connection,
    job_id: int,
    approved_by: str = "Coordinator",
    installer_id: int | None = None,
    scheduled_start: str | None = None,
    scheduled_end: str | None = None,
) -> dict[str, Any]:
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ValueError(f"Job {job_id} not found")
    report = readiness_report(job)
    if not report["is_complete"]:
        refresh_job_readiness(conn, job_id)
        return {"approved": False, "reason": "missing_required_info", "readiness": report}

    data = row_to_dict(job)
    selected_installer_id = installer_id or data.get("suggested_installer_id") or data.get("assigned_installer_id")
    if not selected_installer_id:
        recommendation = refresh_job_recommendation(conn, job_id)
        if recommendation.get("ranked_installers"):
            selected_installer_id = recommendation["ranked_installers"][0]["installer"]["id"]
    if not selected_installer_id:
        return {"approved": False, "reason": "no_installer_available", "readiness": report}

    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    data = row_to_dict(job)
    scheduled_start = scheduled_start or data.get("suggested_scheduled_start")
    scheduled_end = scheduled_end or data.get("suggested_scheduled_end")
    if not scheduled_start:
        scheduled_start, scheduled_end = suggest_time_slot(conn, int(selected_installer_id), data.get("requested_date"), data.get("estimated_minutes"))
    conn.execute(
        """
        UPDATE jobs
        SET assigned_installer_id = ?, scheduled_start = ?, scheduled_end = ?, status = 'confirmed',
            coordinator_approved_at = CURRENT_TIMESTAMP, coordinator_approved_by = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (int(selected_installer_id), scheduled_start, scheduled_end, approved_by, job_id),
    )
    return {"approved": True, "readiness": report, "installer_id": int(selected_installer_id)}
