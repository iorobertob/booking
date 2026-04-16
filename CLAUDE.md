# CLAUDE.md — MISC Booking System

## Purpose
Flask equipment booking system for MISC/LMTA. Manages item reservations, lending, and returns with email notifications and MS Azure SSO. Live at `https://misc.lmta.lt/booking`.

## Tech Stack
- **Backend**: Python/Flask — monolithic, everything in `main.py`
- **ORM**: SQLAlchemy (MySQL) + Flask-Migrate (Alembic)
- **Auth**: Flask-Login + MSAL (MS Azure OAuth2)
- **Email**: MailerSend API
- **Frontend**: Bootstrap 5 + Tailwind CDN + jQuery; DataTables; FullCalendar; Flatpickr
- **Scheduler**: APScheduler (daily return reminders, production only)

## Key Directories
```
main.py              — all models, routes, helpers (~1280 lines)
templates/           — Jinja2 templates; email_*.html for emails; macros.html for reusable modals
static/js/           — item_detail_modals.js (only JS file; handles calendar, date pickers, form submission)
static/css/          — booking.css, nav.css
vars/vars.json       — credentials fallback (gitignored on server; present in dev)
migrations/          — Alembic migration scripts (generated, do not hand-edit)
unit_tests.py        — minimal test suite
migrate.sh           — backup DB + run migrations in one step
```

## Models (all in `main.py`)
- `Booking` — one per booking session; `main.py:108`
- `BookingItem` — one per item in a group booking (v3+); `main.py:148`
- `Item` — equipment item; `main.py:139`
- `Location` — managed location list; `main.py:170`
- `User` — local and SSO accounts; `main.py:176`

See `README.md#models` for full column reference.

## Essential Commands

```bash
# Development (no scheduler, port 5001)
MISC_DEV=true python main.py

# Run tests
python unit_tests.py

# DB migration (after model changes)
export FLASK_APP=main.py
flask db migrate -m "description"
flask db upgrade

# Backup DB + migrate in one step (preferred on server)
./migrate.sh
DRY_RUN=1 ./migrate.sh   # preview only

# Restart production service
sudo systemctl restart booking.service
```

## Credentials
Read via `_cfg()` helper (`main.py:48`): env vars first, then `vars/vars.json`. Copy `.env.example` to `.env` for local dev. Key env vars: `SECRET_KEY`, `DB_USERNAME`, `DB_PASSWORD`, `DB_NAME`, `MAILERSEND_API_KEY`, `AZURE_CLIENT_ID/SECRET/TENANT_ID`, `ADMIN_EMAILS`, `MISC_DEV`.

## Tests
`unit_tests.py` — run with `python unit_tests.py`. No test framework beyond stdlib `unittest`. Tests are minimal; verify critical paths manually when touching `book`, `is_item_available`, or auth flows.

## Documentation Rule
**All new features, route changes, and model changes must be documented in `README.md`** — update the relevant section (Models, Routes Reference, Booking Flow, Changelog) as part of the same commit.

## Additional Documentation
Check these when working on the relevant areas:

| Topic | File |
|-------|------|
| Architectural patterns, design decisions, conventions | `.claude/docs/architectural_patterns.md` |
| Full route reference and booking flow | `README.md#routes-reference` |
| Model column reference | `README.md#models` |
| Email templates and triggers | `README.md#email-notifications` |
| Dark mode implementation | `README.md#dark-mode` |
| DB migration history and migrate.sh usage | `README.md#db-migrations` |
| Deployment and scheduler | `README.md#production-deployment` |


