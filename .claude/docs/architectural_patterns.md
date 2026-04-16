# Architectural Patterns & Design Decisions

Patterns that appear in multiple places across the codebase. Read this before adding features.

---

## 1. Dual-path Availability Check (Backwards Compatibility)

**Where**: `main.py:530` (`is_item_available`), `main.py:499` (`check_all_items_availability`), `main.py:551` (`get_bookings_list`), `main.py:244` (`home`), `main.py:443` (`item_details`)

**Pattern**: Every query that asks "is this item booked?" must check two paths:
1. **New-style** (v3+): query `BookingItem` where `item_id == x`
2. **Old-style** (legacy): query `Booking` where `item_id == x` AND `~Booking.booking_items.any()`

The `~Booking.booking_items.any()` filter is the key distinguisher. Omitting it causes double-counting — new-style `Booking` rows would match both paths.

```
# Pattern used in is_item_available, get_bookings_list, home route, item_details:
new_style → query BookingItem.item_id == item_id
old_style → query Booking.item_id == item_id, ~Booking.booking_items.any()
results   → combine / union
```

---

## 2. Capture-Before-Delete

**Where**: `main.py:891` (`return_item`)

**Pattern**: SQLAlchemy expires object attributes after `db.session.delete()` + `commit()`. Cascade deletes also remove child `BookingItem` rows. Data needed after deletion must be captured before the delete call.

- Scalar fields (`borrower_email`, `item_name`, etc.) → assigned to local variables
- `BookingItem` list → converted to `SimpleNamespace` objects (`main.py:905–913`) before delete
- Old-style `[booking]` list → SQLAlchemy retains in-memory attribute values on the Python object even after deletion (relies on SQLAlchemy's expunge-on-delete behaviour)

---

## 3. Admin Decorator

**Where**: `main.py:210` (definition); applied to ~12 routes

**Pattern**: `@admin_required` decorator wraps `@login_required` semantics: checks `current_user.is_authenticated` and `current_user.is_admin`, aborts with 403 otherwise. All admin routes use this decorator — do not add `if not current_user.is_admin` guards inside route bodies; use the decorator instead.

---

## 4. Duck-Typed Model Properties

**Where**: `main.py:158–167` (`BookingItem` properties)

**Pattern**: `BookingItem` exposes `borrower_name`, `borrower_email`, `borrower_phone`, `status`, `note` as `@property` methods that delegate to the parent `Booking`. This lets Jinja2 templates and `send_email()` accept either a `Booking` or a `BookingItem` without branching. Email templates (`email_*.html`) rely on this — they iterate `items` and access `.item_name`, `.borrow_date`, `.return_date` regardless of which type is passed.

---

## 5. Data-Attribute JS Bridge

**Where**: `templates/home.html:75–79`, `templates/item_details.html:79–86`, `templates/bookings_admin.html`; consumed at `static/js/item_detail_modals.js:15`

**Pattern**: Flask passes URLs and data to JavaScript via `data-*` attributes on the `<script>` tag that loads `item_detail_modals.js`. The JS reads them via `document.currentScript.dataset`. This avoids inline `<script>` globals for URLs and keeps Jinja2 logic out of JS files.

```html
<!-- Template sets data attributes -->
<script src="item_detail_modals.js"
  data-action_cart="{{ url_for('book_cart') }}"
  data-action_book="{{ url_for('book') }}"
  data-disabled_dates="{{ booked_dates }}"
  data-item_for_cart='{{ item_for_cart|tojson }}'></script>

<!-- JS reads them -->
let data = document.currentScript.dataset;  // item_detail_modals.js:15
form.action = data.action_book;             // item_detail_modals.js:472
```

When adding new data to pass from a template to JS, add it as a `data-*` attribute on this script tag — do not create new global variables.

---

## 6. Session as Cart + Borrower Store

**Where**: `main.py:424` (`set_borrower`), `main.py:716` (`book_cart`), `main.py:573` (`book`), `main.py:792` (`cart`)

**Pattern**: Flask session holds two keys:
- `session['cart']` — list of dicts, one per item: `{id, name, location, borrow_date, return_date, borrower_name, ...}`
- `session['borrower_info']` — **single-element list** containing one dict: `[{borrower_name, borrower_email, borrower_phone}]`

`borrower_info` is always a list with one element (accessed as `session['borrower_info'][0]`) — this is an artefact of the original design. Do not change this shape without updating all callers.

Clearing the cart after booking: `session['cart'] = {}` (not `[]` — intentional, checked with `!= {}`).

---

## 7. Credential Resolution Chain

**Where**: `main.py:48–59` (`_cfg` helper), called at `main.py:61–76`

**Pattern**: `_cfg(env_key, json_key)` reads an env var first; if absent, lazy-loads `vars/vars.json` and reads `json_key`. All credential access goes through `_cfg` — do not use `os.environ.get` directly for credentials. When adding a new credential, add it as both an env var (for production) and a `vars/vars.json` key (for dev fallback), then expose it via `_cfg`.

---

## 8. Single `send_email()` Dispatcher

**Where**: `main.py:1130` (definition); called from `book` (`:649`), `lend_item` (`:873`), `return_item` (`:930`, `:944`), `check_and_send_reminders_tomorrow` (`:1112`)

**Pattern**: All email sending goes through one function with a `type_of_mail` parameter that selects the template. Adding a new email type means: (1) add an `elif type_of_mail == 'new_type'` block inside `send_email()`, (2) create `templates/email_new_type.html`. Do not call `mailer.emails.send()` directly outside this function.

`items` parameter is always a list and must contain objects with `.item_name`, `.borrow_date`, `.return_date` attributes (real model objects or `SimpleNamespace`).

---

## 9. Jinja2 Macro Pattern

**Where**: `templates/macros.html` (definitions); included in `home.html`, `item_details.html`, `bookings_list.html`, `bookings_admin.html`

**Pattern**: Reusable modal HTML is defined as Jinja2 macros in `macros.html`: `book_modal`, `deny_modal`, `footer`. Import with `{% from 'macros.html' import book_modal %}`. The `deny_modal` macro accepts an optional `booking_id`; when not provided, the form `action` URL is set dynamically by JS (`showDenyModal` / `openDenyModal`). Do not duplicate modal HTML across templates; extend macros.html instead.

---

## 10. JSON-over-POST for Complex Forms

**Where**: `main.py:591` (`book` reads `itemsJSON`), `main.py:662` (`book_cart` reads `itemsJSON`); packed at `static/js/item_detail_modals.js:342` (`updateJSON`)

**Pattern**: When a form needs to submit a variable-length list of items with per-item data (id, name, dates, borrower), it uses a hidden `<input name="itemsJSON">` field. `updateJSON()` in JS serialises the items array to JSON and writes it to this field before `form.submit()`. Flask routes parse it with `json.loads(request.form.get('itemsJSON'))`. This bypasses the limitations of HTML form arrays.

---

## 11. `base.html` Inheritance vs Standalone Pages

**Where**: All pages extend `base.html` except `add_item.html` and `edit_item.html`

**Decision**: `add_item.html` and `edit_item.html` are standalone (no `{% extends %}`). They load only Tailwind CDN (no Bootstrap, no DataTables, no FullCalendar) keeping them fast and self-contained. They implement their own dark mode init and toggle. This is intentional — do not make them extend `base.html` unless you need Bootstrap modals or DataTables there.

All other templates extend `base.html`, which provides Bootstrap 5, Tailwind, jQuery, DataTables, FullCalendar, Flatpickr, and dark mode init.
