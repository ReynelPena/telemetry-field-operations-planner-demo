from __future__ import annotations

import sqlite3

from app.database import create_schema
from app.seed import seed_database
from app.services.bot import answer_question
from app.services.notifications import queue_missing_info_reminders
from app.services.recommendation import recommend_for_job
from app.services.route_optimizer import optimize_open_jobs
from app.services.workflow import approve_recommendation, readiness_report, refresh_job_readiness, refresh_job_recommendation


def build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    seed_database(conn)
    return conn


def test_toyota_lp_is_low_difficulty_and_recommends_longer_screws():
    conn = build_conn()
    result = recommend_for_job(
        conn,
        {
            "site_state": "TX",
            "requested_date": "2026-05-20",
            "vehicle_make": "Toyota",
            "vehicle_model": "LP",
            "fuel_type": "LP",
            "installation_type": "Telematics + display",
            "quantity": 1,
            "notes": "display mount",
        },
    )
    assert result["complexity"] == 1
    assert 30 <= result["estimated_minutes"] <= 60
    assert "longer display mounting screws" in result["required_materials"]
    assert result["ranked_installers"][0]["installer"]["name"] == "Carlos Martinez"


def test_bot_uses_english_knowledge_rules():
    conn = build_conn()
    result = answer_question(conn, "What should I bring for Toyota LP?")
    assert "longer display mounting screws" in result["answer"]
    assert "difficulty 1/5" in result["answer"]
    assert result["rules"]


def test_route_planner_consolidates_nearby_texas_work():
    conn = build_conn()
    plan = optimize_open_jobs(conn, start_date="2026-05-10", end_date="2026-05-16", state="TX", max_radius_miles=175)
    assert plan["summary"]["job_count"] == 3
    assert plan["summary"]["consolidated_groups"] >= 1
    first_group = plan["groups"][0]
    assert first_group["job_count"] == 3
    assert first_group["total_units"] == 12
    assert first_group["max_difficulty"] == 3
    assert first_group["recommended_team_size"] >= 2
    assert first_group["miles_saved"] > 0


def test_route_planner_flags_advanced_california_work():
    conn = build_conn()
    plan = optimize_open_jobs(conn, start_date="2026-05-10", end_date="2026-05-16", state="CA", max_radius_miles=175)
    assert plan["summary"]["job_count"] == 1
    first_group = plan["groups"][0]
    assert first_group["max_difficulty"] == 5
    assert first_group["recommended_team"][0]["name"] == "Priya Shah"


def test_required_info_gate_blocks_confirmation_until_complete():
    conn = build_conn()
    job = conn.execute("SELECT * FROM jobs WHERE po_number = 'PO-IL-4001'").fetchone()
    report = readiness_report(job)
    assert report["is_complete"] is False
    assert "Installation address" in report["missing_labels"]
    result = approve_recommendation(conn, int(job["id"]), approved_by="Test Coordinator")
    assert result["approved"] is False
    updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
    assert updated["status"] == "waiting_required_info"


def test_missing_info_reminders_are_queued_for_customer_sales_and_scheduling():
    conn = build_conn()
    job = conn.execute("SELECT * FROM jobs WHERE po_number = 'PO-IL-4001'").fetchone()
    result = queue_missing_info_reminders(conn, int(job["id"]))
    assert result["queued"] == 3
    count = conn.execute("SELECT COUNT(*) FROM notifications WHERE job_id = ?", (job["id"],)).fetchone()[0]
    assert count == 3


def test_completed_required_info_moves_to_pending_approval_then_confirmed():
    conn = build_conn()
    job_id = conn.execute("SELECT id FROM jobs WHERE po_number = 'PO-IL-4001'").fetchone()[0]
    conn.execute(
        """
        UPDATE jobs
        SET site_address = '900 Cold Storage Dr', site_zip = '60608', site_contact_name = 'Dock Lead',
            site_contact_phone = '+1 312 555 0999', vehicle_voltage = '12V', vehicle_asset_ids = 'TY-IL-001 to TY-IL-006',
            shipment_tracking_number = '1ZIL4001', order_ready = 1
        WHERE id = ?
        """,
        (job_id,),
    )
    refresh_job_recommendation(conn, int(job_id))
    report = refresh_job_readiness(conn, int(job_id))
    assert report["is_complete"] is True
    updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert updated["status"] == "pending_approval"
    result = approve_recommendation(conn, int(job_id), approved_by="Test Coordinator")
    assert result["approved"] is True
    confirmed = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert confirmed["status"] == "confirmed"
    assert confirmed["assigned_installer_id"] is not None


def test_schedule_recommender_respects_customer_date_window():
    conn = build_conn()
    from app.services.scheduling import recommend_schedule_options

    job = conn.execute("SELECT * FROM jobs WHERE po_number = 'PO-IL-4001'").fetchone()
    job_data = {key: job[key] for key in job.keys()}
    job_data["requested_date_type"] = "range"
    job_data["requested_date"] = "2026-05-18"
    job_data["requested_end_date"] = "2026-05-22"
    recommendation = recommend_for_job(conn, job_data)
    plan = recommend_schedule_options(conn, job_data, recommendation)

    assert plan["options"]
    best_date = plan["best_option"]["date"]
    assert "2026-05-18" <= best_date <= "2026-05-22"
    assert plan["best_option"]["installer_id"] is not None


def test_refresh_job_recommendation_stores_schedule_options():
    conn = build_conn()
    job_id = conn.execute("SELECT id FROM jobs WHERE po_number = 'PO-IL-4001'").fetchone()[0]
    refresh_job_recommendation(conn, int(job_id))
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["suggested_scheduled_start"]
    assert job["suggested_schedule_reason"]
    assert "options" in job["suggested_schedule_options"]
