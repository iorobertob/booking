#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy.sh — one command to deploy the booking system on the server.
#
#   ./deploy.sh              # migrate + restart (run this after `git pull`)
#   ./deploy.sh --pull       # pull first, then migrate + restart
#   ./deploy.sh --no-restart # everything except restarting the service
#   DRY_RUN=1 ./deploy.sh    # show what would happen, change nothing
#
# Handles the venv itself — no `source venv/bin/activate` needed.
# Runs from anywhere: it always operates on its own directory.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[DEPLOY]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}     $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}   $*"; }
die()     { echo -e "${RED}[ERROR]${RESET}  $*" >&2; exit 1; }

DRY_RUN="${DRY_RUN:-0}"
SERVICE="${SERVICE_NAME:-booking.service}"
DO_PULL=0
DO_RESTART=1
for arg in "$@"; do
    case "$arg" in
        --pull)       DO_PULL=1 ;;
        --no-restart) DO_RESTART=0 ;;
        -h|--help)    sed -n '2,13p' "$0"; exit 0 ;;
        *)            die "Unknown option: $arg (try --help)" ;;
    esac
done

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        echo -e "${YELLOW}[DRY-RUN]${RESET} $*"
    else
        eval "$@"
    fi
}

[[ -f "main.py" ]] || die "main.py not found next to this script — wrong directory?"
info "Deploying from ${BOLD}$(pwd)${RESET}"

# ── 1. Code ────────────────────────────────────────────────────────────────
if command -v git >/dev/null 2>&1 && [[ -d .git ]]; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    info "On branch ${BOLD}${BRANCH}${RESET}"
    [[ "$BRANCH" == "main" ]] || warn "Not on 'main' — deploying '${BRANCH}'. Is that intended?"

    if [[ "$DO_PULL" == "1" ]]; then
        info "Pulling latest changes..."
        run git pull
    else
        # Don't pull unasked, but say so if the checkout is stale.
        git fetch --quiet origin "$BRANCH" 2>/dev/null || true
        BEHIND="$(git rev-list --count "HEAD..origin/${BRANCH}" 2>/dev/null || echo 0)"
        if [[ "$BEHIND" != "0" ]]; then
            warn "This checkout is ${BEHIND} commit(s) behind origin/${BRANCH}."
            warn "Run 'git pull' first, or use: ./deploy.sh --pull"
        fi
    fi

    if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
        warn "Working tree is not clean — deploying local modifications:"
        git status --short | sed 's/^/          /'
    fi
    info "Deploying commit ${BOLD}$(git rev-parse --short HEAD)${RESET} — $(git log -1 --pretty=%s)"
fi

# ── 2. Backup, dependencies, migrations, seeding ───────────────────────────
echo ""
info "Running migrate.sh (backup, dependencies, migrations, seeding)..."
[[ -x "./migrate.sh" ]] || die "./migrate.sh is missing or not executable."
# CALLED_FROM_DEPLOY silences migrate.sh's "you still need to restart" notice,
# since this script restarts for you a few lines down.
CALLED_FROM_DEPLOY=1 DRY_RUN="$DRY_RUN" ./migrate.sh

# ── 3. Restart the service ─────────────────────────────────────────────────
# Without this the new code is on disk but gunicorn keeps serving the old one,
# which looks exactly like "the deploy did nothing".
if [[ "$DO_RESTART" == "1" ]]; then
    echo ""
    info "Restarting ${BOLD}${SERVICE}${RESET}..."

    if ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl not found — restart the app yourself, or the new code will NOT be live."
    elif [[ "$DRY_RUN" == "1" ]]; then
        echo -e "${YELLOW}[DRY-RUN]${RESET} sudo systemctl restart ${SERVICE}"
    else
        # Remember when the unit last came up, so we can prove it actually
        # restarted. `is-active` alone would pass even if nothing happened.
        STARTED_BEFORE="$(systemctl show -p ActiveEnterTimestamp --value "$SERVICE" 2>/dev/null || true)"

        if ! sudo systemctl restart "$SERVICE"; then
            die "Restart failed. Run it yourself: sudo systemctl restart ${SERVICE}"
        fi

        sleep 2

        if ! systemctl is-active --quiet "$SERVICE"; then
            die "${SERVICE} is NOT running after the restart. Check: journalctl -u ${SERVICE} -n 50"
        fi

        STARTED_AFTER="$(systemctl show -p ActiveEnterTimestamp --value "$SERVICE" 2>/dev/null || true)"
        if [[ -n "$STARTED_BEFORE" && "$STARTED_BEFORE" == "$STARTED_AFTER" ]]; then
            warn "${SERVICE} is active, but its start time did not change —"
            warn "it may not actually have restarted. Check: systemctl status ${SERVICE}"
        else
            success "${SERVICE} restarted — the new code is live."
            [[ -n "$STARTED_AFTER" ]] && info "Running since ${STARTED_AFTER}"
        fi
    fi
else
    warn "Skipping restart (--no-restart)."
    warn "The new code is on disk but NOT live until you run: sudo systemctl restart ${SERVICE}"
fi

echo ""
echo -e "${GREEN}${BOLD}Deploy complete.${RESET}"
echo -e "  Hard-refresh the browser (Cmd+Shift+R) so updated CSS/JS load."
