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
DRY_RUN="$DRY_RUN" ./migrate.sh

# ── 3. Restart the service ─────────────────────────────────────────────────
if [[ "$DO_RESTART" == "1" ]]; then
    echo ""
    info "Restarting ${BOLD}${SERVICE}${RESET}..."
    if command -v systemctl >/dev/null 2>&1; then
        run sudo systemctl restart "$SERVICE"
        if [[ "$DRY_RUN" != "1" ]]; then
            sleep 2
            if systemctl is-active --quiet "$SERVICE"; then
                success "${SERVICE} is active."
            else
                die "${SERVICE} did not come back up. Check: journalctl -u ${SERVICE} -n 50"
            fi
        fi
    else
        warn "systemctl not available — restart the app yourself."
    fi
else
    warn "Skipping restart (--no-restart). The new code is NOT live yet."
fi

echo ""
echo -e "${GREEN}${BOLD}Deploy complete.${RESET}"
echo -e "  Hard-refresh the browser (Cmd+Shift+R) so updated CSS/JS load."
