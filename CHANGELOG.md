# Changelog — MISC Booking System

All notable changes to this project, newest first.
Full documentation lives in [README.md](README.md).

---

## v4.5.4 — 2026-10-27 — The corner square was the specular highlight, not the frosting
Measuring a screenshot of the artifact instead of reasoning about it showed that **only the
top-left corner was ever affected**; the other three were clean. That asymmetry identifies the
layer: the specular highlight is the one layer whose bloom is bright at the top left.

- **Cause.** `.category-card::after` had `mix-blend-mode: screen`. A blended layer is composited,
  and WebKit then applies neither the card's `overflow: hidden` + `border-radius` clip nor the
  layer's own radius to it, so its square corner painted in the notch outside the card's rounded
  one. A square backdrop would have shown at all four corners, which is what ruled the frosting
  out.
- **Fix.** The blend is gone, with `clip-path: inset(0 round 18px)` as a second guard, since
  clip-path is layer geometry and survives compositing where `border-radius` does not. Cost
  measured rather than assumed: mean difference 4.2/255 across a card, 1.2% of pixels differing by
  more than 16.
- **v4.5.3 is reverted.** The separate `.category-card__glass` layer was built on the wrong
  diagnosis; `backdrop-filter` goes back on the card and the extra element is gone. The `top` lift
  from v4.5.2 and the radius guards stay — they are cheap and remove real compositing risk.
- Net visual change across v4.5.1–v4.5.4, diffed against the build before them: mean 2.08/255,
  with 0.03% of pixels differing by more than 16.

---

## v4.5.3 — 2026-10-27 — Take the frosting off the card and give it its own layer
The rectangle at the corners survived v4.5.1 and v4.5.2, still appearing for the length of the
hover transition and then settling.

- **Why the previous fixes could not have worked.** They put `border-radius: inherit` on every
  layer inside the card, which stops any of *those* layers being squared off. But the frosting was
  on the card itself, and the card's radius is precisely the thing Safari ignores when it
  rasterises a backdrop. No guard on a child could reach it.
- **Fix.** `backdrop-filter` moves to `.category-card__glass`, a new layer inside the card that
  nothing ever animates, at `z-index: -3` where the card's own filter used to sit. The card keeps
  the lift, the rim and the shadow; the frosted box no longer takes part in the hover at all. The
  `@supports` refraction follows the filter onto the same layer.
- Verified the look is unchanged by diffing screenshots against the previous build rather than by
  eye: edge energy inside a card 20.9 before vs 21.7 after, mean per-pixel difference 2.7/255. An
  earlier attempt that placed the glass above the category image measured twice that difference
  and was rejected.

---

## v4.5.2 — 2026-10-27 — Remove the second cause of the Safari corner square
v4.5.1 fixed one layer; a square still flashed for the length of the hover transition and then
settled. That was a different mechanism on the card itself.

- **Cause.** `.category-card` carries `backdrop-filter` and `border-radius`, and the hover lift was
  a `transform`. Safari paints a backdrop-filtered element's backdrop as its square bounding box,
  ignoring the radius, while a transform composites it — so the square appeared for the 0.22s of
  the transition and then settled back into the rounded shape. Safari takes the plain-blur path
  here (the `-webkit-hyphens` gate keeps the SVG refraction away from it), so the backdrop-filter
  is on the card itself, not on an inner layer.
- **Fix.** The lift is now `top: -4px` on the already-relative card instead of a transform. Nothing
  promotes the card, so the backdrop keeps its radius throughout. Relative offsets do not reflow
  siblings, and `z-index: 1` on hover keeps the lifted card's shadow over its neighbours.
- `.category-card::before` and `::after` inherit the card's radius as well. Both are `inset: 0`, so
  their corners coincide with the card's and a missed clip cannot square them off either. Every
  layer inside the card now carries the radius.

---

## v4.5.1 — 2026-10-27 — Fix the square showing at the card corners in Safari
Hovering a category card in Safari painted a square just outside its rounded corners. Chrome was
unaffected.

