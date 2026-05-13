from __future__ import annotations

import os
import sqlite3
from typing import Any

from app.services.workflow import readiness_report, row_to_dict

DEFAULT_SCHEDULING_EMAIL = os.getenv("SCHEDULING_TEAM_EMAIL", "installation-scheduling@example.com")
BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")


def create_notification(
    conn: sqlite3.Connection,
    job_id: int,
    recipient_type: str,
    recipient_email: str,
    subject: str,
    body: str,
    channel: str = "email",
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO notifications (job_id, recipient_type, recipient_email, subject, body, channel, status)
        VALUES (?, ?, ?, ?, ?, ?, 'queued')
        """,
        (job_id, recipient_type, recipient_email, subject, body, channel),
    )
    return int(cursor.lastrowid)


def build_missing_info_body(job: dict[str, Any], missing_labels: list[str], audience: str) -> str:
    portal_url = f"{BASE_URL}/portal/{job.get('portal_token')}"
    po = job.get("po_number") or f"Job #{job.get('id')}"
    missing = "\n".join(f"- {label}" for label in missing_labels)
    if audience == "customer":
        intro = "Your installation job has been received, but we cannot confirm technician scheduling until the required information below is completed."
    elif audience == "sales":
        intro = "This installation job is blocked from confirmation because required customer/dealer information is still missing."
    else:
        intro = "Internal scheduling reminder: this job is not ready to confirm."
    return f"""{intro}

PO / Reference: {po}
Customer: {job.get('customer_name') or ''}
Requested date: {job.get('requested_date') or ''}

Missing required information:
{missing or '- None'}

Customer/dealer completion link:
{portal_url}

Once this information is complete, the system will refresh the AI recommendation and move the job to Pending Coordinator Approval.
"""


def queue_missing_info_reminders(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job_row is None:
        raise ValueError(f"Job {job_id} not found")
    job = row_to_dict(job_row)
    report = readiness_report(job)
    missing_labels = report["missing_labels"]
    notifications: list[int] = []
    subject = f"Action required: installation information needed for {job.get('po_number') or 'job ' + str(job_id)}"

    customer_email = job.get("customer_contact_email") or job.get("dealer_contact_email")
    if customer_email:
        notifications.append(
            create_notification(conn, job_id, "customer_dealer", customer_email, subject, build_missing_info_body(job, missing_labels, "customer"))
        )
    if job.get("sales_rep_email"):
        notifications.append(
            create_notification(conn, job_id, "sales_rep", job["sales_rep_email"], subject, build_missing_info_body(job, missing_labels, "sales"))
        )
    notifications.append(
        create_notification(conn, job_id, "scheduling_team", DEFAULT_SCHEDULING_EMAIL, subject, build_missing_info_body(job, missing_labels, "scheduling"))
    )
    conn.execute(
        "UPDATE jobs SET last_customer_reminder_at = CURRENT_TIMESTAMP, last_internal_reminder_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (job_id,),
    )
    return {"queued": len(notifications), "notification_ids": notifications, "missing_labels": missing_labels}


def queue_confirmation_notice(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    job_row = conn.execute(
        """
        SELECT jobs.*, installers.name AS installer_name, installers.email AS installer_email
        FROM jobs
        LEFT JOIN installers ON installers.id = jobs.assigned_installer_id
        WHERE jobs.id = ?
        """,
        (job_id,),
    ).fetchone()
    if job_row is None:
        raise ValueError(f"Job {job_id} not found")
    job = row_to_dict(job_row)
    subject = f"Installation confirmed: {job.get('po_number') or 'Job #' + str(job_id)}"
    body = f"""The installation has been confirmed.

Customer: {job.get('customer_name')}
Location: {job.get('site_address')}, {job.get('site_city')}, {job.get('site_state')} {job.get('site_zip')}
Schedule: {job.get('scheduled_start')} to {job.get('scheduled_end')}
Assigned technician: {job.get('installer_name')}
Vehicle: {job.get('vehicle_make')} {job.get('vehicle_model')} {job.get('fuel_type')} - voltage {job.get('vehicle_voltage')}
Quantity: {job.get('quantity')}
Tracking: {job.get('shipment_tracking_number') or 'not provided'}
"""
    ids: list[int] = []
    for recipient_type, email in [
        ("customer_dealer", job.get("customer_contact_email") or job.get("dealer_contact_email")),
        ("sales_rep", job.get("sales_rep_email")),
        ("installer", job.get("installer_email")),
        ("scheduling_team", DEFAULT_SCHEDULING_EMAIL),
    ]:
        if email:
            ids.append(create_notification(conn, job_id, recipient_type, email, subject, body))
    return {"queued": len(ids), "notification_ids": ids}
