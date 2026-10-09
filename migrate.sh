#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# migrate.sh — backup the database then run Flask-Migrate for v3.0
#
# Usage:
#   ./migrate.sh            # reads creds from vars/vars.json or env vars
#   DRY_RUN=1 ./migrate.sh  # print commands without executing them
#
# Must be run from the project root (same directory as main.py).
# ---------------------------------------------------------------------------
set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()     { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

DRY_RUN="${DRY_RUN:-0}"
run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        echo -e "${YELLOW}[DRY-RUN]${RESET} $*"
    else
        eval "$@"
    fi
}

# ── Always operate on this script's own directory ──────────────────────────
# Guards against running it from the wrong checkout, which silently migrates one
# database while systemd serves a different directory.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Sanity checks ──────────────────────────────────────────────────────────
[[ -f "main.py" ]] || die "main.py not found next to this script — is this the project root?"

# ── Activate the virtualenv ourselves ──────────────────────────────────────
# Debian has no bare `python`, so without the venv the checks below fail with a
# confusing "'python' not found". Activate it here instead of requiring callers
# to remember. Respects an already-active venv.
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    if [[ -f "venv/bin/activate" ]]; then
        info "Activating virtualenv at ./venv"
        # shellcheck disable=SC1091
        source venv/bin/activate
    else
        warn "No ./venv found — using whatever python/flask are on PATH."
    fi
fi

command -v mysqldump >/dev/null 2>&1 || die "'mysqldump' not found. Install mysql-client and try again."
command -v python    >/dev/null 2>&1 || die "'python' not found, even after activating ./venv."
command -v flask     >/dev/null 2>&1 || die "'flask' not found. Is ./venv set up (pip install -r requirements.txt)?"

# ── Read credentials ───────────────────────────────────────────────────────
# Priority: env vars > vars/vars.json
info "Reading database credentials..."

DB_USER="${DB_USERNAME:-}"
DB_PASS="${DB_PASSWORD:-}"
DB_NAME_VAL="${DB_NAME:-}"

if [[ -z "$DB_USER" || -z "$DB_PASS" || -z "$DB_NAME_VAL" ]]; then
    [[ -f "vars/vars.json" ]] || die "No env vars set and vars/vars.json not found."

    # Use Python (already a dependency) to parse the JSON
    _json_get() {
        python -c "import json,sys; d=json.load(open('vars/vars.json')); print(d.get('$1',''))"
    }

    [[ -z "$DB_USER"     ]] && DB_USER="$(_json_get db_username)"
    [[ -z "$DB_PASS"     ]] && DB_PASS="$(_json_get db_password)"
    [[ -z "$DB_NAME_VAL" ]] && DB_NAME_VAL="$(_json_get database)"
fi

[[ -n "$DB_USER"     ]] || die "Could not determine DB_USERNAME."
[[ -n "$DB_PASS"     ]] || die "Could not determine DB_PASSWORD."
[[ -n "$DB_NAME_VAL" ]] || die "Could not determine DB_NAME."

info "Database : ${BOLD}${DB_NAME_VAL}${RESET}  User: ${BOLD}${DB_USER}${RESET}"

# ── Backup ─────────────────────────────────────────────────────────────────
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="vars"
BACKUP_FILE="${BACKUP_DIR}/booking_dump_${TIMESTAMP}.sql"

echo ""
info "Step 1/4 — Dumping database to ${BOLD}${BACKUP_FILE}${RESET}"

run mysqldump \
    --user="$DB_USER" \
    --password="$DB_PASS" \
    --single-transaction \
    --routines \
    --triggers \
    --add-drop-table \
    "$DB_NAME_VAL" \> "$BACKUP_FILE"

if [[ "$DRY_RUN" != "1" ]]; then
    [[ -s "$BACKUP_FILE" ]] || die "Dump file is empty — aborting before migration."
    DUMP_SIZE="$(du -sh "$BACKUP_FILE" | cut -f1)"
    success "Backup saved: ${BACKUP_FILE} (${DUMP_SIZE})"
fi

# ── Install dependencies ───────────────────────────────────────────────────
echo ""
info "Step 2/4 — Installing/updating Python dependencies (pip install -r requirements.txt)"

run pip install -q -r requirements.txt

if [[ "$DRY_RUN" != "1" ]]; then
    success "Dependencies up to date."
fi

# ── flask db upgrade (apply any pending migrations from repo) ───────────────
echo ""
info "Step 3/4 — Applying any pending migrations (flask db upgrade)"

export FLASK_APP=main.py

run flask db upgrade

if [[ "$DRY_RUN" != "1" ]]; then
    success "Database at current head."
fi

# ── Detect drift (report only — never generate migrations here) ─────────────
# This step used to run `flask db migrate`, which GENERATES a migration file on
# whichever machine the script runs on. On a server that file is untracked, so
# the database ends up on a revision that exists nowhere in git — which is how
# 161eadb6a073 came about and left Alembic with two heads mid-deploy.
# Migrations are now generated in development, committed, and only applied here.
echo ""
info "Step 4/4 — Checking for model/schema drift (report only)"

if [[ "$DRY_RUN" == "1" ]]; then
    echo -e "${YELLOW}[DRY-RUN]${RESET} flask db check"
else
    if flask db check >/dev/null 2>&1; then
        success "Schema matches the models."
    else
        warn "The models and the database schema differ."
        warn "Generate the migration in DEVELOPMENT, commit it, then redeploy:"
        warn "    flask db migrate -m 'describe the change'   # on your machine"
        warn "    git add migrations/versions/ && git commit && git push"
        warn "Not generating anything here — that is what created the two-head problem."
    fi
fi

# ── Fix NULL is_bookable values ─────────────────────────────────────────────
echo ""
info "Fixing NULL is_bookable values on existing items..."

run python -c "
from main import app, db
from sqlalchemy import text
with app.app_context():
    with db.engine.connect() as conn:
        result = conn.execute(text('UPDATE item SET is_bookable=1 WHERE is_bookable IS NULL'))
        conn.commit()
        print(f'  Updated {result.rowcount} row(s).')
"

if [[ "$DRY_RUN" != "1" ]]; then
    success "NULL is_bookable values fixed."
fi

# ── Seed default locations ─────────────────────────────────────────────────
echo ""
info "Seeding default locations (A, B, C) if not already present..."

run python -c '"from main import app, create_default_locations; create_default_locations()"'

# ── Seed admin notification contacts ───────────────────────────────────────
echo ""
info "Seeding admin notification contacts (previously hardcoded in main.py)..."

run python -c '"from main import seed_admin_notification_contacts; seed_admin_notification_contacts()"'

if [[ "$DRY_RUN" != "1" ]]; then
    success "Admin notification contacts seeded."
fi

if [[ "$DRY_RUN" != "1" ]]; then
    success "Locations seeded."
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Migrations done.${RESET}"
echo ""
# The most common deploy mistake: migrating but never restarting, so gunicorn
# keeps serving the old code and the deploy looks like it did nothing.
if [[ "${CALLED_FROM_DEPLOY:-0}" != "1" ]]; then
    warn "migrate.sh does NOT restart the service — the new code is NOT live yet."
    warn "Run ${BOLD}./deploy.sh${RESET} to migrate AND restart in one step, or restart now with:"
    warn "    sudo systemctl restart booking.service"
    echo ""
fi
echo -e "  Backup : ${BACKUP_FILE}"
echo -e "  To roll back the schema: ${YELLOW}flask db downgrade${RESET}"
echo -e "  To restore data:         ${YELLOW}mysql -u $DB_USER -p $DB_NAME_VAL < $BACKUP_FILE${RESET}"