- **Cause.** The specular highlight (`.category-card::after`) was an oversized layer that slid on
  hover with `translate3d`. The 3D transform promoted it to its own compositing layer, and WebKit
  does not apply an ancestor's `overflow: hidden` + `border-radius` clip to a composited
  descendant — so the layer drew its own square corners outside the card. That is why it appeared
  only in Safari, and only on hover: the plain 2D scale at rest was never promoted.
- **Fix.** The layer is now exactly the card's box and is never transformed; the hover drift moves
  the gradients *within* it via `background-position`. Nothing extends past the card, so there is
  nothing for a missed clip to reveal, and the highlight still travels as it did.
- `.category-card__media` inherits the card's border radius, so a clip miss during its hover zoom
  cannot square it off either.

---

## v4.5 — 2026-10-27 — Drop the diagonal weave; admins can upload a card background
The striped texture behind the category cards is gone, and each faculty can have its own background
image instead.

- **The weave is removed** from both themes. It was added in v4.3 to give the refraction hard edges
  to bend, but it read as exactly what it was — a visible diagonal stripe pattern. The soft colour
  pools stay: they are what give the blur something to scatter, so the cards still read as glass
  rather than as flat tinted panels.
- **Per-faculty background image.** `Faculty.background_image_path` (nullable, relative to
  `static/`) and `POST /admin/faculty/<id>/background` to set or clear it. The upload card sits in
  Admin → Categories, next to the rest of that faculty's card settings, with a preview, a replace
  field and a Remove button that only appears when there is something to remove.
- The image is pushed back so the cards stay the subject — blurred slightly, desaturated slightly,
  and dimmed by an overlay (dark in dark mode, light in light mode), because an uploaded photo can
  be bright anywhere and the card titles sit on top of it. The same feathered mask applies, so it
  fades at the edges instead of ending on a straight line.

Authorisation is scoped to the faculty in the URL, not the session's current one, so the check is
against the faculty actually being changed; a faculty admin gets 403 on another faculty's
background. Replacing an image deletes the previous file rather than orphaning it, and the filename
carries a timestamp so a replacement is always a new URL.

`_save_category_image()` grew into `_save_image_upload(file, directory, stem)`, shared by both
upload paths, so the extension allowlist and the 4 MB stream-read size check have one implementation.

Upload filenames also gained a short random suffix: the whole-second timestamp alone meant two
uploads in the same second reused one name, and the replacement would then be served from cache
under the unchanged URL.

Migration `8d9f1af35972`.

---

## v4.4.3 — 2026-10-26 — The seam was the specular layer, and the refraction is back
The division line down the left and across the bottom of a hovered card was never the glass. It was
`.category-card::after`, the specular highlight.

That layer sits at `inset: 0` — exactly the card — and blends with `mix-blend-mode: screen`, so it
lightens whatever it covers. On hover it took `translate3d(7%, -5%, 0) scale(1.06)`, sliding right
and up, which brought its own rectangular edge inside the card: lit glass on one side of that edge,
unlit on the other, along the left and the bottom. Nothing else in the stack moves on hover in a way
that could produce two edges on exactly those two sides.

The geometry is exact rather than suggestive. With `transform-origin` at the centre, the old hover
put the layer's left edge at 4% of the card's width and its bottom edge at 98% of its height — both
inside. On the 1300px-wide card in the report, 4% is 52px, which is where the line was.

- **Fixed** by giving the layer headroom: `scale(1.16)` at rest, `translate3d(7%, -5%, 0) scale(1.22)`
  on hover. Every edge now lands outside the card — left −4%, right 118%, top −16%, bottom 106% — so
  the drift survives and the edge never enters. Scaling rather than a larger `inset` keeps the
  background percentages mapped to the element box, so the streak and the pointer-tracked bloom land
  where they did before.
- **Reverted v4.4.2.** The refraction was removed on a misdiagnosis: the A/B that appeared to clear
  the card with the lens disabled was measuring the specular edge, which was present either way. Both
  the `#glassLens` filter and the `@supports` rule are restored, including the v4.4.1 rim and corner
  fixes, which were genuine bugs on their own. Re-checked with the specular fix in place: the lens is
  clean at the real card size.

