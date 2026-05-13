from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("TIS_DB_PATH", DATA_DIR / "telemetry_scheduler.db"))


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS installers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            home_city TEXT,
            home_state TEXT NOT NULL,
            coverage_states TEXT NOT NULL DEFAULT '',
            skill_tags TEXT NOT NULL DEFAULT '',
            max_complexity INTEGER NOT NULL DEFAULT 3,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            make TEXT NOT NULL,
            model TEXT NOT NULL,
            fuel_type TEXT NOT NULL DEFAULT '',
            complexity INTEGER NOT NULL DEFAULT 3,
            estimated_minutes INTEGER NOT NULL DEFAULT 120,
            skill_tags TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS knowledge_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            triggers TEXT NOT NULL DEFAULT '',
            vehicle_make TEXT NOT NULL DEFAULT '',
            vehicle_model TEXT NOT NULL DEFAULT '',
            fuel_type TEXT NOT NULL DEFAULT '',
            complexity_adjustment INTEGER NOT NULL DEFAULT 0,
            required_tools TEXT NOT NULL DEFAULT '',
            required_materials TEXT NOT NULL DEFAULT '',
            checklist TEXT NOT NULL DEFAULT '',
            risk_notes TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            customer_type TEXT NOT NULL DEFAULT 'Customer',
            customer_contact_name TEXT NOT NULL DEFAULT '',
            customer_contact_email TEXT NOT NULL DEFAULT '',
            customer_contact_phone TEXT NOT NULL DEFAULT '',
            dealer_name TEXT NOT NULL DEFAULT '',
            dealer_contact_name TEXT NOT NULL DEFAULT '',
            dealer_contact_email TEXT NOT NULL DEFAULT '',
            dealer_contact_phone TEXT NOT NULL DEFAULT '',
            sales_rep_name TEXT NOT NULL DEFAULT '',
            sales_rep_email TEXT NOT NULL DEFAULT '',
            po_number TEXT NOT NULL DEFAULT '',
            hubspot_deal_id TEXT NOT NULL DEFAULT '',
            installation_required INTEGER NOT NULL DEFAULT 1,
            order_ready INTEGER NOT NULL DEFAULT 0,
            shipment_carrier TEXT NOT NULL DEFAULT '',
            shipment_tracking_number TEXT NOT NULL DEFAULT '',
            portal_token TEXT UNIQUE,
            site_address TEXT NOT NULL DEFAULT '',
            site_city TEXT NOT NULL DEFAULT '',
            site_state TEXT NOT NULL DEFAULT '',
            site_zip TEXT NOT NULL DEFAULT '',
            site_contact_name TEXT NOT NULL DEFAULT '',
            site_contact_email TEXT NOT NULL DEFAULT '',
            site_contact_phone TEXT NOT NULL DEFAULT '',
            site_access_hours TEXT NOT NULL DEFAULT '',
            site_access_notes TEXT NOT NULL DEFAULT '',
            requested_date TEXT NOT NULL,
            requested_date_type TEXT NOT NULL DEFAULT 'exact',
            requested_end_date TEXT NOT NULL DEFAULT '',
            date_flexibility_notes TEXT NOT NULL DEFAULT '',
            scheduled_start TEXT,
            scheduled_end TEXT,
            vehicle_make TEXT NOT NULL DEFAULT '',
            vehicle_model TEXT NOT NULL DEFAULT '',
            fuel_type TEXT NOT NULL DEFAULT '',
            vehicle_voltage TEXT NOT NULL DEFAULT '',
            vehicle_asset_ids TEXT NOT NULL DEFAULT '',
            vehicle_year TEXT NOT NULL DEFAULT '',
            installation_type TEXT NOT NULL DEFAULT 'Telematics',
            quantity INTEGER NOT NULL DEFAULT 1,
            complexity INTEGER NOT NULL DEFAULT 3,
            estimated_minutes INTEGER NOT NULL DEFAULT 120,
            required_tools TEXT NOT NULL DEFAULT '',
            required_materials TEXT NOT NULL DEFAULT '',
            checklist TEXT NOT NULL DEFAULT '',
            risk_notes TEXT NOT NULL DEFAULT '',
            assigned_installer_id INTEGER REFERENCES installers(id) ON DELETE SET NULL,
            suggested_installer_id INTEGER REFERENCES installers(id) ON DELETE SET NULL,
            suggested_scheduled_start TEXT,
            suggested_scheduled_end TEXT,
            suggested_schedule_reason TEXT NOT NULL DEFAULT '',
            suggested_schedule_options TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'po_received',
            info_completion_percent INTEGER NOT NULL DEFAULT 0,
            missing_required_fields TEXT NOT NULL DEFAULT '[]',
            required_info_completed_at TEXT,
            ai_recommendation_summary TEXT NOT NULL DEFAULT '',
            ai_recommendation_generated_at TEXT,
            coordinator_approved_at TEXT,
            coordinator_approved_by TEXT NOT NULL DEFAULT '',
            last_customer_reminder_at TEXT,
            last_internal_reminder_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
            recipient_type TEXT NOT NULL DEFAULT '',
            recipient_email TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL DEFAULT 'email',
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT
        );

        CREATE TABLE IF NOT EXISTS integration_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system TEXT NOT NULL DEFAULT '',
            external_id TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL DEFAULT '',
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            payload TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'received',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_requested_date ON jobs(requested_date);
        CREATE INDEX IF NOT EXISTS idx_jobs_suggested_schedule ON jobs(suggested_scheduled_start);
        CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(site_state);
        CREATE INDEX IF NOT EXISTS idx_jobs_installer ON jobs(assigned_installer_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_po ON jobs(po_number);
        CREATE INDEX IF NOT EXISTS idx_jobs_portal_token ON jobs(portal_token);
        CREATE INDEX IF NOT EXISTS idx_installers_state ON installers(home_state);
        CREATE INDEX IF NOT EXISTS idx_vehicles_lookup ON vehicles(make, model, fuel_type);
        CREATE INDEX IF NOT EXISTS idx_notifications_job ON notifications(job_id);
        CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status);
        """
    )


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in existing_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def migrate_db(conn: sqlite3.Connection) -> None:
    job_columns = {
        "customer_type": "customer_type TEXT NOT NULL DEFAULT 'Customer'",
        "customer_contact_name": "customer_contact_name TEXT NOT NULL DEFAULT ''",
        "customer_contact_email": "customer_contact_email TEXT NOT NULL DEFAULT ''",
        "customer_contact_phone": "customer_contact_phone TEXT NOT NULL DEFAULT ''",
        "dealer_name": "dealer_name TEXT NOT NULL DEFAULT ''",
        "dealer_contact_name": "dealer_contact_name TEXT NOT NULL DEFAULT ''",
        "dealer_contact_email": "dealer_contact_email TEXT NOT NULL DEFAULT ''",
        "dealer_contact_phone": "dealer_contact_phone TEXT NOT NULL DEFAULT ''",
        "sales_rep_name": "sales_rep_name TEXT NOT NULL DEFAULT ''",
        "sales_rep_email": "sales_rep_email TEXT NOT NULL DEFAULT ''",
        "po_number": "po_number TEXT NOT NULL DEFAULT ''",
        "hubspot_deal_id": "hubspot_deal_id TEXT NOT NULL DEFAULT ''",
        "installation_required": "installation_required INTEGER NOT NULL DEFAULT 1",
        "order_ready": "order_ready INTEGER NOT NULL DEFAULT 0",
        "shipment_carrier": "shipment_carrier TEXT NOT NULL DEFAULT ''",
        "shipment_tracking_number": "shipment_tracking_number TEXT NOT NULL DEFAULT ''",
        "portal_token": "portal_token TEXT",
        "requested_date_type": "requested_date_type TEXT NOT NULL DEFAULT 'exact'",
        "requested_end_date": "requested_end_date TEXT NOT NULL DEFAULT ''",
        "date_flexibility_notes": "date_flexibility_notes TEXT NOT NULL DEFAULT ''",
        "site_zip": "site_zip TEXT NOT NULL DEFAULT ''",
        "site_contact_name": "site_contact_name TEXT NOT NULL DEFAULT ''",
        "site_contact_email": "site_contact_email TEXT NOT NULL DEFAULT ''",
        "site_contact_phone": "site_contact_phone TEXT NOT NULL DEFAULT ''",
        "site_access_hours": "site_access_hours TEXT NOT NULL DEFAULT ''",
        "site_access_notes": "site_access_notes TEXT NOT NULL DEFAULT ''",
        "vehicle_voltage": "vehicle_voltage TEXT NOT NULL DEFAULT ''",
        "vehicle_asset_ids": "vehicle_asset_ids TEXT NOT NULL DEFAULT ''",
        "vehicle_year": "vehicle_year TEXT NOT NULL DEFAULT ''",
        "suggested_installer_id": "suggested_installer_id INTEGER REFERENCES installers(id) ON DELETE SET NULL",
        "suggested_scheduled_start": "suggested_scheduled_start TEXT",
        "suggested_scheduled_end": "suggested_scheduled_end TEXT",
        "suggested_schedule_reason": "suggested_schedule_reason TEXT NOT NULL DEFAULT ''",
        "suggested_schedule_options": "suggested_schedule_options TEXT NOT NULL DEFAULT '[]'",
        "info_completion_percent": "info_completion_percent INTEGER NOT NULL DEFAULT 0",
        "missing_required_fields": "missing_required_fields TEXT NOT NULL DEFAULT '[]'",
        "required_info_completed_at": "required_info_completed_at TEXT",
        "ai_recommendation_summary": "ai_recommendation_summary TEXT NOT NULL DEFAULT ''",
        "ai_recommendation_generated_at": "ai_recommendation_generated_at TEXT",
        "coordinator_approved_at": "coordinator_approved_at TEXT",
        "coordinator_approved_by": "coordinator_approved_by TEXT NOT NULL DEFAULT ''",
        "last_customer_reminder_at": "last_customer_reminder_at TEXT",
        "last_internal_reminder_at": "last_internal_reminder_at TEXT",
    }
    if "jobs" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
        for column, ddl in job_columns.items():
            add_column_if_missing(conn, "jobs", column, ddl)
        conn.execute("UPDATE jobs SET portal_token = lower(hex(randomblob(16))) WHERE portal_token IS NULL OR portal_token = ''")


def init_db() -> None:
    with get_connection() as conn:
        create_schema(conn)
        migrate_db(conn)
        conn.commit()


def seed_if_empty() -> None:
    from app.seed import seed_database

    with get_connection() as conn:
        installer_count = conn.execute("SELECT COUNT(*) FROM installers").fetchone()[0]
        vehicle_count = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        rule_count = conn.execute("SELECT COUNT(*) FROM knowledge_rules").fetchone()[0]
        if installer_count == 0 and vehicle_count == 0 and rule_count == 0:
            seed_database(conn)


def csv_to_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def list_to_csv(items: Iterable[str]) -> str:
    return ", ".join(dict.fromkeys([item.strip() for item in items if item and item.strip()]))
