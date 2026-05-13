from __future__ import annotations

import calendar as calendar_module
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.database import get_connection, init_db, seed_if_empty
from app.services.bot import answer_question
from app.services.notifications import queue_confirmation_notice, queue_missing_info_reminders
from app.services.recommendation import recommend_for_job
from app.services.route_optimizer import optimize_open_jobs
from app.services.scheduling import recommend_schedule_options, schedule_options_from_json
from app.services.workflow import (
    approve_recommendation,
    generate_portal_token,
    readiness_report,
    refresh_job_readiness,
    refresh_job_recommendation,
    required_field_definitions,
    row_to_dict,
)

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Telemetry Field Operations Planner",
    description="AI-assisted intake, scheduling, routing, assignment approval, notifications, and technical knowledge base for industrial telematics installers.",
    version="0.4.0",
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


@app.on_event("startup")
def startup() -> None:
    init_db()
    seed_if_empty()
    rebuild_operational_statuses()


class RecommendationRequest(BaseModel):
    customer_name: str | None = None
    site_city: str | None = None
    site_state: str | None = None
    requested_date: str | None = None
    requested_date_type: str | None = "exact"
    requested_end_date: str | None = ""
    date_flexibility_notes: str | None = ""
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    fuel_type: str | None = None
    vehicle_voltage: str | None = None
    installation_type: str | None = "Telematics"
    quantity: int | None = 1
    notes: str | None = ""


class BotRequest(BaseModel):
    question: str


class PlanningRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    state: str | None = None
    customer: str | None = None
    max_radius_miles: float = 175.0
    max_days_apart: int = 7
    cost_per_mile: float = 0.75
    flight_cost: float = 450.0


class CrmPurchaseOrderRequest(BaseModel):
    source_system: str = "HubSpot"
    hubspot_deal_id: str | None = None
    po_number: str
    customer_name: str
    customer_type: str | None = "Customer"
    customer_contact_name: str | None = ""
    customer_contact_email: str | None = ""
    customer_contact_phone: str | None = ""
    dealer_name: str | None = ""
    dealer_contact_name: str | None = ""
    dealer_contact_email: str | None = ""
    dealer_contact_phone: str | None = ""
    sales_rep_name: str | None = ""
    sales_rep_email: str | None = ""
    installation_required: bool = True
    requested_date: str | None = None
    requested_date_type: str | None = "exact"
    requested_end_date: str | None = ""
    date_flexibility_notes: str | None = ""
    site_address: str | None = ""
    site_city: str | None = ""
    site_state: str | None = ""
    site_zip: str | None = ""
    site_contact_name: str | None = ""
    site_contact_email: str | None = ""
    site_contact_phone: str | None = ""
    vehicle_make: str | None = ""
    vehicle_model: str | None = ""
    fuel_type: str | None = ""
    vehicle_voltage: str | None = ""
    vehicle_asset_ids: str | None = ""
    installation_type: str | None = "Telematics"
    quantity: int = 1
    notes: str | None = ""


class ProductionTrackingRequest(BaseModel):
    job_id: int | None = None
    po_number: str | None = None
    shipment_carrier: str | None = ""
    shipment_tracking_number: str
    order_ready: bool = True


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


def default_planning_window() -> tuple[str, str]:
    today = date.today()
    return today.isoformat(), (today + timedelta(days=45)).isoformat()


def rebuild_operational_statuses() -> None:
    with get_connection() as conn:
        rows = conn.execute("SELECT id, portal_token FROM jobs").fetchall()
        for row in rows:
            if not row["portal_token"]:
                conn.execute("UPDATE jobs SET portal_token = ? WHERE id = ?", (generate_portal_token(), row["id"]))
            try:
                refresh_job_recommendation(conn, int(row["id"]))
                refresh_job_readiness(conn, int(row["id"]))
            except Exception:
                try:
                    refresh_job_readiness(conn, int(row["id"]))
                except Exception:
                    pass
        conn.commit()