The v4.4.2 reasoning about displacement near a clipped rim is still correct as far as it goes — it
simply was not the cause of what was on screen.

---

## v4.4.2 — 2026-10-26 — Remove the refraction; the seam was intrinsic to it
The v4.4.1 fix was not enough: the hard band a few pixels inside a hovered card was still there.
Measuring the lens had shown it no longer sampled outside the card, which was true — but that was
never the whole cause, and the refraction has now been removed entirely.

Verified by direct A/B in the browser at the real card size: with the lens disabled the card is
uniformly smooth; with it enabled the band reappears. The two requirements are in conflict at this
size. Displacement must fall to zero at the rim, because `backdrop-filter` clips the backdrop to
the card's own box. On a 150px-tall card that leaves about 75px of radius for the offset to rise
and fall again, and any offset large enough to notice over that distance shears the backdrop
steeply enough to break the diagonal weave into visibly offset segments — which is the band.
Ramps from a sharp knee to a ten-stop curve were tried at scales from 9 to 24: gentle enough to
hide the band is also too gentle to see.

So the cards are frosted glass now — `blur(22px) saturate(180%)` with the lit rim, foot-weighted
scrim and pointer-tracked specular all unchanged — which is what every shipping "glass" surface on
the web actually does. Apple gets away with real refraction by sampling a full-resolution normal
map on a GPU surface, not a stretched raster inside a clipped backdrop. The `#glassLens` SVG and
its `@supports` rule are gone; the reasoning is recorded in `booking.css` so it is not
reintroduced.

The weave backdrop stays: without refraction it still gives the blur something to scatter, which
is what makes the cards read as translucent rather than as flat tinted panels.

---

## v4.4.1 — 2026-10-26 — Fix the torn edge on hovered category cards
Hovering a card showed a hard rectangular seam a few pixels inside its border, with the backdrop
texture visibly offset across it. Two bugs in the refraction lens, both at the rim.

- **The lens displaced the backdrop hardest exactly at the rim** (the gradient reached its maximum
  at offset 1.0). `backdrop-filter` clips the backdrop to the card's own box, so a 5.8px offset at
  the rim sampled pixels that do not exist — which is the seam. The ramp now peaks in a band set in
  from the edge and returns to neutral at the edge itself: 0.03px of displacement at the rim, with
  the 4px peak sitting 10–15px inside, so every sample stays within the card.
- **The map's corners were transparent.** Both rects in the lens image carried `rx='18'`, so the
  area outside the corner radius was transparent black — read as R=0, G=0, i.e. −9px of displacement
  in both axes. The rects are square-cornered now; the gradient alone shapes the lens.

Displacement scale dropped 18 → 15 to keep the peak comfortably under its inset.

Also: the hover no longer changes `backdrop-filter`. It was going from `blur(7px)` to `blur(5px)`,
and since `backdrop-filter` does not interpolate, the whole refracted texture snapped in a single
frame — the "displaced a bit" part of the report. The lift, rim and specular carry the hover now;
the material stays put.

---

## v4.4 — 2026-10-26 — No more hard refreshing
Deploying new CSS or JS used to be invisible to anyone whose browser had already cached the old
copy: nginx serves `/booking/static/` with `expires max`, so the only cure was asking people to
hard refresh. Three changes remove that step.

