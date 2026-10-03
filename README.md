# MISC Booking System

Flask-based equipment booking system for the Music Innovations Studies Centre (MISC) at LMTA (Lithuanian Academy of Music and Theatre). Manages equipment reservations, lending, and returns with email notifications and MS Office 365 login.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Models](#models)
3. [Routes Reference](#routes-reference)
4. [Booking Flow](#booking-flow)
5. [Email Notifications](#email-notifications)
6. [Dark Mode](#dark-mode)
7. [Environment Setup](#environment-setup)
8. [DB Migrations](#db-migrations)
9. [Production Deployment](#production-deployment)
10. [Known Issues](#known-issues)
11. [TODO](#todo)
12. [Changelog](#changelog)

---

## Architecture Overview

- **Backend**: Python / Flask (monolithic, `main.py`)
- **Database**: MySQL via SQLAlchemy ORM + Flask-Migrate (Alembic)
- **Auth**: Flask-Login for session management; local accounts + MS Azure SSO via MSAL
- **Email**: MailerSend API (`mailersend` SDK)
- **Frontend**: Bootstrap 5 + Tailwind CDN + jQuery; DataTables for admin tables; FullCalendar for item booking calendars; Flatpickr for date pickers
- **Scheduler**: APScheduler background job for daily return reminders (production only)
- **Static assets**: `static/css/`, `static/js/`, `static/images/`
- **Templates**: Jinja2 in `templates/`; email templates in `templates/email_*.html`

### Configuration priority

Credentials are read in this order (first match wins):

1. Environment variables (e.g. `SECRET_KEY`, `DB_USERNAME`)
2. `vars/vars.json` (server-side file, gitignored)

---

## Models

All models are defined in `main.py` and use SQLAlchemy via the shared `db = SQLAlchemy(app)` instance.

---

### `Booking`

One record per booking session (single-item or group). For new group bookings, child `BookingItem` records hold the per-item details. For legacy single-item bookings (created before v3.0), there are no children.

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `item_id` | Integer FK → `item.id` | First item (backwards compat) |
| `item_name` | String(100) | First item name |
| `borrower_name` | String(100) | |
| `borrower_email` | String(100) | Borrower contact email |
| `user_email` | String(100) | Login account email (may differ) |
| `borrower_phone` | String(100) | |
| `borrow_date` | DateTime | First item's borrow date |
| `return_date` | DateTime | First item's return date |
| `created_at` | DateTime | When the request was submitted. NULL for bookings made before v3.4 |
| `status_changed_at` | DateTime | When the booking last moved along the lifecycle. NULL for bookings made before v3.4 |
| `status` | String(20) | Lifecycle: `'booked'` → `'approved'` → `'lent'` → `'returned'`, plus `'denied'`. Rows are **kept** at terminal statuses (`returned`/`denied`); only `ACTIVE_STATUSES` hold an item |
| `note` | String(300) | Optional booking note |

**Relationships**:
- `booking_items` → list of `BookingItem` (cascade delete). Empty for legacy rows.
- `item` → `Item`

**Distinguishing new vs legacy rows**: `len(booking.booking_items) > 0` means it's a v3+ group booking. Legacy rows have no children.

---

### `BookingItem`

One record per item within a group booking. Introduced in v3.0. Legacy `Booking` rows have no `BookingItem` children.

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `booking_id` | Integer FK → `booking.id` CASCADE | |
| `item_id` | Integer FK → `item.id` | Nullable (item may be deleted) |
| `item_name` | String(100) | Snapshot at booking time |
| `borrow_date` | DateTime | Per-item borrow date |
| `return_date` | DateTime | Per-item return date |

**Relationships**: `booking` → `Booking`; `item` → `Item`

**Duck-type properties** (delegate to parent `Booking` for backwards-compat template access):
`borrower_name`, `borrower_email`, `borrower_phone`, `status`, `note`

---

### `Item`

An individual piece of equipment that can be booked.

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `name` | String(100) | |
| `location` | String(100) | Free text; should match a `Location.name` |
| `manual_link` | String(200) | URL to manual/docs |
| `photo_path` | String(200) | Path to item photo |
| `is_bookable` | Boolean | If False, item cannot be booked |
| `max_booking_days` | Integer | Default `8`. Longest booking allowed for this item, counted **inclusively** (borrow Mon → return the following Mon = 8 days). Set per item by admins; enforced in `/book`, `/book_cart`, the Flatpickr return-date picker and the FullCalendar drag selection |

**Relationships**: `bookings` → list of `Booking`

---

### `Location`

Managed list of locations used as a dropdown in the add/edit item forms.

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `name` | String(100) unique | e.g. `'A'`, `'B'`, `'Studio 1'` |

Pre-populated with `A`, `B`, `C` by `create_default_locations()` on first startup. Manage via `/locations`.

> **Note**: `Item.location` is a plain `String`, not a FK. Deleting a `Location` is blocked if any `Item` references its name.

---

### `User`

Registered user (local account or MS SSO auto-created).

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `username` | String(100) unique | Email address for SSO users |
| `password` | String(128) | Hashed (pbkdf2); placeholder for SSO users |
| `email` | String(100) | |
| `is_admin` | Boolean | Grants access to all admin routes |
| `first_name` | String(50) | |
| `last_name` | String(50) | |

---

## Routes Reference

### Public routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Home page — item grid with availability status and cart controls |
| GET | `/about` | About page |
| GET | `/policy` | Terms of use / policy page |
| GET | `/item/<item_id>` | Item detail page with booking calendar and booking/cart modals |
| GET | `/bulk_details` | JSON endpoint — returns item details + booked dates for a list of item IDs (used by cart JS) |

### Auth routes

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/login` | Local username/password login |
| GET | `/logout` | Logout (clears Flask-Login session + custom session keys) |
| GET | `/login_microsoft` | Initiates MS OAuth2 flow |
| GET | `/getAToken` | MS OAuth2 callback — creates/logs in user, populates borrower info in session |

### Booking flow routes (login required)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/set_borrower` | Saves borrower info (name, email, phone) to session as JSON |
| POST | `/book_cart` | Adds items to cart session with borrower info and dates |
| GET | `/cart` | Cart review page — shows items with dates, allows date editing before confirming |
| GET/POST | `/remove_from_cart/<item_id>` | Removes one item from cart, or clears cart if `item_id='all'` |
| POST | `/book` | **Confirms booking**: validates availability for all items, creates one `Booking` + one `BookingItem` per item, sends confirmation email, clears cart |

### Admin routes

All admin routes require the user to be authenticated and have `is_admin = True`. Unauthorised access returns HTTP 403.

#### Booking management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/bookings_list` | Legacy flat list of all `Booking` rows (DataTables) with lend/deny/return actions per row |
| GET | `/bookings_admin` | **New in v3**: one row per `Booking` (group view); click a row to open a detail modal |
| GET | `/booking_detail_json/<booking_id>` | JSON — returns borrower info + items list for the detail modal in `/bookings_admin` |
| GET | `/approve/<booking_id>` | **New in v3.3**: sets `status = 'approved'`, sends the "ready to collect" email. Refuses if already returned/denied |
| GET | `/lend/<booking_id>` | Sets `status = 'lent'`, sends the collection email. Reachable from `booked` or `approved`; refuses if already returned/denied |
| POST/GET | `/return/<booking_id>` | **Changed in v3.3**: no longer deletes. Sets `status = 'returned'`, or `'denied'` when `formAction` is `deny`/`deny_no_note`. Sends return or deny email |

#### Item management

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/add_item` | Add new item (Tailwind form with location dropdown) |
| GET/POST | `/edit_item/<item_id>` | Edit existing item (Tailwind form with location dropdown pre-selected) |
| POST | `/delete_item/<item_id>` | Delete item |
| GET | `/admin_dashboard?section=history[&status=…]` | **New in v3.4**: full booking log — every booking ever made, including returned/denied, with per-status filter chips and counts |
| GET | `/admin/export_items?format=csv\|xlsx\|pdf` | Download the full item list as a file (`csv` default; `xls`/`excel` are accepted aliases for `xlsx`). Triggered by the **Export items** dropdown in the Items section of the admin dashboard. Columns: ID, Name, Location, Bookable, Manual Link, Photo Path |

#### Location management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/locations` | List all locations; inline add form |
| POST | `/add_location` | Create a new `Location` |
| POST | `/delete_location/<location_id>` | Delete a `Location` (blocked if any item references it) |

#### Utilities

| Method | Path | Description |
|--------|------|-------------|
| GET | `/session-dump` | Returns current session as JSON (debugging) |
| GET | `/test-job` | Manually triggers the daily return-reminder email job |

---

## Booking Flow

### Step-by-step

```
User browses items on home page (/)
    │
    ├─ Clicks "Book" or "Add to Cart" on an item
    │       │
    │       └─ Modal opens (macros.html: book_modal)
    │           User fills in: name, email, phone, borrow/return dates, note
    │
    ├─ "Add to Cart" → POST /book_cart
    │       Saves item + dates + borrower info to session['cart']
    │       Redirects to home
    │
    ├─ Repeat for more items
    │
    ├─ GET /cart
    │       Reviews all cart items, can change dates
    │
    └─ "Book" → POST /book
            Validates every item (is_bookable, date order, availability)
            Creates ONE Booking record (first item for backwards-compat fields)
            Creates ONE BookingItem per item
            Commits, sends confirmation email
            Clears cart, redirects home

Admin flow:
    GET /bookings_admin or /bookings_list
        Click row → GET /booking_detail_json/<id>
        Modal shows borrower info + per-item dates
        Actions (shown per status):
            booked   → "Approve"       → GET /approve/<id>      → status='approved' + email
                       "Mark as Lent"  → GET /lend/<id>         → status='lent'     + email
                       "Deny"          → POST /return/<id>      → status='denied'   + deny email
            approved → "Mark as Lent"  → GET /lend/<id>         → status='lent'     + email
                       "Deny"          → POST /return/<id>      → status='denied'   + deny email
            lent     → "Mark Returned" → GET/POST /return/<id>  → status='returned' + return email
            returned / denied → no actions (terminal)
```

### Availability checking

`is_item_available(item_id, start, end)` checks for date overlaps in **both**:
1. **New-style**: `BookingItem.item_id == item_id` with date overlap
2. **Old-style (legacy)**: `Booking.item_id == item_id` where `~Booking.booking_items.any()` (no children)

This ensures legacy single-item bookings and new group bookings are both respected.

Both branches additionally filter on `Booking.status.in_(ACTIVE_STATUSES)`. Since v3.3 a returned or
denied booking is **kept** in the database rather than deleted, so without this filter every returned
item would stay permanently unbookable. The same filter applies in `check_all_items_availability()`,
`get_bookings_list()` (calendar) and the return-reminder job.

### Backwards compatibility

Legacy `Booking` rows (created before v3.0, no `BookingItem` children) continue to work on all pages:
- `home`: detects lent status via both paths
- `item_details`: combines old and new bookings in the table
- `bookings_list` / `bookings_admin`: shows all rows; detail modal falls back to `[booking]` when no children
- `lend_item` / `return_item`: email uses `booking_items` if present, else `[booking]`
- `get_bookings_list`: returns dates from both old and new rows for the calendar

---

## Email Notifications

All emails are sent via MailerSend. Templates are in `templates/email_*.html`.

| `type_of_mail` | Template | Trigger | Recipients |
|----------------|----------|---------|------------|
| `booking` | `email_booking.html` | Booking confirmed | Borrower + all admins |
| `approved` | `email_approved.html` | Booking approved (`/approve`) | Borrower only |
| `lent` | `email_lent.html` | Item collected (`/lend`) | Borrower only |
| `returned` | `email_returned.html` | Item marked as returned | Borrower only |
| `deny` | `email_deny.html` | Booking denied | Borrower + all admins |
| `return_reminder` | `email_return_item.html` | Daily cron (22:22) | Borrower only |

### Notification sequence per booking

A full lifecycle sends **four** emails (was three before v3.3):

| # | When | `type_of_mail` | To |
|---|------|----------------|-----|
| 1 | Borrower submits the booking | `booking` | Borrower |
| 1b | Same moment — request for action | `booking_admin` | Admins (Approve / Deny buttons) |
| 2 | Admin approves | `approved` | Borrower |
| 3 | Admin marks collected | `lent` | Borrower |
| 4 | Admin marks returned | `returned` | Borrower |

Denial replaces steps 2–4 with a single `deny` email (or none at all when the admin uses
`deny_no_note`). The daily return reminder is independent of this sequence.

Email templates receive: `borrower_name`, `borrower_email`, `borrower_phone`, `borrow_date`, `return_date`, `items` (list of `BookingItem` or `Booking` objects), `now`.

The `items` list uses duck typing — both `BookingItem` and `Booking` expose `.item_name`, `.borrow_date`, `.return_date`, so templates work with both.

### Daily reminder job

`check_and_send_reminders_tomorrow()` runs at 22:22 via APScheduler (production only). Queries `Booking` rows due today or tomorrow **whose status is still active**, groups them by borrower, and sends one aggregated email per borrower. Returned and denied bookings are never chased.

> The reminder job only queries old-style `Booking` rows directly. Group bookings will need updating here in a future version.

---

## Dark Mode

Dark mode is implemented using both Bootstrap 5 (`data-bs-theme`) and Tailwind CSS (`dark` class on `<html>`), kept in sync.

**Detection order**:
1. `localStorage.getItem('theme')` (user preference, persisted across visits)
2. `window.matchMedia('(prefers-color-scheme: dark)')` (OS preference, first visit)

**Toggle**: The ☀/🌙 button in the navbar writes to `localStorage` and toggles both `html.dark` (Tailwind) and `html[data-bs-theme]` (Bootstrap) simultaneously.

**Flash prevention**: A small inline script in `<head>` (before `<body>` renders) reads the preference and applies the dark class immediately, preventing a light-mode flash on load.

**Standalone pages** (`add_item.html`, `edit_item.html`) implement dark mode independently — they don't extend `base.html` and include their own Tailwind + init script + toggle button.

---

## Environment Setup

1. Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run in development mode (no scheduler, port 5006):

```bash
MISC_DEV=true python main.py
# or
python main.py --dev
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask session secret |
| `DB_USERNAME` | MySQL username |
| `DB_PASSWORD` | MySQL password |
| `DB_NAME` | MySQL database name |
| `MAILERSEND_API_KEY` | MailerSend API key |
| `MISC_PASSWORD` | Password for the local `admin` account |
| `AZURE_CLIENT_ID` | MS app registration client ID |
| `AZURE_CLIENT_SECRET` | MS app registration secret |
| `AZURE_TENANT_ID` | MS tenant ID |
| `ADMIN_EMAILS` | Comma-separated admin email addresses |
| `MISC_DEV` | Set to `true` / `1` to enable dev mode |

All variables fall back to `vars/vars.json` keys if not set in the environment.

---

## DB Migrations

```bash
export FLASK_APP=main.py

# First time only
flask db init

# Generate a migration after model changes
flask db migrate -m "describe the change"

# Apply
flask db upgrade
```

### `migrate.sh` — automated backup + migrate script

A helper script is included at `migrate.sh`. It:
1. Reads DB credentials from env vars or `vars/vars.json` automatically
2. Dumps the current database to `vars/booking_dump_YYYYMMDD_HHMMSS.sql` (aborts if the dump is empty)
3. Runs `flask db migrate` + `flask db upgrade`
4. Seeds default locations (A, B, C) if they don't exist yet

```bash
# Activate your virtualenv first, then:
./migrate.sh

# Preview all commands without executing anything:
DRY_RUN=1 ./migrate.sh
```

If something goes wrong after migration:

```bash
flask db downgrade
mysql -u <db_user> -p <db_name> < vars/booking_dump_<timestamp>.sql
```

### Migration history

| Migration | Description |
|-----------|-------------|
| Initial | `booking`, `item`, `user` tables |
| v2.1 | `booking.note`, `item.is_bookable` columns |
| v3.4 (`b11084c2f89c`) | `item.max_booking_days` (server_default `8`), `booking.created_at`, `booking.status_changed_at` |
| v3.0 | `booking_item` table, `location` table |

After deploying v3.0 for the first time, run:

```bash
flask db migrate -m "add booking_items and location tables"
flask db upgrade
```

`create_default_locations()` is called automatically on `__main__` startup and seeds locations A, B, C if they don't exist. Run it once manually after migration if using gunicorn:

```bash
python -c "from main import app, create_default_locations; create_default_locations()"
```

---

## Production Deployment

The app runs under gunicorn managed by systemd (`booking.service`).

- **Config**: `vars/vars.json` on the server (gitignored)
- **Scheduler**: Only starts when `MISC_DEV` is not set
- **Path prefix**: `APPLICATION_ROOT = '/booking'` is set in production (controlled by `MISC_DEV`)
- **Gunicorn does not execute `__main__`** — use the `MISC_DEV` env var, not `--dev`

```bash
sudo systemctl daemon-reload
sudo systemctl restart booking.service
```

---

## Known Issues

- When migrating the database, flashed messages can persist across the redirect and must be dismissed manually.
- When migrating the database, the user session may break and the user cannot log out — clear cookies to fix.
- The daily reminder job only queries old-style `Booking` rows; group bookings (v3+) are not yet covered.
- No CSRF protection on any form.
- MailerSend BCC bug: BCC is currently commented out due to a reported SDK bug; all recipients receive the email via the `to` field.

---

## TODO
* Update to  Booked / Lent / Returned statuse — DONE (v3.3: booked / approved / lent / returned / denied)
* Add max lending time. — DONE (v3.4: `Item.max_booking_days`, default 8) 
* Group by categories like a music shop (microphones, instrruments, utilities, )
* Students should add a note in their profile and/or booking on what they study and what the booking is for. DONE
* Do not serve images from Flask — serve from nginx instead.
* Simplify the email templates (currently bloated HTML).
* Implement Flask Blueprints.
* Add the LMTA email to the booking item (in case users falsify identity).
* Turn add item and other mini pages into modals.
* Find out if it is necessary to send booked dates back and forth, or handle everything in session.
* DataTables alternative?
* Refactor: `url_for('book_cart')` and `url_for('book')` are always the same — no need to pass them as data attributes to the JS script.
* Return to same page search/filter conditions when going back.
* Bulk deny bookings by checkbox selection.
* Bulk or individually mark as returned. 
* Mark as lent or returned in bulk.
* Grouping items and show quantity available.
* Connect to Google Calendar and/or apple and/or outlook
* Note on return about state of item.
* Comments on item pages.
* Add [random/deterministic] colours to booking calendar (partially done — `colorFromString()` in `item_detail_modals.js`).
* Check the password security of accounts created via MS Login.
* Extend daily reminder job to cover v3+ group bookings (currently only queries old-style `Booking` rows).
* Keep sending reminders while item is "lent" and include a message asking admin to mark it returned.
* Decide whether to send reminder emails to admins.
* Bulk delete items by checkbox.
* Add a Booking Log — DONE (v3.4: admin dashboard → History)
* Add possibility to return items individually that were bulk booked/lent.
* Add a reminder in the return email to take a photo of where the item was left. (integrate on the phone)

---

## Changelog

### v3.4 — 2026-10-03
- **Tidier action buttons in the admin booking tables**: the Actions cell had no layout, so buttons
  inherited ad-hoc `me-1` margins and wrapped mid-label ("Mark as / Returned"), and rows with one,
  two or three actions all lined up differently. Added a `.booking-actions` cell class in
  `booking.css` that stacks the buttons at equal width with a consistent gap and no label wrapping,
  and shrinks the column to its content via the `white-space: nowrap; width: 1%` convention the
  other admin tables already use. Applied to both `admin_dashboard.html` and `bookings_admin.html`.
- **Item page and `/bookings_list` show active bookings only**: both now filter on `ACTIVE_STATUSES`.
  Since v3.3 kept returned/denied rows, these views were listing closed bookings and making free
  periods look occupied — the opposite of what the item page is for (seeing when an item that is out
  now is free again). `approved` is included because it holds the dates just as `lent` does; hiding it
  would advertise a reserved period as free. The item table is ordered soonest-first. The complete
  record of every booking remains in the admin dashboard History section.
- **Booking history log** (`/admin_dashboard?section=history`): a new **History** item in the admin
  sidebar listing every booking ever made — requested time, borrower, items, dates, status badge, last
  status change and note — with filter chips per status (showing counts) and DataTables search/sort/paging.
  Before v3.3 no history was possible because returns deleted the row; this makes the retained rows usable.
- **`Booking.created_at` / `Booking.status_changed_at`**: timestamps so the log can show *when* things
  happened. `status_changed_at` is maintained by the new `Booking.set_status()` helper, used by approve,
  lend, return and deny. Both are NULL for bookings created before this version — the history shows "—".
- **`Item.max_booking_days`**: per-item cap on booking length, default **8 days**, counted inclusively
  (borrow Mon → return the following Mon = 8 days). Editable in `add_item.html`, `edit_item.html` and the
  admin dashboard Items section, and shown as a column there and in the item export.
  - Enforced **server-side** in `/book` and `/book_cart` (the authority), and **client-side** for immediate
    feedback: the Flatpickr return-date picker gets a `maxDate`, FullCalendar's `selectAllow` rejects
    over-long drag selections, and `submitForm()` blocks submission with a message naming both lengths.
  - When several items are booked together the **strictest cap wins** — `setMaxBookingDays()` takes the
    minimum across the selection. The cart re-reads caps from the DB so an admin change applies to carts
    already in flight.
- **DB migration required**: `b11084c2f89c`. Run `./migrate.sh` (or `flask db upgrade`) on the server.
  All three columns are additive; existing items get `max_booking_days = 8` via `server_default`.

### v3.3 — 2026-10-03
- **Booking status lifecycle**: `booked → approved → lent → returned`, plus `denied`. Previously only
  `booked` and `lent` existed; a return or denial **deleted** the booking row, so there was no history.
  - Statuses are now named constants in `main.py` (`STATUS_*`, `ACTIVE_STATUSES`, `TERMINAL_STATUSES`)
    instead of string literals scattered across the codebase.
  - `/return` and deny keep the row and set a terminal status. **Availability is now driven by status,
    not by row deletion**: `is_item_available()`, `check_all_items_availability()`, `get_bookings_list()`
    and the return-reminder job all filter on `ACTIVE_STATUSES`, so returned/denied bookings release the
    item and free their calendar dates.
  - New route `/approve/<booking_id>`. Approve and Lend both refuse to act on a terminal booking.
  - **Notifications: 4 per full cycle, up from 3.** A new "Booking approved — ready to collect" email
    (`email_approved.html`) is sent on approval; the lend email is now collection-only ("Item(s)
    Collected") instead of "Booking approved and collected". The admin booking-request email's primary
    button is now **Approve Booking** rather than Mark as Lent.
  - UI: a single `status_badge()` macro in `macros.html` (mirrored for modal JS in
    `static/js/status_badge.js`) replaces the per-template badge logic in `admin_dashboard.html`,
    `bookings_admin.html`, `bookings_list.html`, `profile.html` and `item_details.html`. Action buttons
    are per status: booked → Approve/Lend/Deny, approved → Lend/Deny, lent → Return, terminal → none.
  - **No DB migration required** — `status` is already `String(20)` and the new values fit. Existing
    `booked`/`lent` rows stay valid.
- **Dark mode fix — link-buttons**: `[data-bs-theme="dark"] a` outranked Bootstrap's `.btn` colours and
  painted every `<a class="btn">` light blue (unreadable on `btn-info`/`btn-success`). Scoped to
  `a:not(.btn)`.

### v3.2 — 2026-09-06
- **Dark mode fix — non-bookable items**: rows for `is_bookable = False` on the home page used Bootstrap's `.table-secondary`, which hard-codes a light grey background and ignores `data-bs-theme`, so in dark mode the row rendered as pale text on a pale block. Replaced with a theme-aware `.item-not-bookable` class in `booking.css` (subtle tint + `opacity: 0.7`, with a dark-mode override), legible in both themes.
- **Dev-mode startup fix**: the `__main__` block no longer overwrites `LOCALHOST` with `False` when `-d` is absent, so `MISC_DEV=true` in `.env`/the environment is honoured again and `python main.py` starts on port 5006 instead of the occupied default 5000. `-d`/`--dev` still forces dev mode on. The `scheduler.shutdown()` call in the shutdown handler is now guarded, so a startup failure reports the real error instead of masking it with `NameError: name 'scheduler' is not defined`.
- **Item export**: `/admin/export_items` streams the full item list as CSV, XLSX or PDF; an **Export items** dropdown sits next to the heading in the admin dashboard Items section.
  - CSV is written with a UTF-8 BOM so Excel opens Lithuanian characters correctly.
  - XLSX (via `openpyxl`) has a bold header row, frozen header, auto-filter and auto-sized columns.
  - PDF (via `reportlab`) is landscape A4 with a repeating header row.
  - New dependencies: `openpyxl`, `reportlab` — run `pip install -r requirements.txt` on the server after deploying. Both are imported lazily; if one is missing the route flashes an error instead of breaking the dashboard.

### v3.0 — 2026-03-03
- **Group booking**: one `Booking` record per cart session + one `BookingItem` per item (backwards-compatible with legacy single-item bookings).
- **Admin bookings page** (`/bookings_admin`): one row per Booking group; click to open detail modal with per-item dates and actions.
- **Location model**: DB-managed locations with CRUD at `/locations`; add/edit item forms use a dropdown instead of a free-text field.
- **Dark mode**: Tailwind CDN added alongside Bootstrap 5; OS preference detection + `localStorage` persistence; toggle button in navbar.
- **UI**: `add_item.html` and `edit_item.html` rewritten with Tailwind utility classes and dark mode support.
- **Nav**: removed stray Bootstrap 4 CDN script; added "All Bookings" and "Locations" admin links.

### v2.1 — 20251121
- Collect borrower's phone number.
- On "add to cart", pre-populate borrow date with today.
- Block calendar dates before today and already-booked dates.
- Add to cart from map selection.
- Save borrower info in session for better UX.
- New admin page for all bookings/lent instances.
- Send email with note on booking denial.
- Added About and Terms of Use pages.
- "Book" on item detail now shows a modal consistent with "Add to Cart".
- Refactored several variable names.
- MS Office 365 login via MSAL.
- Email on lend and on return.
- Booking note field.
- Non-bookable item flag (`is_bookable`).
- Deterministic calendar colours per borrower name (`colorFromString()`).
- Aggregated return reminder: one email per borrower, not one per item.
- Delete item confirmation modal.
- Fixed bugs: duplicate recipient emails, table overflow, phone field UX, empty booking handling.

### v2.0 — 20240826
- Added navigation bar.
- Cart functionality for multi-item booking.
- Layout changes.
- Bulk booking.