def fetch_counts() -> dict[str, int | float]:
    start, end = default_planning_window()
    with get_connection() as conn:
        counts: dict[str, int | float] = {
            "installers": conn.execute("SELECT COUNT(*) FROM installers WHERE active = 1").fetchone()[0],
            "vehicles": conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0],
            "rules": conn.execute("SELECT COUNT(*) FROM knowledge_rules WHERE active = 1").fetchone()[0],
            "open_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('cancelled', 'completed', 'no_installation_required')").fetchone()[0],
            "confirmed_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('confirmed', 'in_progress')").fetchone()[0],
            "waiting_required_info": conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'waiting_required_info'").fetchone()[0],
            "pending_approval": conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'pending_approval'").fetchone()[0],
            "queued_notifications": conn.execute("SELECT COUNT(*) FROM notifications WHERE status = 'queued'").fetchone()[0],
        }
        plan = optimize_open_jobs(conn, start_date=start, end_date=end)
        counts["route_groups"] = plan["summary"]["consolidated_groups"]
        counts["projected_savings"] = plan["summary"]["projected_total_savings"]
        counts["planned_units"] = plan["summary"]["total_units"]
    return counts


def fetch_job_with_names(conn: Any, job_id: int) -> Any:
    return conn.execute(
        """
        SELECT jobs.*,
               assigned.name AS installer_name,
               suggested.name AS suggested_installer_name
        FROM jobs
        LEFT JOIN installers assigned ON assigned.id = jobs.assigned_installer_id
        LEFT JOIN installers suggested ON suggested.id = jobs.suggested_installer_id
        WHERE jobs.id = ?
        """,
        (job_id,),
    ).fetchone()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.4.0"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    start, end = default_planning_window()
    with get_connection() as conn:
        confirmed_jobs = conn.execute(
            """
            SELECT jobs.*, installers.name AS installer_name
            FROM jobs
            LEFT JOIN installers ON installers.id = jobs.assigned_installer_id
            WHERE jobs.status IN ('confirmed', 'in_progress')
            ORDER BY requested_date ASC, scheduled_start ASC
            LIMIT 8
            """
        ).fetchall()
        pending_jobs = conn.execute(
            """
            SELECT jobs.*, suggested.name AS suggested_installer_name
            FROM jobs
            LEFT JOIN installers suggested ON suggested.id = jobs.suggested_installer_id
            WHERE jobs.status IN ('waiting_required_info', 'pending_approval', 'po_received')
            ORDER BY status, requested_date ASC, updated_at DESC
            LIMIT 10
            """
        ).fetchall()
        notifications = conn.execute(
            """
            SELECT notifications.*, jobs.customer_name, jobs.po_number
            FROM notifications
            LEFT JOIN jobs ON jobs.id = notifications.job_id
            WHERE notifications.status = 'queued'
            ORDER BY notifications.created_at DESC
            LIMIT 5
            """
        ).fetchall()
        plan = optimize_open_jobs(conn, start_date=start, end_date=end)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "counts": fetch_counts(),
            "confirmed_jobs": confirmed_jobs,
            "pending_jobs": pending_jobs,
            "notifications": notifications,
            "planning_preview": plan,
        },
    )