- **Versioned asset URLs.** Every `url_for('static', ...)` now gets a `?v=` stamp taken from the
  file's modification time, via a Flask `url_defaults` hook — so no template had to change. `git
  pull` rewrites the mtime of files it actually changed, so exactly those URLs change and are
  refetched, while untouched files keep their URL and stay cached. Stat results are memoised per
  worker, and gunicorn restarts on deploy, so the cache cannot go stale.
- **HTML is served `no-cache, must-revalidate`.** Versioning the assets alone achieves nothing if
  the page referencing them is itself cached — the new URLs would never be seen. Only `text/html`
  responses are touched; downloads and static files keep their own headers.
- **`deploy.sh` clears stale `__pycache__`** before restarting, and no longer tells you to hard
  refresh.

Uploaded category images were already safe: they are stored under a filename containing a
timestamp, so a replacement is a new URL regardless.

---

## v4.3 — 2026-10-26 — Category cards: actual refraction, and something to refract
The previous cards were a stylistic approximation of Apple's Liquid Glass. Three changes close
most of the gap the web can close.

- **A stage behind the grid.** The real limit was not the filter but the backdrop: the page is a
  smooth gradient, so blurring *or* refracting it returns the same flat colour. A feathered layer of
  soft colour pools and a fine diagonal weave now sits behind the cards — enough structure for the
  material to bend and scatter — scoped to the grid so it cannot affect the equipment table.
- **Real refraction** via an SVG `feDisplacementMap` on `backdrop-filter`, which bends the backdrop
  rather than smearing it. The lens map is neutral in the centre and ramps at the rim, as light does
  through thick glass. Gated on `@supports`, and additionally excluded on Safari, which reports
  support for `url()` here but does not render it; those engines keep the blur path unchanged.
- **The glass had to transmit.** The card wash and scrim were opaque enough to hide the stage, so the
  refraction had nothing visible to work on. The wash is now much thinner and the scrim weighted to
  the foot where the title sits.
- **Pointer-tracked specular** — the highlight follows the cursor, so a card reads as a lit surface
  rather than a fixed gradient. Progressive enhancement: skipped for touch devices and for
  `prefers-reduced-motion`, with the per-card stagger as the fallback. Tilt via DeviceOrientation was
  deliberately not used; iOS requires a permission prompt on a user gesture, which is far too
  intrusive for someone browsing for a microphone.

Still out of reach in a browser, and the reason this remains an approximation: Apple samples backdrop
luminance per region in real time to keep the material legible, and morphs controls with fluid
dynamics. Neither has a web API.

## v4.2 — 2026-10-25 — User sign-up date and last login
- **`User.created_at`** and **`User.last_login_at`**. Both login paths — local and Microsoft SSO —
  stamp the latter through a single `record_login()` helper, which commits separately so a failure
  recording the timestamp can never block someone signing in. A *failed* login does not stamp it.
- Shown as sortable **Signed up** and **Last login** columns in the dashboard Users section (sorted
  chronologically via `data-order`, not alphabetically on the formatted string), and in the header of
  a borrower's profile.
- Existing accounts show "—" and "never" until they next sign in: there is nothing reliable to infer
  these from, and backfilling from booking dates would be invented data.
- **DB migration required**: `d4640af2391b`.

## v4.1 — 2026-10-25 — Item categories
- **Per-faculty categories** (`Category`), **many-to-many with items** (`item_categories`). An item can
  sit in several at once — the ZYLIA is both a Microphone and Spatial Audio — so the overlaps in the
  coordinator's proposal stop forcing arbitrary filing decisions.
- **Seeded for MISC**: the coordinator's twelve with two merges agreed with the user — Audio Interfaces
  joined to Mixers & Processing (the XR18 is genuinely both), and the two overlapping "accessories"
  buckets combined. **Ten categories, all empty**; items are filed by hand.
- **Assignment works in both directions**: a bulk picker per category
  (`/admin/category/<id>/items`), and category checkboxes on the add/edit item forms. Both refuse to
  file an item under another faculty's category.
- **Cards on each faculty home page**, clicking one filters the equipment list. Admins also get an
  "uncategorised" card for the unfiled backlog.
- **Card images** upload to `static/uploads/` (gitignored): extension allowlist, a real size check on
  the stream rather than trusting Content-Length, and per-category filenames with a cache-buster since
  nginx serves `/static` with `expires max`.
- **Liquid-glass styling**: cards transmit the page behind them (low-alpha tint plus
  `backdrop-filter`), with a specular bloom, a diagonal highlight staggered per card, and a lit rim.
  Placeholders use muted shades of the site's own indigo — the previous 0-359 hue made a rainbow.
  Cards with an uploaded image get the same treatment so they read as glass over a photo.
- **DB migration required**: `98085de03901`.

### Fixed — faculty isolation
An audit after the category work found three leaks dating from the v4.0 faculty phases:
- **`/bookings_admin` was entirely unscoped** — any admin saw every faculty's bookings, including
  borrower names and email addresses, with no `require_faculty_admin` at all.
- **`export_items` exported every faculty's inventory**, so a KIMO admin's CSV contained MISC's.
  Now scoped, and gains a Categories column.
- **`delete_location` / `edit_location` had no faculty guard**, and their in-use and duplicate-name
  checks spanned all faculties — so one faculty using "Studio A" blocked another deleting their own.
- Also fixed: the footer read `{{ current_year or 2025 }}` but nothing ever supplied `current_year`,
  so it was frozen at 2025. Added a context processor, and imported the macro `with context` —
  Jinja macros do not see the template context otherwise.

## v4.0.3 — 2026-10-25
- **Added a fourth faculty: Sound Engineering** (`/?faculty=sound`, Lithuanian "Garso režisūra"). It is a
  full sub-system like the others — its own tab, equipment, locations, admin dashboard, booking log,
  per-faculty admins and scoped notification emails — because faculties are data-driven: the only code
  change is one entry in `DEFAULT_FACULTIES`. No migration; `create_default_faculties()` seeds it on
  deploy and is idempotent.
- It starts with **no admins**, so until a super admin grants rights in the Users section, only super
  admins can open its dashboard.

## v4.0.2 — 2026-10-25
- **Editing an item from the admin dashboard now returns to the items list.** The `edit_item` route
  already honoured a `next` field, but neither the dashboard's Edit link nor `edit_item.html` supplied
  one, so saving always landed on the public home page. The link now passes the dashboard URL
  (preserving the faculty), the form carries it through, and **Cancel** returns to the same place.
- **Hardened the `next` redirects.** Six routes redirected to a caller-supplied `next` without checking
  it — an open redirect, which this change would have leaned on more heavily. They now share
  `_safe_next()`, which accepts only same-site paths and logs anything else.

## v4.0.1 — 2026-10-19 — migrate.sh seeders never ran
- **`migrate.sh` aborted silently on every real run.** Its `run()` helper used `eval "$@"`, which
  re-parses its arguments and destroyed the quoting of the one multi-line command — the `is_bookable`
  fix. That failed with `Argument expected for the -c option`, and because the script runs under
  `set -e` everything after it was skipped: all the seeders, and (via `deploy.sh`) the service restart.
  `DRY_RUN=1` only echoes commands, so it never surfaced in a dry run.
  - Consequence: after deploying the faculty work, no faculties were seeded. With zero faculties the
    homepage renders **no tabs and all items**, i.e. indistinguishable from the old version — which is
    exactly how the broken deploy looked like "nothing changed".
- `run()` now executes `"$@"` directly; `run_py` runs seeder snippets; the backup does its own
  redirection, which cannot pass through either helper.
- The inline SQL became `fix_null_is_bookable()` in `main.py`, matching every other data fix.
- Verified by running `migrate.sh` for real (not `DRY_RUN`) end to end for the first time.

## v4.0 phases 3 & 4 — 2026-10-19 — Borrower profiles; All Bookings is active-only
- **`/user/<id>` — borrower profile.** Shows that person's whole borrowing record **across every
  faculty**, with per-status tallies and inline denial reasons. Visible to the person themselves and to
  **any admin of any faculty** — deliberately not scoped, since the point is that a Theatre admin can
  see how someone treated MISC's equipment.
- **`ProfileComment`** — admins of any faculty leave notes (e.g. on the state of returned items); the
  borrower can reply to each note, so a record is never one-sided. Authorship is shown as
  "Name — KIMO admin" or "Name (borrower)", with the faculty recorded at write time so old notes still
  read correctly after a role change. Top-level notes are admin-only; replies are open to the borrower
  and to admins.
- **`Booking.user_id`** links a booking to the account that made it. Previously only emails were stored,
  so a person's history could only be found by string-matching addresses — unreliable once someone has
  both an SSO and a local account. `backfill_booking_users()` links older rows by email, leaving
  ambiguous ones NULL rather than guessing; `bookings_for_user()` still falls back to email so
  pre-existing bookings appear.
- Borrower names in All Bookings, History and the Users list link to the profile.
- **All Bookings now lists active bookings only** (booked / approved / lent). Returned and denied are
  closed records and appear in History, which the heading links to.
- **DB migration required**: `9a030ac47afa`.

## v4.0 phase 2 — 2026-10-19 — Roles and scoped administration
- **`FacultyAdmin`** (user × faculty) is the source of truth for who administers what; `User.is_admin`
  becomes a denormalisation meaning "administers at least one faculty, or is a super admin", written
  only by `refresh_admin_flag()`.
- **`User.is_super_admin`** — administers every faculty and is the only role that may grant or revoke
  rights (`/admin/toggle_faculty_admin/<user>/<faculty>`, `/admin/toggle_super_admin/<user>`). Seeded
  from `SUPER_ADMINS`, defaulting to roberto.becerra@lmta.lt and edvinas.siliunas@lmta.lt. The last
  super admin cannot be demoted, and nobody can demote themselves.
- **`migrate_admin_roles_to_faculties()`** converts the old flag into grants — every existing admin
  became a MISC admin (7 grants, 2 super admins locally).
- **Notification emails are scoped**: `admin_notification_contacts(faculty)` returns that faculty's
  admins plus super admins. Subscription lives per grant, so an admin of two faculties can be notified
  about one and muted on the other.
- **Dashboard is per faculty**, with pills for the faculties you administer. Bookings, items, locations
  and history are filtered. An explicit `?faculty=` you don't administer returns 403; only a stale
  session falls back silently.
- **Cross-faculty actions are refused** — approve, lend, return, deny, booking JSON, item edit/delete and
  location edit/delete all check `require_faculty_admin`.
- **Fixed: `unit_tests.py` was running against the real MySQL database.** It set
  `SQLALCHEMY_DATABASE_URI` after `init_app`, which Flask-SQLAlchemy 3.x ignores, so `db.create_all()`
  ran against dev MySQL and created `faculty_admin` outside Alembic. The suite now swaps the engine and
  asserts it is on sqlite before creating anything.
- **DB migration required**: `491d4dacd8d2`.

## v4.0 phase 1 — 2026-10-19 — Faculties
First of four phases making one installation serve three faculties: **MISC**, **KIMO**
(Nacionalinė kino mokykla) and **Theatre & Dance**.

- **`Faculty` model** (`code`, `short_name`, `name`, `name_lt`, `sort_order`, `is_active`), seeded by
  `create_default_faculties()` from `DEFAULT_FACULTIES` in `main.py`.
- **`Item`, `Location` and `Booking` gain `faculty_id`.** `backfill_faculty_assignments()` assigns every
  pre-existing row to MISC — 152 rows locally (90 items, 6 locations, 56 bookings). Both run from
  `__main__` and from `migrate.sh`, matching how this project does data fixes (schema in Alembic,
  data in seeders).
- **Location names are unique per faculty**, not globally, so two faculties may each have a "Studio A".
- **Homepage tabs** switch faculty. `?faculty=<code>` makes links shareable; the choice is then
  remembered in the session, and opening an item switches to that item's faculty. An unknown code falls
  back rather than 404ing.
- **A booking cannot mix faculties** — approval, denial and notifications must resolve to one set of
  admins. Checked before per-item validation so the message is clear.
- Users are deliberately **not** scoped to a faculty: anyone may book from any faculty, which is what
  makes the shared borrowing history (phase 3) worth consulting.
- **DB migration required**: `0d9bbfc1f476`.

> Note: this revision's `downgrade()` drops unnamed foreign keys, which MySQL rejects. To roll back, restore
> the dump `migrate.sh` takes before migrating rather than using `flask db downgrade`.

## v3.6.3 — 2026-10-09
- **`deploy.sh` proves the service actually restarted.** It records the unit's `ActiveEnterTimestamp`
  before and after, and warns if the start time did not change — `is-active` alone passes even when the
  restart silently did nothing. A failed restart, or a service that is down afterwards, now exits
  non-zero with the command to investigate rather than printing "Deploy complete".
- **`migrate.sh` says plainly that it does not restart.** Running it instead of `./deploy.sh` leaves the
  new code on disk while gunicorn serves the old one, which looks exactly like a deploy that did
  nothing — the cause of the "sorting fix didn't work" report. It now ends with that warning and points
  at `./deploy.sh`. The warning is suppressed when `deploy.sh` invokes it, since that restarts for you.
- `--no-restart` spells out that the code is not live and gives the command to make it so.

## v3.6.2 — 2026-10-09
- **Admin tables keep their sorting across row actions.** Toggling admin rights or email subscription,
  deleting a user or item, or approving/denying a booking POSTs and redirects back to the same page,
  which reset the DataTable to its default order every time. `stateSave: true` is now set on
  `#usersTable`, `#bookingsAdminTable` (both in the dashboard and `/bookings_admin`), `#itemsTable` and
  `#historyTable`, so the sort column and direction, the search term, the page length and the current
  page all survive the redirect. State lives in the browser's `localStorage`, keyed per table and path,
  and expires after DataTables' default two hours.
