# Telemetry Field Operations Planner

An English-language MVP for planning industrial telematics installations across the United States.

The system is designed around a full operational workflow:

1. A PO is received from a CRM such as HubSpot, or entered manually.
2. The system creates an installation job if the PO requires field installation by the internal team.
3. The job remains blocked until mandatory customer/dealer, site, voltage, vehicle, and production tracking information is complete.
4. The system generates AI-assisted recommendations for technician selection, route grouping, materials, estimated time, and installation date/time.
5. A coordinator reviews and approves the recommendation before the job appears on the confirmed calendar.
6. The system queues customer/dealer and internal email reminders when required information is missing.

This is a local MVP. It does not yet send real emails, book flights, call live map APIs, or connect to HubSpot credentials. The outbox and integrations are structured so those services can be connected later.

## Deployment Shape

- `app/` contains the FastAPI application, Jinja templates, static assets, services, and API routes.
- `data/` is the runtime SQLite directory. Local database files are intentionally ignored by Git.
- `tests/` contains the recommendation, scheduling, readiness, route planning, and notification checks.
- `docs/` contains the static GitHub Pages demo for the public repository site.
- `.github/workflows/pages.yml` publishes `docs/` to GitHub Pages on pushes to `main`.

GitHub Pages can host the static demo in `docs/`, but it cannot run the Python/FastAPI backend. Use the Quick Start or Docker commands below for the full interactive backend.

## GitHub Pages Demo

The repository is prepared for:

```text
https://github.com/ReynelPena/telemetry-field-operations-planner-demo.git
```

After pushing to `main`, enable GitHub Pages with **Build and deployment: GitHub Actions**. The public demo URL will be:

```text
https://reynelpena.github.io/telemetry-field-operations-planner-demo/
```

The static demo entry point is:

```text
docs/index.html
```

Initial repository commands:

```bash
git init
git branch -M main
git remote add origin https://github.com/ReynelPena/telemetry-field-operations-planner-demo.git
git add .
git commit -m "Prepare GitHub Pages demo"
git push -u origin main
```

## Main Modules

- **Operations Dashboard**: confirmed jobs, jobs waiting for required information, pending approvals, route opportunities, and queued reminders.
- **Intake Queue**: jobs that cannot yet be confirmed.
- **Confirmed Calendar**: only confirmed jobs appear on the technician calendar.
- **Route Planner**: groups nearby jobs and recommends team deployment strategies.
- **Schedule Recommendation Engine**: recommends installation dates and time slots using customer requested dates/ranges, technician availability, current confirmed schedules, workload, route opportunities, and skill fit.
- **Jobs**: complete PO/work order records with readiness status.
- **Customer/Dealer Portal**: tokenized link to collect missing site, contact, voltage, and vehicle information.
- **Email Outbox**: simulated email queue for reminders and confirmations.
- **Integrations**: example CRM PO intake and production tracking endpoints.
- **Installers**: technician location, coverage, skill tags, and max difficulty.
- **Vehicles**: difficulty, time, and technical notes by model.
- **Knowledge Base**: rules for tools, materials, checklists, and installation risks.
- **Technical Bot**: answers field questions using the knowledge base.

## Mandatory Fields Before Confirmation

A job cannot be confirmed or placed on the technician calendar until these fields are complete:

- Purchase order number
- Customer name
- Customer or dealer email
- Sales rep email
- Requested installation date or scheduling window start
- Installation address
- Installation city
- Installation state
- Installation ZIP code
- On-site contact name
- On-site contact phone
- Vehicle make
- Vehicle model
- Fuel / power type
- Vehicle voltage
- Vehicle serial numbers or asset IDs
- Unit quantity
- Shipment tracking number

The system may still generate recommendations while information is missing, but it will not confirm the technician assignment until the required fields are complete and the coordinator approves. If the customer chooses a date range, the window end date is also required before confirmation.

## Installation Date Recommendation

The customer or dealer can provide one of three scheduling preferences:

- **Exact preferred date**: the system first tries the requested date, then suggests nearby alternatives if availability or routing makes the exact date inefficient.
- **Date range / scheduling window**: the system searches inside the requested start and end dates.
- **Flexible after start date**: the system searches from the start date through the flexible window.