@app.get("/intake", response_class=HTMLResponse)
def intake_queue(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        jobs = conn.execute(
            """
            SELECT jobs.*, suggested.name AS suggested_installer_name
            FROM jobs
            LEFT JOIN installers suggested ON suggested.id = jobs.suggested_installer_id
            WHERE jobs.status IN ('waiting_required_info', 'pending_approval', 'po_received')
            ORDER BY CASE jobs.status WHEN 'waiting_required_info' THEN 1 WHEN 'pending_approval' THEN 2 ELSE 3 END,
                     jobs.requested_date ASC, jobs.updated_at DESC
            """
        ).fetchall()
    return templates.TemplateResponse(request, "intake.html", {"jobs": jobs, "required_fields": required_field_definitions()})


@app.get("/installers", response_class=HTMLResponse)
def installers_page(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        installers = conn.execute("SELECT * FROM installers ORDER BY active DESC, home_state, name").fetchall()
    return templates.TemplateResponse(request, "installers.html", {"installers": installers})


@app.post("/installers")
def create_installer(
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    home_city: str = Form(""),
    home_state: str = Form(...),
    coverage_states: str = Form(""),
    skill_tags: str = Form(""),
    max_complexity: int = Form(3),
) -> RedirectResponse:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO installers
            (name, email, phone, home_city, home_state, coverage_states, skill_tags, max_complexity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, email, phone, home_city, home_state.upper(), coverage_states.upper(), skill_tags, max_complexity),
        )
        conn.commit()
    return redirect("/installers")


@app.post("/installers/{installer_id}/toggle")
def toggle_installer(installer_id: int) -> RedirectResponse:
    with get_connection() as conn:
        row = conn.execute("SELECT active FROM installers WHERE id = ?", (installer_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Installer not found")
        new_active = 0 if row["active"] else 1
        conn.execute("UPDATE installers SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_active, installer_id))
        conn.commit()
    return redirect("/installers")


@app.get("/vehicles", response_class=HTMLResponse)
def vehicles_page(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        vehicles = conn.execute("SELECT * FROM vehicles ORDER BY make, model, fuel_type").fetchall()
    return templates.TemplateResponse(request, "vehicles.html", {"vehicles": vehicles})


@app.post("/vehicles")
def create_vehicle(
    make: str = Form(...),
    model: str = Form(...),
    fuel_type: str = Form(""),
    complexity: int = Form(3),
    estimated_minutes: int = Form(120),
    skill_tags: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO vehicles (make, model, fuel_type, complexity, estimated_minutes, skill_tags, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (make, model, fuel_type, complexity, estimated_minutes, skill_tags, notes),
        )
        conn.commit()
    return redirect("/vehicles")


@app.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        rules = conn.execute("SELECT * FROM knowledge_rules ORDER BY active DESC, updated_at DESC, title").fetchall()
    return templates.TemplateResponse(request, "knowledge.html", {"rules": rules})


@app.post("/knowledge")
def create_knowledge_rule(
    title: str = Form(...),
    triggers: str = Form(""),
    vehicle_make: str = Form(""),
    vehicle_model: str = Form(""),
    fuel_type: str = Form(""),
    complexity_adjustment: int = Form(0),
    required_tools: str = Form(""),
    required_materials: str = Form(""),
    checklist: str = Form(""),
    risk_notes: str = Form(""),
) -> RedirectResponse:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_rules
            (title, triggers, vehicle_make, vehicle_model, fuel_type, complexity_adjustment,
             required_tools, required_materials, checklist, risk_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, triggers, vehicle_make, vehicle_model, fuel_type, complexity_adjustment, required_tools, required_materials, checklist, risk_notes),
        )
        conn.commit()
    return redirect("/knowledge")


@app.post("/knowledge/{rule_id}/toggle")
def toggle_rule(rule_id: int) -> RedirectResponse:
    with get_connection() as conn:
        row = conn.execute("SELECT active FROM knowledge_rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        new_active = 0 if row["active"] else 1
        conn.execute("UPDATE knowledge_rules SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_active, rule_id))
        conn.commit()
    return redirect("/knowledge")


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        jobs = conn.execute(
            """
            SELECT jobs.*, assigned.name AS installer_name, suggested.name AS suggested_installer_name
            FROM jobs
            LEFT JOIN installers assigned ON assigned.id = jobs.assigned_installer_id
            LEFT JOIN installers suggested ON suggested.id = jobs.suggested_installer_id
            ORDER BY requested_date DESC, updated_at DESC
            """
        ).fetchall()
    return templates.TemplateResponse(request, "jobs.html", {"jobs": jobs})


@app.get("/jobs/new", response_class=HTMLResponse)
def new_job_page(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        vehicles = conn.execute("SELECT * FROM vehicles ORDER BY make, model").fetchall()
    today = date.today().isoformat()
    return templates.TemplateResponse(request, "job_form.html", {"vehicles": vehicles, "today": today, "required_fields": required_field_definitions()})


@app.post("/jobs/new")
def create_job(
    customer_name: str = Form(...),
    customer_type: str = Form("Customer"),
    customer_contact_name: str = Form(""),
    customer_contact_email: str = Form(""),
    customer_contact_phone: str = Form(""),
    dealer_name: str = Form(""),
    dealer_contact_name: str = Form(""),
    dealer_contact_email: str = Form(""),
    dealer_contact_phone: str = Form(""),
    sales_rep_name: str = Form(""),
    sales_rep_email: str = Form(""),
    po_number: str = Form(""),
    hubspot_deal_id: str = Form(""),
    installation_required: str | None = Form("yes"),
    order_ready: str | None = Form(None),
    shipment_carrier: str = Form(""),
    shipment_tracking_number: str = Form(""),
    requested_date: str = Form(...),
    requested_date_type: str = Form("exact"),
    requested_end_date: str = Form(""),
    date_flexibility_notes: str = Form(""),
    site_address: str = Form(""),
    site_city: str = Form(""),
    site_state: str = Form(""),
    site_zip: str = Form(""),
    site_contact_name: str = Form(""),
    site_contact_email: str = Form(""),
    site_contact_phone: str = Form(""),
    site_access_hours: str = Form(""),
    site_access_notes: str = Form(""),
    vehicle_make: str = Form(""),
    vehicle_model: str = Form(""),
    fuel_type: str = Form(""),
    vehicle_voltage: str = Form(""),
    vehicle_asset_ids: str = Form(""),
    vehicle_year: str = Form(""),
    installation_type: str = Form("Telematics + Display"),
    quantity: int = Form(1),
    notes: str = Form(""),
    send_initial_reminder: str | None = Form(None),
) -> RedirectResponse:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO jobs
            (customer_name, customer_type, customer_contact_name, customer_contact_email, customer_contact_phone,
             dealer_name, dealer_contact_name, dealer_contact_email, dealer_contact_phone,
             sales_rep_name, sales_rep_email, po_number, hubspot_deal_id, installation_required, order_ready,
             shipment_carrier, shipment_tracking_number, portal_token, requested_date, requested_date_type, requested_end_date, date_flexibility_notes, site_address, site_city, site_state,
             site_zip, site_contact_name, site_contact_email, site_contact_phone, site_access_hours, site_access_notes,
             vehicle_make, vehicle_model, fuel_type, vehicle_voltage, vehicle_asset_ids, vehicle_year,
             installation_type, quantity, notes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'po_received')
            """,
            (
                customer_name,
                customer_type,
                customer_contact_name,
                customer_contact_email,
                customer_contact_phone,
                dealer_name,
                dealer_contact_name,
                dealer_contact_email,
                dealer_contact_phone,
                sales_rep_name,
                sales_rep_email,
                po_number,
                hubspot_deal_id,
                1 if installation_required else 0,
                1 if order_ready else 0,
                shipment_carrier,
                shipment_tracking_number,
                generate_portal_token(),
                requested_date,
                requested_date_type,
                requested_end_date,
                date_flexibility_notes,
                site_address,
                site_city,
                site_state.upper(),
                site_zip,
                site_contact_name,
                site_contact_email,
                site_contact_phone,
                site_access_hours,
                site_access_notes,
                vehicle_make,
                vehicle_model,
                fuel_type,
                vehicle_voltage,
                vehicle_asset_ids,
                vehicle_year,
                installation_type,
                quantity,
                notes,
            ),
        )
        job_id = int(cursor.lastrowid)
        refresh_job_recommendation(conn, job_id)
        report = refresh_job_readiness(conn, job_id)
        if send_initial_reminder and report["missing_fields"]:
            queue_missing_info_reminders(conn, job_id)
        conn.commit()
    return redirect(f"/jobs/{job_id}")


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int) -> HTMLResponse:
    with get_connection() as conn:
        job = fetch_job_with_names(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job_dict = row_to_dict(job)
        recommendation = recommend_for_job(conn, job_dict)
        readiness = readiness_report(job_dict)
        schedule_plan = schedule_options_from_json(job_dict.get("suggested_schedule_options"))
        if not schedule_plan.get("options"):
            schedule_plan = recommend_schedule_options(conn, job_dict, recommendation)
        installers = conn.execute("SELECT * FROM installers WHERE active = 1 ORDER BY name").fetchall()
        notifications = conn.execute(
            "SELECT * FROM notifications WHERE job_id = ? ORDER BY created_at DESC LIMIT 8",
            (job_id,),
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "recommendation": recommendation,
            "readiness": readiness,
            "schedule_plan": schedule_plan,
            "installers": installers,
            "notifications": notifications,
            "required_fields": required_field_definitions(),
        },
    )


@app.post("/jobs/{job_id}/refresh")
def refresh_job(job_id: int) -> RedirectResponse:
    with get_connection() as conn:
        refresh_job_recommendation(conn, job_id)
        refresh_job_readiness(conn, job_id)
        conn.commit()
    return redirect(f"/jobs/{job_id}")


@app.post("/jobs/{job_id}/suggest-installer")
def suggest_installer(job_id: int, installer_id: int = Form(...)) -> RedirectResponse:
    with get_connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        scheduled_start, scheduled_end = conn.execute(
            "SELECT suggested_scheduled_start, suggested_scheduled_end FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not scheduled_start:
            from app.services.recommendation import suggest_time_slot

            scheduled_start, scheduled_end = suggest_time_slot(conn, installer_id, job["requested_date"], job["estimated_minutes"])
        conn.execute(
            """
            UPDATE jobs
            SET suggested_installer_id = ?, suggested_scheduled_start = ?, suggested_scheduled_end = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (installer_id, scheduled_start, scheduled_end, job_id),
        )
        refresh_job_readiness(conn, job_id)
        conn.commit()
    return redirect(f"/jobs/{job_id}")


@app.post("/jobs/{job_id}/approve")
def approve_job(
    job_id: int,
    installer_id: str | None = Form(None),
    scheduled_start: str | None = Form(None),
    scheduled_end: str | None = Form(None),
    schedule_choice: str | None = Form(None),
    approved_by: str = Form("Coordinator"),
) -> RedirectResponse:
    selected_installer_id: int | None = None
    if schedule_choice:
        parts = schedule_choice.split("||")
        if len(parts) == 3:
            try:
                selected_installer_id = int(parts[0])
                scheduled_start = parts[1]
                scheduled_end = parts[2]
            except ValueError:
                pass
    if installer_id and installer_id.strip():
        try:
            selected_installer_id = int(installer_id)
        except ValueError:
            pass
    with get_connection() as conn:
        result = approve_recommendation(conn, job_id, approved_by=approved_by, installer_id=selected_installer_id, scheduled_start=scheduled_start, scheduled_end=scheduled_end)
        if not result.get("approved") and result.get("reason") == "missing_required_info":
            queue_missing_info_reminders(conn, job_id)
        elif result.get("approved"):
            queue_confirmation_notice(conn, job_id)
        conn.commit()
    return redirect(f"/jobs/{job_id}")


@app.post("/jobs/{job_id}/send-reminders")
def send_job_reminders(job_id: int) -> RedirectResponse:
    with get_connection() as conn:
        queue_missing_info_reminders(conn, job_id)
        conn.commit()
    return redirect(f"/jobs/{job_id}")


@app.post("/jobs/{job_id}/production")
def update_production_info(
    job_id: int,
    shipment_carrier: str = Form(""),
    shipment_tracking_number: str = Form(""),
    order_ready: str | None = Form(None),
) -> RedirectResponse:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET shipment_carrier = ?, shipment_tracking_number = ?, order_ready = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (shipment_carrier, shipment_tracking_number, 1 if order_ready else 0, job_id),
        )
        refresh_job_recommendation(conn, job_id)
        refresh_job_readiness(conn, job_id)
        conn.commit()
    return redirect(f"/jobs/{job_id}")


@app.post("/jobs/{job_id}/status")
def update_job_status(job_id: int, status: str = Form(...)) -> RedirectResponse:
    allowed = {
        "po_received",
        "waiting_required_info",
        "pending_approval",
        "confirmed",
        "in_progress",
        "completed",
        "cancelled",
        "no_installation_required",
    }
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status")
    with get_connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        report = readiness_report(job)
        if status in {"confirmed", "in_progress"} and not report["is_complete"]:
            queue_missing_info_reminders(conn, job_id)
            refresh_job_readiness(conn, job_id)
        else:
            conn.execute("UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, job_id))
        conn.commit()
    return redirect(f"/jobs/{job_id}")


@app.get("/portal/{portal_token}", response_class=HTMLResponse)
def portal_page(request: Request, portal_token: str) -> HTMLResponse:
    with get_connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE portal_token = ?", (portal_token,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Portal link not found")
        readiness = readiness_report(job)
    return templates.TemplateResponse(request, "portal.html", {"job": job, "readiness": readiness, "public_base": str(request.base_url).rstrip("/")})


@app.post("/portal/{portal_token}")
def portal_update(
    portal_token: str,
    customer_contact_name: str = Form(""),
    customer_contact_email: str = Form(""),
    customer_contact_phone: str = Form(""),
    dealer_name: str = Form(""),
    dealer_contact_name: str = Form(""),
    dealer_contact_email: str = Form(""),
    dealer_contact_phone: str = Form(""),
    requested_date: str = Form(""),
    requested_date_type: str = Form("exact"),
    requested_end_date: str = Form(""),
    date_flexibility_notes: str = Form(""),
    site_address: str = Form(""),
    site_city: str = Form(""),
    site_state: str = Form(""),
    site_zip: str = Form(""),
    site_contact_name: str = Form(""),
    site_contact_email: str = Form(""),
    site_contact_phone: str = Form(""),
    site_access_hours: str = Form(""),
    site_access_notes: str = Form(""),
    vehicle_make: str = Form(""),
    vehicle_model: str = Form(""),
    fuel_type: str = Form(""),
    vehicle_voltage: str = Form(""),
    vehicle_asset_ids: str = Form(""),
    vehicle_year: str = Form(""),
    quantity: int = Form(1),
    notes: str = Form(""),
) -> RedirectResponse:
    with get_connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE portal_token = ?", (portal_token,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Portal link not found")
        conn.execute(
            """
            UPDATE jobs
            SET customer_contact_name = ?, customer_contact_email = ?, customer_contact_phone = ?,
                dealer_name = ?, dealer_contact_name = ?, dealer_contact_email = ?, dealer_contact_phone = ?,
                requested_date = COALESCE(NULLIF(?, ''), requested_date),
                requested_date_type = ?, requested_end_date = ?, date_flexibility_notes = ?,
                site_address = ?, site_city = ?, site_state = ?, site_zip = ?, site_contact_name = ?,
                site_contact_email = ?, site_contact_phone = ?, site_access_hours = ?, site_access_notes = ?,
                vehicle_make = ?, vehicle_model = ?, fuel_type = ?, vehicle_voltage = ?, vehicle_asset_ids = ?,
                vehicle_year = ?, quantity = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE portal_token = ?
            """,
            (
                customer_contact_name,
                customer_contact_email,
                customer_contact_phone,
                dealer_name,
                dealer_contact_name,
                dealer_contact_email,
                dealer_contact_phone,
                requested_date,
                requested_date_type,
                requested_end_date,
                date_flexibility_notes,
                site_address,
                site_city,
                site_state.upper(),
                site_zip,
                site_contact_name,
                site_contact_email,
                site_contact_phone,
                site_access_hours,
                site_access_notes,
                vehicle_make,
                vehicle_model,
                fuel_type,
                vehicle_voltage,
                vehicle_asset_ids,
                vehicle_year,
                quantity,
                notes,
                portal_token,
            ),
        )
        job_id = int(job["id"])
        refresh_job_recommendation(conn, job_id)
        report = refresh_job_readiness(conn, job_id)
        if report["is_complete"]:
            # Notify internal scheduling that the job is ready for coordinator approval.
            conn.execute(
                """
                INSERT INTO notifications (job_id, recipient_type, recipient_email, subject, body, channel, status)
                VALUES (?, 'scheduling_team', 'installation-scheduling@example.com', ?, ?, 'email', 'queued')
                """,
                (
                    job_id,
                    f"Ready for approval: {job['po_number'] or 'Job #' + str(job_id)}",
                    "Required customer/dealer information is complete. Review the AI recommendation and approve the schedule.",
                ),
            )
        conn.commit()
    return redirect(f"/portal/{portal_token}")


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, month: str | None = None) -> HTMLResponse:
    if month:
        try:
            first_day = datetime.strptime(month + "-01", "%Y-%m-%d").date()
        except ValueError:
            first_day = date.today().replace(day=1)
    else:
        first_day = date.today().replace(day=1)

    prev_month = (first_day.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
    _, days_in_month = calendar_module.monthrange(first_day.year, first_day.month)
    start = first_day.isoformat()
    end = first_day.replace(day=days_in_month).isoformat()

    with get_connection() as conn:
        jobs = conn.execute(
            """
            SELECT jobs.*, installers.name AS installer_name
            FROM jobs
            LEFT JOIN installers ON installers.id = jobs.assigned_installer_id
            WHERE substr(COALESCE(jobs.scheduled_start, jobs.requested_date), 1, 10) BETWEEN ? AND ?
              AND jobs.status IN ('confirmed', 'in_progress', 'completed')
            ORDER BY scheduled_start, requested_date, customer_name
            """,
            (start, end),
        ).fetchall()

    jobs_by_date: dict[str, list[Any]] = defaultdict(list)
    for job in jobs:
        schedule_day = (job["scheduled_start"] or job["requested_date"] or "")[:10]
        jobs_by_date[schedule_day].append(job)

    cal = calendar_module.Calendar(firstweekday=6)
    weeks = []
    for week in cal.monthdatescalendar(first_day.year, first_day.month):
        week_data = []
        for day in week:
            day_iso = day.isoformat()
            week_data.append({"date": day, "iso": day_iso, "in_month": day.month == first_day.month, "jobs": jobs_by_date.get(day_iso, [])})
        weeks.append(week_data)

    return templates.TemplateResponse(
        request,
        "calendar.html",
        {"month_label": first_day.strftime("%B %Y"), "prev_month": prev_month.strftime("%Y-%m"), "next_month": next_month.strftime("%Y-%m"), "weeks": weeks},
    )


@app.get("/planning", response_class=HTMLResponse)
def planning_page(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    state: str | None = None,
    customer: str | None = None,
    max_radius_miles: float = 175.0,
    max_days_apart: int = 7,
) -> HTMLResponse:
    default_start, default_end = default_planning_window()
    start_date = start_date or default_start
    end_date = end_date or default_end
    with get_connection() as conn:
        plan = optimize_open_jobs(conn, start_date=start_date, end_date=end_date, state=state or None, customer=customer or None, max_radius_miles=max_radius_miles, max_days_apart=max_days_apart)
    return templates.TemplateResponse(
        request,
        "planning.html",
        {"plan": plan, "start_date": start_date, "end_date": end_date, "state": state or "", "customer": customer or "", "max_radius_miles": max_radius_miles, "max_days_apart": max_days_apart},
    )


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        notifications = conn.execute(
            """
            SELECT notifications.*, jobs.customer_name, jobs.po_number
            FROM notifications
            LEFT JOIN jobs ON jobs.id = notifications.job_id
            ORDER BY notifications.created_at DESC
            LIMIT 100
            """
        ).fetchall()
    return templates.TemplateResponse(request, "notifications.html", {"notifications": notifications})


@app.post("/notifications/{notification_id}/sent")
def mark_notification_sent(notification_id: int) -> RedirectResponse:
    with get_connection() as conn:
        conn.execute("UPDATE notifications SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?", (notification_id,))
        conn.commit()
    return redirect("/notifications")


@app.get("/integrations", response_class=HTMLResponse)
def integrations_page(request: Request) -> HTMLResponse:
    with get_connection() as conn:
        events = conn.execute("SELECT * FROM integration_events ORDER BY created_at DESC LIMIT 25").fetchall()
    return templates.TemplateResponse(request, "integrations.html", {"events": events})


@app.get("/bot", response_class=HTMLResponse)
def bot_page(request: Request, question: str | None = None) -> HTMLResponse:
    result = None
    if question:
        with get_connection() as conn:
            result = answer_question(conn, question)
    return templates.TemplateResponse(request, "bot.html", {"question": question or "", "result": result})


@app.post("/api/recommendation")
def api_recommendation(payload: RecommendationRequest) -> JSONResponse:
    with get_connection() as conn:
        recommendation = recommend_for_job(conn, payload.model_dump())
    return JSONResponse(recommendation)


@app.post("/api/planning/optimize")
def api_planning_optimize(payload: PlanningRequest) -> JSONResponse:
    with get_connection() as conn:
        plan = optimize_open_jobs(conn, start_date=payload.start_date, end_date=payload.end_date, state=payload.state, customer=payload.customer, max_radius_miles=payload.max_radius_miles, max_days_apart=payload.max_days_apart, cost_per_mile=payload.cost_per_mile, flight_cost=payload.flight_cost)
    return JSONResponse(plan)


@app.post("/api/bot/ask")
def api_bot_ask(payload: BotRequest) -> JSONResponse:
    with get_connection() as conn:
        result = answer_question(conn, payload.question)
    return JSONResponse(result)


@app.post("/api/crm/hubspot/po")
def api_create_job_from_crm(payload: CrmPurchaseOrderRequest, request: Request) -> JSONResponse:
    requested_date = payload.requested_date or date.today().isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO jobs
            (customer_name, customer_type, customer_contact_name, customer_contact_email, customer_contact_phone,
             dealer_name, dealer_contact_name, dealer_contact_email, dealer_contact_phone,
             sales_rep_name, sales_rep_email, po_number, hubspot_deal_id, installation_required,
             portal_token, requested_date, requested_date_type, requested_end_date, date_flexibility_notes, site_address, site_city, site_state, site_zip, site_contact_name,
             site_contact_email, site_contact_phone, vehicle_make, vehicle_model, fuel_type, vehicle_voltage,
             vehicle_asset_ids, installation_type, quantity, notes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'po_received')
            """,
            (
                payload.customer_name,
                payload.customer_type or "Customer",
                payload.customer_contact_name or "",
                payload.customer_contact_email or "",
                payload.customer_contact_phone or "",
                payload.dealer_name or "",
                payload.dealer_contact_name or "",
                payload.dealer_contact_email or "",
                payload.dealer_contact_phone or "",
                payload.sales_rep_name or "",
                payload.sales_rep_email or "",
                payload.po_number,
                payload.hubspot_deal_id or "",
                1 if payload.installation_required else 0,
                generate_portal_token(),
                requested_date,
                payload.requested_date_type or "exact",
                payload.requested_end_date or "",
                payload.date_flexibility_notes or "",
                payload.site_address or "",
                payload.site_city or "",
                (payload.site_state or "").upper(),
                payload.site_zip or "",
                payload.site_contact_name or "",
                payload.site_contact_email or "",
                payload.site_contact_phone or "",
                payload.vehicle_make or "",
                payload.vehicle_model or "",
                payload.fuel_type or "",
                payload.vehicle_voltage or "",
                payload.vehicle_asset_ids or "",
                payload.installation_type or "Telematics",
                payload.quantity,
                payload.notes or "",
            ),
        )
        job_id = int(cursor.lastrowid)
        refresh_job_recommendation(conn, job_id)
        report = refresh_job_readiness(conn, job_id)
        if report["missing_fields"]:
            queue_missing_info_reminders(conn, job_id)
        conn.execute(
            """
            INSERT INTO integration_events (source_system, external_id, event_type, job_id, payload, status)
            VALUES (?, ?, 'po_received', ?, ?, 'processed')
            """,
            (payload.source_system, payload.hubspot_deal_id or payload.po_number, job_id, json.dumps(payload.model_dump())),
        )
        conn.commit()
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return JSONResponse({"job_id": job_id, "status": job["status"], "missing_required_fields": report["missing_labels"], "portal_url": f"{str(request.base_url).rstrip('/')}/portal/{job['portal_token']}"})


@app.post("/api/production/tracking")
def api_update_tracking(payload: ProductionTrackingRequest) -> JSONResponse:
    with get_connection() as conn:
        if payload.job_id:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (payload.job_id,)).fetchone()
        elif payload.po_number:
            job = conn.execute("SELECT * FROM jobs WHERE po_number = ?", (payload.po_number,)).fetchone()
        else:
            raise HTTPException(status_code=400, detail="job_id or po_number is required")
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        conn.execute(
            """
            UPDATE jobs
            SET shipment_carrier = ?, shipment_tracking_number = ?, order_ready = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (payload.shipment_carrier or "", payload.shipment_tracking_number, 1 if payload.order_ready else 0, job["id"]),
        )
        refresh_job_recommendation(conn, int(job["id"]))
        report = refresh_job_readiness(conn, int(job["id"]))
        conn.execute(
            """
            INSERT INTO integration_events (source_system, external_id, event_type, job_id, payload, status)
            VALUES ('Production', ?, 'tracking_updated', ?, ?, 'processed')
            """,
            (payload.po_number or str(job["id"]), job["id"], json.dumps(payload.model_dump())),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    return JSONResponse({"job_id": updated["id"], "status": updated["status"], "missing_required_fields": report["missing_labels"]})


@app.get("/api/jobs/{job_id}/schedule-options")
def api_job_schedule_options(job_id: int) -> JSONResponse:
    with get_connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job_data = row_to_dict(job)
        recommendation = recommend_for_job(conn, job_data)
        plan = recommend_schedule_options(conn, job_data, recommendation)
    return JSONResponse(plan)


@app.get("/api/jobs")
def api_jobs() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT jobs.*, installers.name AS installer_name
            FROM jobs
            LEFT JOIN installers ON installers.id = jobs.assigned_installer_id
            ORDER BY requested_date, scheduled_start
            """
        ).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/jobs/{job_id}/readiness")
def api_job_readiness(job_id: int) -> JSONResponse:
    with get_connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        report = readiness_report(job)
    return JSONResponse(report)


@app.get("/api/installers")
def api_installers() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM installers ORDER BY name").fetchall()
    return rows_to_dicts(rows)
