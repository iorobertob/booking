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

# ── Sanity checks ──────────────────────────────────────────────────────────
[[ -f "main.py" ]] || die "Run this script from the project root (main.py not found)."

command -v mysqldump >/dev/null 2>&1 || die "'mysqldump' not found. Install mysql-client and try again."
command -v python    >/dev/null 2>&1 || die "'python' not found."
command -v flask     >/dev/null 2>&1 || die "'flask' not found. Activate your virtualenv first."

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
info "Step 1/3 — Dumping database to ${BOLD}${BACKUP_FILE}${RESET}"

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

# ── flask db migrate ────────────────────────────────────────────────────────
echo ""
info "Step 2/3 — Generating migration script (flask db migrate)"

export FLASK_APP=main.py

run flask db migrate -m '"add booking_items and location tables"'

if [[ "$DRY_RUN" != "1" ]]; then
    success "Migration script generated."
fi

# ── flask db upgrade ────────────────────────────────────────────────────────
echo ""
info "Step 3/3 — Applying migration (flask db upgrade)"

run flask db upgrade

if [[ "$DRY_RUN" != "1" ]]; then
    success "Database upgraded."
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

if [[ "$DRY_RUN" != "1" ]]; then
    success "Locations seeded."
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}All done.${RESET}"
echo -e "  Backup : ${BACKUP_FILE}"
echo -e "  To roll back the schema: ${YELLOW}flask db downgrade${RESET}"
echo -e "  To restore data:         ${YELLOW}mysql -u $DB_USER -p $DB_NAME_VAL < $BACKUP_FILE${RESET}"