- Deliberately not AJAX: the POST/redirect flow is unchanged, so flash messages, the back button and
  non-JS behaviour all stay as they were.

## v3.6.1 — 2026-10-09
- **`deploy.sh`**: one command for the whole deploy — virtualenv, `migrate.sh`, service restart, and a
  check that the service actually came back up. Supports `--pull`, `--no-restart`, `DRY_RUN=1` and
  `SERVICE_NAME`. Warns when the checkout is behind origin, not on `main`, or has local modifications —
  each of which has previously caused a deploy that appeared to succeed while serving old code.
- **`migrate.sh` activates `./venv` itself** instead of failing with `'python' not found` (Debian has no
  bare `python`). Both scripts now `cd` to their own directory, so running them from the wrong place
  can no longer migrate one database while systemd serves another.

## v3.6 — 2026-10-08
- **Fixed the Approve button in the admin notification email.** When the primary action was renamed from
  "Mark as Lent" to "Approve" in v3.3, `send_email()` kept passing `lend_url` to the template while the
  template read `{{ approve_url }}`. The variable was undefined, so the button rendered as
  `<a href="">` and did nothing. It now passes `approve_url`.
- **All Bookings table shows item names instead of the note.** The Note column is gone and the Items
  column lists each item's name (it previously showed only a count, which the names make redundant).
  The borrower's note is in the detail modal, opened by clicking the row.