For each feasible option, the system considers:

- technician availability and existing confirmed jobs
- technician coverage state, skill tags, and max difficulty
- estimated labor time and unit quantity
- customer date preference
- nearby same-state, same-city, and same-customer route opportunities
- workload conflicts and calendar capacity

The job detail screen shows ranked schedule options. The coordinator can approve the top option or manually override the installer/date/time before confirmation. Confirmed jobs appear on the calendar by the approved scheduled date, not just by the customer's requested date.

## Toyota LP Rule

Toyota LP is configured as a low-difficulty installation:

- Difficulty: 1/5
- Average estimated labor: 45 minutes per unit
- Practical range: 30-60 minutes depending on installer speed and site readiness
- Recommended material: longer display mounting screws for the screen/display mount

## Quick Start - Windows PowerShell

From the folder that contains `requirements.txt`:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

Do not use `Activate.ps1` if PowerShell blocks scripts. The commands above use the virtual environment directly.

## Quick Start - macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## Docker

```bash
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

## CRM PO Intake API

Use this endpoint when a CRM deal or PO requires installation by your team:

```text
POST /api/crm/hubspot/po
```

Example JSON:

```json
{
  "source_system": "HubSpot",
  "hubspot_deal_id": "123456789",
  "po_number": "PO-12345",
  "customer_name": "Example Fleet",
  "customer_contact_email": "customer@example.com",
  "sales_rep_email": "sales@example.com",
  "installation_required": true,
  "requested_date": "2026-06-01",
  "requested_date_type": "range",
  "requested_end_date": "2026-06-05",
  "date_flexibility_notes": "Customer prefers morning appointments.",
  "site_city": "Dallas",
  "site_state": "TX",
  "vehicle_make": "Toyota",
  "vehicle_model": "LP",
  "fuel_type": "LP",
  "quantity": 10
}
```

The response includes:

- `job_id`
- current status
- missing required fields
- customer/dealer portal URL

## Schedule Recommendation API

Use this endpoint to retrieve fresh schedule recommendations for a job:

```text
GET /api/jobs/{job_id}/schedule-options
```

The response includes:

- customer requested scheduling window
- ranked installation date/time options
- recommended installer per option
- score, reasons, warnings, workload count, and route matches

## Production Tracking API

Use this endpoint when the production team has prepared the order and tracking is available:

```text
POST /api/production/tracking
```

Example JSON:

```json
{
  "po_number": "PO-12345",
  "shipment_carrier": "UPS",
  "shipment_tracking_number": "1Z999999999",
  "order_ready": true
}
```

After tracking is saved, the job is re-evaluated. If all required information is complete, it moves to `Pending Approval`.

## Status Definitions

- `po_received`: PO exists but has not been fully processed.
- `waiting_required_info`: mandatory information is missing; the job cannot be confirmed.
- `pending_approval`: required information is complete and the coordinator must approve the AI recommendation.
- `confirmed`: coordinator approved; job appears on the confirmed calendar.
- `in_progress`: job is actively being performed.
- `completed`: job completed.
- `cancelled`: job cancelled.
- `no_installation_required`: PO does not require field installation by the internal team.

## Environment Variables

Optional settings:

```text
TIS_DB_PATH=/custom/path/telemetry_scheduler.db
PUBLIC_BASE_URL=https://scheduler.example.com
SCHEDULING_TEAM_EMAIL=installation-scheduling@example.com
```

## Tests

```bash
pytest -q
```

Expected result:

```text
9 passed
```

## Production Next Steps

Recommended upgrades before production use:

- Real authentication and roles for admin, coordinator, production, sales, installer, customer/dealer.
- Real email sending through SendGrid, Microsoft 365, Gmail, HubSpot, or similar.
- HubSpot OAuth/webhook integration.
- Google Maps or Mapbox for live routing and drive-time estimates.
- Flight, hotel, rental car, and per-diem cost engine.
- Installer mobile/PWA view with photos, signatures, checklists, and job completion evidence.
- Cloud database and backups.
- Audit trail for approvals and customer/dealer submissions.