- **Denial reasons are stored.** `Booking.denial_reason` records what the admin typed when denying;
  previously it was only used in the deny email and then discarded. It is returned by
  `/booking_detail_json/<id>` and shown in the detail modal, highlighted, for denied bookings only.
  `deny_no_note` still denies silently and records no reason.
- **DB migration required**: `62a223514ffe`.

## v3.5.1 — 2026-10-03
- **Password-manager autofill fix**: no template carried a single `autocomplete` attribute, so macOS
  Passwords/iCloud Keychain fell back to heuristics. The "Add Local User" form in the dashboard Users
  section is shaped exactly like a sign-in form (a `username` text input plus a `password` input), so the
  manager treated it as one and offered to *update* the saved `lmta.lt` credential while an admin was on
  `misc.lmta.lt/booking`. Explicit hints added:
  - `login.html`: `autocomplete="username"` / `current-password` — this is the real sign-in form.
  - Add Local User: `autocomplete="off"` on the form and username, `new-password` on the password, so the
    manager neither autofills an existing credential nor offers to update one.
  - Booking modal borrower fields: `name` / `email` / `tel`. Profile: `given-name` / `family-name` / `tel`.
- Addresses the long-standing note "iCloud passwords cannot deal with dynamically created modals" that
  was dropped from the TODO list in an earlier README rewrite.

## v3.5 — 2026-10-03
- **Admin notification recipients are now managed in the UI**, not hardcoded. The Users section of the
  admin dashboard gains an **Admin emails** column with a per-admin On/Off toggle
  (`/admin/toggle_admin_emails/<user_id>`), backed by `User.receives_admin_emails`.
- **Removed two hardcoded contact lists** that had already drifted apart — one in `book()` (with Julius
  and Mantautas commented out) and one in `send_email()` (with them active), so who got copied depended
  on which email was being sent. Both now call a single `admin_notification_contacts()`.
- Recipients are admins with `receives_admin_emails = True` **and a deliverable address**. Accounts like
  `admin` (no email) or `dummy` (invalid email) are skipped rather than handed to the mailer; the Users
  table flags such an account with a "no email" badge. Addresses are de-duplicated.
- **`seed_admin_notification_contacts()`** creates an admin `User` for anyone on the old hardcoded list
  who has no account yet, so nobody silently stops receiving notifications. It is idempotent, matches
  existing users on email or username, never modifies them, and gives new accounts an unusable random
  password (these people sign in via Microsoft SSO). It runs from `__main__` and from `migrate.sh`,
  since gunicorn never executes `__main__`. `LEGACY_ADMIN_CONTACTS` in `main.py` is only this seed list —
  after the first run, recipients are edited in the UI.
- Development behaviour is unchanged: when `MISC_DEV` is set, all admin email is still redirected to
  `DEV_ADMIN_CONTACTS` so local testing never mails the team.
- If no admin is subscribed, the app logs a warning and sends no admin email rather than falling back to
  the seed list — an admin who switches everyone off is obeyed.
- **DB migration required**: `e9a4b86a213f`.

## v3.4 — 2026-10-03
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

## v3.3 — 2026-10-03
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

## v3.2 — 2026-09-06
- **Dark mode fix — non-bookable items**: rows for `is_bookable = False` on the home page used Bootstrap's `.table-secondary`, which hard-codes a light grey background and ignores `data-bs-theme`, so in dark mode the row rendered as pale text on a pale block. Replaced with a theme-aware `.item-not-bookable` class in `booking.css` (subtle tint + `opacity: 0.7`, with a dark-mode override), legible in both themes.
- **Dev-mode startup fix**: the `__main__` block no longer overwrites `LOCALHOST` with `False` when `-d` is absent, so `MISC_DEV=true` in `.env`/the environment is honoured again and `python main.py` starts on port 5006 instead of the occupied default 5000. `-d`/`--dev` still forces dev mode on. The `scheduler.shutdown()` call in the shutdown handler is now guarded, so a startup failure reports the real error instead of masking it with `NameError: name 'scheduler' is not defined`.
- **Item export**: `/admin/export_items` streams the full item list as CSV, XLSX or PDF; an **Export items** dropdown sits next to the heading in the admin dashboard Items section.
  - CSV is written with a UTF-8 BOM so Excel opens Lithuanian characters correctly.
  - XLSX (via `openpyxl`) has a bold header row, frozen header, auto-filter and auto-sized columns.
  - PDF (via `reportlab`) is landscape A4 with a repeating header row.
  - New dependencies: `openpyxl`, `reportlab` — run `pip install -r requirements.txt` on the server after deploying. Both are imported lazily; if one is missing the route flashes an error instead of breaking the dashboard.

## v3.0 — 2026-03-03
- **Group booking**: one `Booking` record per cart session + one `BookingItem` per item (backwards-compatible with legacy single-item bookings).
- **Admin bookings page** (`/bookings_admin`): one row per Booking group; click to open detail modal with per-item dates and actions.
- **Location model**: DB-managed locations with CRUD at `/locations`; add/edit item forms use a dropdown instead of a free-text field.
- **Dark mode**: Tailwind CDN added alongside Bootstrap 5; OS preference detection + `localStorage` persistence; toggle button in navbar.
- **UI**: `add_item.html` and `edit_item.html` rewritten with Tailwind utility classes and dark mode support.
- **Nav**: removed stray Bootstrap 4 CDN script; added "All Bookings" and "Locations" admin links.

## v2.1 — 20251121
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

## v2.0 — 20240826
- Added navigation bar.
- Cart functionality for multi-item booking.
- Layout changes.
- Bulk booking.
