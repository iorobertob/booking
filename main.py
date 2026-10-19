import argparse
import re
from functools import wraps
from flask import session, Flask, render_template, request, redirect, url_for, flash, send_from_directory, send_file, jsonify, abort
from mailersend import MailerSendClient, EmailBuilder, IdentityBuilder
import mailersend
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timedelta
import pymysql
import json
import csv
import io
import os
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging
from logging.handlers import RotatingFileHandler
from collections import defaultdict
from msal import ConfidentialClientApplication
import time

load_dotenv()  # loads .env if present; no-op if absent
LOCALHOST = os.environ.get('MISC_DEV', 'false').lower() in ('1', 'true', 'yes')

# Set up the logging configuration
logging.basicConfig(level=logging.INFO)

# Create a custom logger
logger = logging.getLogger(__name__)

# Set up the file handler with a rotating log file
handler = RotatingFileHandler('mails_sent_log.txt', maxBytes=10000, backupCount=1)
handler.setLevel(logging.INFO)

# Create a logging format
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(handler)

pymysql.install_as_MySQLdb()

# Helper to read from env var first, then vars/vars.json fallback
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$')

def _borrower_info_for_user(user):
    """Build a borrower_info JSON string from a logged-in User, or return False."""
    name  = f"{user.first_name or ''} {user.last_name or ''}".strip()
    email_candidate = user.email or user.username or ''
    email = email_candidate if _EMAIL_RE.match(email_candidate) else ''
    phone = user.phone or ''
    if not (name or email):
        return False
    return json.dumps([{"borrower_name": name, "borrower_email": email, "borrower_phone": phone}])

_vars_json = None
def _cfg(env_key, json_key=None):
    val = os.environ.get(env_key)
    if val is not None:
        return val
    global _vars_json
    if _vars_json is None:
        try:
            with open('vars/vars.json') as f:
                _vars_json = json.load(f)
        except FileNotFoundError:
            _vars_json = {}
    return _vars_json.get(json_key or env_key)

SECRET_KEY      = _cfg('SECRET_KEY',           'secret_key')
DB_USERNAME     = _cfg('DB_USERNAME',           'db_username')
DB_PASSWORD     = _cfg('DB_PASSWORD',           'db_password')
DB_NAME         = _cfg('DB_NAME',               'database')
MAILERSEND_KEY  = _cfg('MAILERSEND_API_KEY',    'mailersend_api_key')
MISC_PASSWORD   = _cfg('MISC_PASSWORD',         'misc_password')
CLIENT_ID       = _cfg('AZURE_CLIENT_ID')
CLIENT_SECRET   = _cfg('AZURE_CLIENT_SECRET')
TENANT_ID       = _cfg('AZURE_TENANT_ID')

_admin_emails_raw = _cfg('ADMIN_EMAILS',        'admin_emails')
# ADMIN_EMAILS: env var is comma-separated string; vars.json is a list
if isinstance(_admin_emails_raw, str):
    ADMIN_EMAILS = set(e.strip().lower() for e in _admin_emails_raw.split(',') if e.strip())
else:
    ADMIN_EMAILS = set(e.lower() for e in (_admin_emails_raw or []))

_super_admins_raw = _cfg('SUPER_ADMINS', 'super_admins')
# Seeded as super admins on first run; they administer every faculty and are the
# only ones who may grant or revoke faculty admin rights.
if isinstance(_super_admins_raw, str):
    SUPER_ADMIN_EMAILS = set(e.strip().lower() for e in _super_admins_raw.split(',') if e.strip())
elif _super_admins_raw:
    SUPER_ADMIN_EMAILS = set(e.lower() for e in _super_admins_raw)
else:
    SUPER_ADMIN_EMAILS = {'roberto.becerra@lmta.lt', 'edvinas.siliunas@lmta.lt'}

AUTHORITY     = f"https://login.microsoftonline.com/{TENANT_ID}"
REDIRECT_PATH = "/getAToken"
SCOPE         = ["User.Read"]

# app = Flask(__name__, static_folder='images')
app = Flask(__name__)
# For prefix forwarding
app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

if not LOCALHOST:
    app.config['APPLICATION_ROOT'] = '/booking'
    scheduler = BackgroundScheduler(daemon=True)
    atexit.register(lambda: scheduler.shutdown())

app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql://{DB_USERNAME}:{DB_PASSWORD}@localhost/{DB_NAME}'
app.config['SECRET_KEY'] = SECRET_KEY

mailer = MailerSendClient(api_key=str(MAILERSEND_KEY))

db = SQLAlchemy(app)

migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'


# ── Faculties ─────────────────────────────────────────────────────────────────
# The system serves three faculties from one installation. Items, locations,
# bookings and admin rights belong to a faculty; USERS DO NOT — a user may book
# from any faculty, which is what makes the shared borrowing history on their
# profile worth consulting.
DEFAULT_FACULTY_CODE = 'misc'

# Seeded on first run. Edit here (and re-run the seeder) to rename or add one.
DEFAULT_FACULTIES = [
    {"code": "misc",    "short_name": "MISC",
     "name": "Music Studies Innovation Centre",
     "name_lt": "Muzikos inovacijų studijų centras", "sort_order": 1},
    {"code": "kimo",    "short_name": "KIMO",
     "name": "National Cinema School",
     "name_lt": "Nacionalinė kino mokykla", "sort_order": 2},
    {"code": "theatre", "short_name": "Theatre & Dance",
     "name": "Theatre and Dance",
     "name_lt": "Teatras ir šokis", "sort_order": 3},
]


class Faculty(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    code       = db.Column(db.String(30),  unique=True, nullable=False)  # URL slug: misc, kimo, theatre
    short_name = db.Column(db.String(40),  nullable=False)               # tab label
    name       = db.Column(db.String(150), nullable=False)               # English
    name_lt    = db.Column(db.String(150), nullable=True)                # Lithuanian
    sort_order = db.Column(db.Integer, default=0,    nullable=False)
    is_active  = db.Column(db.Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<Faculty {self.code}>"


def all_faculties():
    """Active faculties in tab order."""
    return Faculty.query.filter_by(is_active=True).order_by(Faculty.sort_order, Faculty.id).all()


def default_faculty():
    return Faculty.query.filter_by(code=DEFAULT_FACULTY_CODE).first() or Faculty.query.order_by(Faculty.sort_order).first()


def current_faculty():
    """The faculty currently being viewed.

    `?faculty=<code>` wins so links and tabs are shareable; otherwise the last
    one chosen in this session; otherwise the default. Always returns a row, so
    callers never have to handle None.
    """
    code = request.args.get('faculty')
    if code:
        fac = Faculty.query.filter_by(code=code).first()
        if fac:
            session['faculty_code'] = fac.code
            return fac
        # Unknown code in the URL: fall through rather than 404, so a stale
        # bookmark still lands somewhere usable.
    code = session.get('faculty_code')
    if code:
        fac = Faculty.query.filter_by(code=code).first()
        if fac:
            return fac
    return default_faculty()


def backfill_faculty_assignments():
    """Give every pre-faculty row a faculty.

    Everything that existed before the system became multi-faculty belonged to
    MISC, so NULL faculty_id is backfilled to the default faculty. Kept here
    rather than in the migration because this project does schema in Alembic and
    data fixes in seeders that migrate.sh runs (as with the is_bookable fix).
    Idempotent: only NULL rows are touched.
    """
    with app.app_context():
        fac = default_faculty()
        if not fac:
            logger.warning("No faculties seeded — skipping faculty backfill.")
            return
        total = 0
        for model in (Item, Location, Booking):
            n = model.query.filter(model.faculty_id.is_(None)).update(
                {model.faculty_id: fac.id}, synchronize_session=False)
            if n:
                logger.info("Backfilled %s %s row(s) to faculty '%s'", n, model.__name__, fac.code)
            total += n
        db.session.commit()
        print(f"  Assigned {total} pre-existing row(s) to '{fac.code}'.")


def migrate_admin_roles_to_faculties():
    """Turn the old single is_admin flag into per-faculty grants.

    Every existing admin administered what is now MISC, so each gets a
    FacultyAdmin row for the default faculty, carrying over their email
    subscription. Addresses in SUPER_ADMIN_EMAILS additionally become super
    admins. Idempotent: existing grants are left alone.
    """
    with app.app_context():
        fac = default_faculty()
        if not fac:
            logger.warning("No faculties seeded — skipping admin role migration.")
            return

        granted = promoted = 0
        for user in User.query.all():
            email = (user.notification_email or user.username or '').lower()

            if email in SUPER_ADMIN_EMAILS and not user.is_super_admin:
                user.is_super_admin = True
                promoted += 1
                logger.info("Promoted to super admin: %s", email)

            # Pre-faculty admins administered MISC.
            if user.is_admin and not user.admin_role_for(fac):
                db.session.add(FacultyAdmin(
                    user_id=user.id, faculty_id=fac.id,
                    receives_admin_emails=bool(user.receives_admin_emails)))
                granted += 1
                logger.info("Granted %s admin of '%s'", email or user.username, fac.code)

        db.session.flush()
        for user in User.query.all():
            refresh_admin_flag(user)
        db.session.commit()
        print(f"  {granted} faculty admin grant(s), {promoted} super admin(s).")


def faculty_filter(column, faculty):
    """Restrict `column` to `faculty`.

    The migration backfills every row, so a NULL faculty_id should not exist.
    The default faculty still claims NULLs as a safety net, so a row that slips
    through (e.g. created by an older release mid-deploy) stays visible instead
    of disappearing from every tab.
    """
    if faculty is None:
        return db.true()
    if faculty.code == DEFAULT_FACULTY_CODE:
        return db.or_(column == faculty.id, column.is_(None))
    return column == faculty.id


def items_for_faculty(faculty):
    return Item.query.filter(faculty_filter(Item.faculty_id, faculty)).all()


def locations_for_faculty(faculty):
    return Location.query.filter(
        faculty_filter(Location.faculty_id, faculty)
    ).order_by(Location.name).all()


def create_default_faculties():
    """Seed the three faculties. Existing rows are left untouched."""
    with app.app_context():
        for spec in DEFAULT_FACULTIES:
            if not Faculty.query.filter_by(code=spec["code"]).first():
                db.session.add(Faculty(**spec))
                logger.info("Seeded faculty: %s", spec["code"])
        db.session.commit()


# ── Booking statuses ──────────────────────────────────────────────────────────
# Lifecycle:  booked → approved → lent → returned
#                   ↘ denied
# ACTIVE statuses hold the item: they block other bookings over the same dates.
# TERMINAL statuses release it, so every availability/calendar query must exclude
# them — the booking row is kept for history instead of being deleted.
STATUS_BOOKED   = 'booked'      # request submitted, awaiting admin decision
STATUS_APPROVED = 'approved'    # admin approved; item reserved, not yet collected
STATUS_LENT     = 'lent'        # physically handed over to the borrower
STATUS_RETURNED = 'returned'    # physically returned — item free again
STATUS_DENIED   = 'denied'      # admin rejected the request — item free again

ACTIVE_STATUSES   = (STATUS_BOOKED, STATUS_APPROVED, STATUS_LENT)
TERMINAL_STATUSES = (STATUS_RETURNED, STATUS_DENIED)
ALL_STATUSES      = ACTIVE_STATUSES + TERMINAL_STATUSES

# Default booking length cap for a new item, in days counted inclusively
# (borrow Mon → return the following Mon = 8 days).
DEFAULT_MAX_BOOKING_DAYS = 8


def booking_length_days(borrow_date, return_date):
    """Length of a booking in days, counting both the borrow and return day."""
    return (return_date.date() - borrow_date.date()).days + 1 \
        if hasattr(borrow_date, 'date') else (return_date - borrow_date).days + 1


class Booking(db.Model):
    id              = db.Column(db.Integer,     primary_key=True)
    item_id         = db.Column(db.Integer,     db.ForeignKey('item.id'), nullable = False)
    item_name       = db.Column(db.String(100), nullable=False)
    borrower_name   = db.Column(db.String(100), nullable=False)
    borrower_email  = db.Column(db.String(100), nullable=False)
    user_email      = db.Column(db.String(100), nullable=False)
    borrower_phone  = db.Column(db.String(100), nullable=False)
    borrow_date     = db.Column(db.DateTime,    nullable=False)
    return_date     = db.Column(db.DateTime,    nullable=False)
    status          = db.Column(db.String(20),  default=STATUS_BOOKED)
    note            = db.Column(db.String(300), default='', nullable=True)
    # History-log timestamps. created_at is when the request was submitted;
    # status_changed_at is when it last moved along the lifecycle.
    created_at        = db.Column(db.DateTime, default=datetime.now, nullable=True)
    status_changed_at = db.Column(db.DateTime, default=datetime.now, nullable=True)
    # Why an admin denied the request. Kept separate from `note`, which is the
    # borrower's own reason for booking.
    denial_reason     = db.Column(db.String(300), nullable=True)
    # Which faculty's equipment this booking is for. A booking never spans two
    # faculties — otherwise "notify the faculty the activity came from" has no
    # single answer. Enforced when the booking is created.
    faculty_id        = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True, index=True)
    faculty           = db.relationship('Faculty')
    booking_items   = db.relationship('BookingItem', back_populates='booking', cascade='all, delete-orphan')
    item            = db.relationship('Item',   back_populates='bookings')

    def to_dict(self):
        return {
            "id"            : self.id,
            "item_id"       : self.item_id,
            "item_name"     : self.item_name,
            "borrower_name" : self.borrower_name,
            "borrower_email": self.borrower_email,
            "user_email"    : self.user_email,
            "borrower_phone": self.borrower_phone,  # commented in model
            "borrow_date"   : self.borrow_date.isoformat() if self.borrow_date else None,
            "return_date"   : self.return_date.isoformat() if self.return_date else None,
            "status"        : self.status,
            "note"          : self.note or '',
            "created_at"        : self.created_at.isoformat() if self.created_at else None,
            "status_changed_at" : self.status_changed_at.isoformat() if self.status_changed_at else None,
            "denial_reason"     : self.denial_reason or '',
        }

    def set_status(self, new_status):
        """Move the booking along the lifecycle and stamp when it happened."""
        self.status            = new_status
        self.status_changed_at = datetime.now()

# Define the Item model
class Item(db.Model):
    id              = db.Column(db.Integer,     primary_key=True)
    name            = db.Column(db.String(100), nullable=False)
    location        = db.Column(db.String(100), nullable=False)
    manual_link     = db.Column(db.String(200), default='')
    photo_path      = db.Column(db.String(200), default='')
    is_bookable     = db.Column(db.Boolean,     default=True, nullable=False)
    # Longest booking allowed for this item, counted inclusively: a borrow date of
    # Mon and a return date of the following Mon is 8 days. Set per item by admins.
    max_booking_days = db.Column(db.Integer, default=DEFAULT_MAX_BOOKING_DAYS,
                                 server_default=str(DEFAULT_MAX_BOOKING_DAYS), nullable=False)
    # The faculty that owns this piece of equipment.
    faculty_id       = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True, index=True)
    faculty          = db.relationship('Faculty')
    bookings        = db.relationship('Booking', order_by=Booking.id, back_populates='item')

class BookingItem(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    booking_id  = db.Column(db.Integer, db.ForeignKey('booking.id', ondelete='CASCADE'), nullable=False)
    item_id     = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=True)
    item_name   = db.Column(db.String(100), nullable=False)
    borrow_date = db.Column(db.DateTime, nullable=False)
    return_date = db.Column(db.DateTime, nullable=False)
    booking     = db.relationship('Booking', back_populates='booking_items')
    item        = db.relationship('Item')

    @property
    def borrower_name(self):  return self.booking.borrower_name
    @property
    def borrower_email(self): return self.booking.borrower_email
    @property
    def borrower_phone(self): return self.booking.borrower_phone
    @property
    def status(self):         return self.booking.status
    @property
    def note(self):           return self.booking.note


class Location(db.Model):
    id   = db.Column(db.Integer, primary_key=True)
    # Rooms are physical and belong to one faculty. The name is unique within a
    # faculty rather than globally, so two faculties may each have a "Studio A".
    name = db.Column(db.String(100), nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True, index=True)
    faculty    = db.relationship('Faculty')

    __table_args__ = (
        db.UniqueConstraint('faculty_id', 'name', name='uq_location_faculty_name'),
    )


# Define the User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username    = db.Column(db.String(100), unique=True, nullable=False)
    password    = db.Column(db.String(128), nullable=False)
    email       = db.Column(db.String(100), nullable=True)
    is_admin    = db.Column(db.Boolean, default=False)
    first_name  = db.Column(db.String(50),  nullable=True)
    last_name   = db.Column(db.String(50),  nullable=True)
    phone       = db.Column(db.String(30),  nullable=True)
    course      = db.Column(db.String(200), nullable=True)
    # Whether this admin is copied on booking notifications. Toggled per admin in
    # the dashboard Users section; ignored for non-admins.
    receives_admin_emails = db.Column(db.Boolean, default=True, nullable=False,
                                      server_default='1')
    # Super admins administer every faculty and are the only ones who may grant
    # or revoke faculty admin rights.
    is_super_admin = db.Column(db.Boolean, default=False, nullable=False,
                               server_default='0')

    # FacultyAdmin is the source of truth for WHICH faculties a user administers.
    # `is_admin` is a denormalisation meaning "administers at least one faculty,
    # or is a super admin"; it keeps @admin_required and the templates simple.
    # It is only ever written by refresh_admin_flag(), so the two cannot drift.

    @property
    def admin_faculties(self):
        """Faculties this user administers — all of them for a super admin."""
        if self.is_super_admin:
            return all_faculties()
        return [r.faculty for r in self.faculty_admin_roles if r.faculty]

    @property
    def admin_faculty_ids(self):
        return {f.id for f in self.admin_faculties}

    def is_admin_of(self, faculty):
        if self.is_super_admin:
            return True
        if faculty is None:
            return False
        return any(r.faculty_id == faculty.id for r in self.faculty_admin_roles)

    def admin_role_for(self, faculty):
        if faculty is None:
            return None
        return next((r for r in self.faculty_admin_roles if r.faculty_id == faculty.id), None)

    @property
    def notification_email(self):
        """Deliverable address for this user, or None.

        Local accounts often carry the address in `username` rather than `email`
        (see _borrower_info_for_user), and seeded accounts like 'admin'/'dummy'
        have no valid address at all — those must never reach the mailer.
        """
        for candidate in (self.email, self.username):
            if candidate and _EMAIL_RE.match(candidate.strip()):
                return candidate.strip().lower()
        return None

    @property
    def display_name(self):
        name = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return name or (self.username or '')


class FacultyAdmin(db.Model):
    """Grants one user admin rights over one faculty.

    Email subscription lives here rather than on User, so an admin of two
    faculties can be notified about one and not the other.
    """
    __tablename__ = 'faculty_admin'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False, index=True)
    receives_admin_emails = db.Column(db.Boolean, default=True, nullable=False, server_default='1')

    user    = db.relationship('User', backref=db.backref('faculty_admin_roles',
                                                         cascade='all, delete-orphan'))
    faculty = db.relationship('Faculty')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'faculty_id', name='uq_faculty_admin_user_faculty'),
    )


def refresh_admin_flag(user):
    """Keep User.is_admin in step with the grants. The single write path.

    Queries rather than reading user.faculty_admin_roles: a grant added by
    foreign key (or a pending delete) leaves that collection stale, which
    silently desynchronised the flag. Flushing first makes the pending change
    visible to the query.
    """
    db.session.flush()
    has_grant = db.session.query(FacultyAdmin.id).filter_by(user_id=user.id).first() is not None
    user.is_admin = bool(user.is_super_admin or has_grant)


class Programme(db.Model):
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)



def create_admin_user():
    """Create an admin user"""

    with app.app_context():

        db.create_all()

        admin_exists = User.query.filter_by(username='admin').first() is not None
        if not admin_exists:
            admin_user = User(username='admin', password=generate_password_hash(str(MISC_PASSWORD), method='pbkdf2'), is_admin=True)
            db.session.add(admin_user)
            db.session.commit()


def create_default_locations():
    """Pre-populate the default faculty with locations A, B, C."""
    with app.app_context():
        fac = default_faculty()
        if not fac:
            logger.warning("No faculties seeded yet — skipping default locations.")
            return
        for name in ['A', 'B', 'C']:
            if not Location.query.filter_by(name=name, faculty_id=fac.id).first():
                db.session.add(Location(name=name, faculty_id=fac.id))
        db.session.commit()


# ── Admin notification recipients ─────────────────────────────────────────────
# Who gets copied on booking notifications. This used to be two hardcoded lists
# (one in book(), one in send_email()) that had already drifted apart. It is now
# driven by the User table: any admin with receives_admin_emails = True and a
# deliverable address. Toggle it per admin in the dashboard Users section.
#
# LEGACY_ADMIN_CONTACTS is only a seed: on first run after this change,
# seed_admin_notification_contacts() creates an admin User for anyone on it who
# is not in the database yet, so nobody silently stops receiving notifications.
# After that it is unused — edit recipients in the UI, not here.
LEGACY_ADMIN_CONTACTS = [
    {"name": "Edvinas",       "email": "edvinas.siliunas@lmta.lt"},
    {"name": "Edvinas Gmail", "email": "siliunas.edvinas@gmail.com"},
    {"name": "Roberto",       "email": "roberto.becerra@lmta.lt"},
    {"name": "Julius",        "email": "julius.aglinskas@lmta.lt"},
    {"name": "Mantautas",     "email": "mantautas.krukauskas@lmta.lt"},
]

# In development every admin email is redirected here, so local testing never
# mails the real team. Unchanged from the previous DEV_ADMIN_CONTACTS behaviour.
DEV_ADMIN_CONTACTS = [
    {"name": "Roberto", "email": "roberto.becerra@lmta.lt"},
]


def admin_notification_contacts(faculty=None):
    """Admins subscribed to notifications for `faculty`.

    Activity belongs to exactly one faculty, so only that faculty's admins are
    notified. Super admins are included for every faculty, since they administer
    all of them; their subscription is their own `receives_admin_emails` flag,
    while a faculty admin's is the per-grant flag on FacultyAdmin — so someone
    administering two faculties can be notified about one and not the other.

    Returns [{"name", "email"}], de-duplicated by address. Admins without a
    deliverable address are skipped, rather than handed to the mailer.
    Passing faculty=None falls back to every subscribed admin, which only
    happens for mail that is not tied to a booking.
    """
    if LOCALHOST:
        return list(DEV_ADMIN_CONTACTS)

    contacts, seen = [], set()

    def add(user):
        email = user.notification_email
        if not email or email in seen:
            return
        seen.add(email)
        contacts.append({"name": user.display_name or email, "email": email})

    for user in User.query.filter_by(is_super_admin=True, receives_admin_emails=True).all():
        add(user)

    q = FacultyAdmin.query.filter_by(receives_admin_emails=True)
    if faculty is not None:
        q = q.filter_by(faculty_id=faculty.id)
    for role in q.all():
        if role.user:
            add(role.user)

    if not contacts:
        # Deliberate: respect an admin who switched everyone off rather than
        # resurrecting the seed list behind their back. Loud so it is diagnosable.
        logger.warning("No admin is subscribed to notifications for faculty %s — "
                       "admin emails will not be sent.",
                       faculty.code if faculty else "(any)")
    return contacts


def seed_admin_notification_contacts():
    """Make sure the previously hardcoded recipients still exist as admin users.

    Without this, switching from the hardcoded list to the User table would
    silently drop anyone who never had an account. Accounts are created with an
    unusable random password — these people sign in via Microsoft SSO.
    Existing users are matched on email or username and are never modified.
    """
    with app.app_context():
        for contact in LEGACY_ADMIN_CONTACTS:
            email = contact["email"].lower()
            existing = User.query.filter(
                db.or_(db.func.lower(User.email) == email,
                       db.func.lower(User.username) == email)
            ).first()
            if existing:
                continue
            user = User(
                username   = email,
                email      = email,
                # pbkdf2:sha256 like the SSO path — the default (scrypt) hash is
                # longer than User.password's String(128) and MySQL rejects it.
                password   = generate_password_hash(os.urandom(24).hex(),
                                                    method='pbkdf2:sha256'),
                is_admin   = True,
                first_name = contact["name"],
                receives_admin_emails = True,
            )
            db.session.add(user)
            db.session.flush()
            fac = default_faculty()
            if fac:
                db.session.add(FacultyAdmin(user_id=user.id, faculty_id=fac.id,
                                            receives_admin_emails=True))
            logger.info("Seeded admin notification contact: %s", email)
        db.session.commit()


def create_default_programmes():
    """Pre-populate Programme table with default study programmes."""
    with app.app_context():
        defaults = [
            'Music Innovations Studies',
            'Music Technologies',
            'Sound Engineering',
            'Composition',
            'Other',
        ]
        for name in defaults:
            if not Programme.query.filter_by(name=name).first():
                db.session.add(Programme(name=name))
        db.session.commit()


def admin_required(f):
    """Create admin only decorator"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def super_admin_required(f):
    """Only super admins. Used for granting/revoking admin rights."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_super_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def require_faculty_admin(faculty):
    """403 unless the current user administers `faculty` (super admins always do)."""
    if not current_user.is_authenticated or not current_user.is_admin_of(faculty):
        abort(403)


def admin_faculties_for(user):
    """Faculties whose admin area `user` may open, in tab order."""
    if not user.is_authenticated:
        return []
    return sorted(user.admin_faculties, key=lambda f: (f.sort_order, f.id))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
    # return User.query.get(int(user_id))


@app.route('/session-dump')
@admin_required
def session_dump():
    return jsonify(dict(session))


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/policy')
def policy():
    return render_template('policy.html')


@app.route('/')
def home():

    faculty = current_faculty()
    items = items_for_faculty(faculty)
    availability = check_all_items_availability(items)

    bookings = []
    for item in items:
        # New-style: lent booking via BookingItem
        booking = db.session.query(Booking).join(BookingItem).filter(
            BookingItem.item_id == item.id, Booking.status == STATUS_LENT
        ).first()
        # Fallback old-style
        if not booking:
            booking = Booking.query.filter_by(item_id=item.id, status=STATUS_LENT).first()
        bookings.append(booking)

    borrower_info_session = session.get('borrower_info')
    if borrower_info_session and borrower_info_session != {}:
        borrower_info = json.dumps(borrower_info_session)
    elif current_user.is_authenticated:
        borrower_info = _borrower_info_for_user(current_user)
    else:
        borrower_info = False

    if request.args.get('flash') == 'select_items':
        flash("Select at least one item before booking.", "warning")
        return redirect(url_for('home'))

    return render_template('home.html', items=items, availability=availability,
                           bookings=bookings, borrower_info=borrower_info,
                           faculties=all_faculties(), faculty=faculty)

"""
Set of auxiliary functions for MS Office Login
"""
def _build_msal_app():
    return ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET
    )


def _build_auth_code_flow(scopes=None):
    return _build_msal_app().initiate_auth_code_flow(
        scopes or [],
        redirect_uri=url_for("authorized", _external=True))


def is_microsoft_token_expired():
    expires_at = session.get("token_expires_at")
    return not expires_at or time.time() > expires_at


@app.route("/login_microsoft")
def login_microsoft():
    flow = _build_auth_code_flow(scopes=SCOPE)
    session["flow"]  = flow
    session["state"] = flow["state"]
    session.modified = True
    return redirect(flow["auth_uri"])


@app.route("/getAToken")
def authorized():

    expected_state  = session.get("state")
    received_state  = request.args.get("state")

    if not session.get("state") or session["state"] != request.args.get("state"):
        session.pop("state", None)
        flash("Session expired or invalid. Try again.", "warning")
        return redirect(url_for("login"))

    if not expected_state or expected_state != received_state:
        session.pop("state", None)
        flash("Your login session expired. Please try again.", "warning")
        return redirect(url_for("login"))

    try:
        result = _build_msal_app().acquire_token_by_auth_code_flow(
            session.get("flow", {}), request.args)
    except ValueError:
        flash("Invalid login state. Please start again.", "danger")
        return redirect(url_for("login"))

    # Clear session state
    session.pop("state", None)
    session.pop("flow", None)
    
    if "id_token_claims" in result:

        # Set expiration time for this login. 
        session["token_expires_at"] = int(time.time()) + result.get("expires_in", 3600)

        claims = result["id_token_claims"]

        # Extract basic user info
        email       = claims.get("preferred_username") or claims.get("email")
        
        full_name = claims.get("name", "")
        name_parts = full_name.split(" ")

        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name  = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Store original claims in session
        session["microsoft_user"] = claims

        borrower_info = []
        borrower_info.append({
            "borrower_name"     : first_name,
            "borrower_email"    : email,
            "borrower_phone"    : ''
        })
        session['borrower_info'] = borrower_info
        session.modified = True

        session['user_email'] = email

        # Try to find existing user by email
        user = User.query.filter_by(username=email).first()

        is_new_user = False
        if not user:
            is_new_user = True
            user = User(
                username    = email,
                email       = email,
                first_name  = first_name,
                last_name   = last_name,
                password    = generate_password_hash("office_placeholder_password", method='pbkdf2:sha256'),
                is_admin    = email.lower() in ADMIN_EMAILS
            )
            db.session.add(user)
            db.session.commit()

        # Log the user in
        login_user(user)

        if is_new_user:
            flash('Welcome! Please complete your profile.', 'info')
            return redirect(url_for('profile'))

    return redirect(url_for("home"))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            response = login_user(user, remember=True)

            flash('Login successful', 'success')
            return redirect(url_for('home'))
        else:
            flash('Login unsuccessful. Please check your username and password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():

    logout_user()

    pop_session()
    
    return redirect(url_for('home'))

def pop_session():
    # Remove any custom session keys, but keep Flask-Login's session structure intact
    session.pop("microsoft_user", None)
    session.pop("flow", None)
    session.pop("borrower_info", None)
    session.pop("user_email", None)
    # Don't use session.clear()
    session.modified = True  #  force session to update in some Flask versions


@app.route('/set_borrower', methods=['POST'])
@login_required
def set_borrower():
    """Saver the borrower's info to the current session"""

    data = request.get_json()

    borrower_info = []
    borrower_info.append({
        "borrower_name"     : data.get('name'),
        "borrower_email"    : data.get('contact'),
        "borrower_phone"    : data.get('phone'),
    })
    session['borrower_info'] = borrower_info
    session.modified = True

    return jsonify(status='ok', message='Borrower info saved to session.')


@app.route('/item/<int:item_id>', methods=['GET', 'POST'])
def item_details(item_id):
    item = Item.query.get_or_404(item_id)
    if item.faculty_id:
        fac = Faculty.query.get(item.faculty_id)
        if fac:
            session['faculty_code'] = fac.code

    # Only active bookings: this table answers "when is this item taken?", so a
    # returned or denied booking would wrongly make a free period look occupied.
    # 'approved' counts as active — it holds the dates just like 'lent'.
    # The full record of past bookings lives in the admin History section.
    # Old-style (no BookingItem children)
    old_bookings = Booking.query.filter(
        Booking.item_id == item_id,
        ~Booking.booking_items.any(),
        Booking.status.in_(ACTIVE_STATUSES),
    ).all()
    # New-style: Booking objects whose BookingItems reference this item
    new_bookings = db.session.query(Booking).join(BookingItem).filter(
        BookingItem.item_id == item_id,
        Booking.status.in_(ACTIVE_STATUSES),
    ).all()
    # Soonest first, so an upcoming free period is easy to spot
    bookings = sorted(old_bookings + new_bookings, key=lambda b: b.borrow_date)

    booking_dates = get_bookings_list(item_id=item_id)
    booked_dates  = []

    for booking in booking_dates:
        booked_dates.extend(get_all_dates_between(booking["borrow_date"], booking["return_date"]))
    # Convert dates to string format
    booked_dates_str = [date.strftime('%Y-%m-%d') for date in booked_dates]

    item_for_cart = row2dict(item)
    booked_dates  = json.dumps(booked_dates_str)

    borrower_info_session = session.get('borrower_info')
    if borrower_info_session and borrower_info_session != {}:
        borrower_info = json.dumps(borrower_info_session)
    elif current_user.is_authenticated:
        borrower_info = _borrower_info_for_user(current_user)
    else:
        borrower_info = False

    if request.args.get('flash') == 'select_items':
        flash("Select at least one item before booking.", "warning")
        return redirect(url_for('item_details'))

    return render_template('item_details.html', borrower_info=borrower_info, item=item, item_for_cart = item_for_cart, bookings=bookings, booking_dates=booking_dates, booked_dates=booked_dates)


def row2dict(row):
    """
    Utility function to get a dict from an SQALchemy result that is only one item (row) and
    convert it to a dict, for the purpose of building a JSON
    """
    d = {}
    for column in row.__table__.columns:
        d[column.name] = str(getattr(row, column.name))

    return d


def check_all_items_availability(items=None):
    """Availability strings, positionally aligned with `items`.

    Callers pass the exact list they are rendering (e.g. one faculty's items);
    the two lists must stay in step, so never re-query here.
    """
    if items is None:
        items = Item.query.all()
    now   = datetime.now()
    availability = []
    for item in items:
        if is_item_available(item.id, now, now):
            availability.append("Available")
        else:
            status = None
            now_overlap = db.or_(
                db.and_(BookingItem.borrow_date <= now, BookingItem.return_date >= now),
                db.and_(BookingItem.borrow_date >= now, BookingItem.return_date <= now),
            )
            bi = db.session.query(BookingItem).join(BookingItem.booking).filter(
                BookingItem.item_id == item.id,
                Booking.status.in_(ACTIVE_STATUSES),
                now_overlap,
            ).first()
            if bi:
                status = bi.booking.status
            else:
                old = Booking.query.filter(
                    Booking.item_id == item.id,
                    ~Booking.booking_items.any(),
                    Booking.status.in_(ACTIVE_STATUSES),
                    db.or_(
                        db.and_(Booking.borrow_date <= now, Booking.return_date >= now),
                        db.and_(Booking.borrow_date >= now, Booking.return_date <= now),
                    ),
                ).first()
                if old:
                    status = old.status
            availability.append(status.capitalize() if status else "Lent/booked")
    return availability


def is_item_available(item_id, start_date, end_date):
    overlap = db.or_(
        db.and_(BookingItem.borrow_date <= start_date, BookingItem.return_date >= start_date),
        db.and_(BookingItem.borrow_date <= end_date,   BookingItem.return_date >= end_date),
        db.and_(BookingItem.borrow_date >= start_date, BookingItem.return_date <= end_date),
    )
    # New-style: check BookingItem table. Returned/denied bookings are kept as
    # history, so they must not count as holding the item.
    if db.session.query(BookingItem).join(BookingItem.booking).filter(
        BookingItem.item_id == item_id,
        Booking.status.in_(ACTIVE_STATUSES),
        overlap,
    ).first():
        return False
    # Old-style: Booking without BookingItem children
    old_overlap = db.or_(
        db.and_(Booking.borrow_date <= start_date, Booking.return_date >= start_date),
        db.and_(Booking.borrow_date <= end_date,   Booking.return_date >= end_date),
        db.and_(Booking.borrow_date >= start_date, Booking.return_date <= end_date),
    )
    return Booking.query.filter(
        Booking.item_id == item_id,
        ~Booking.booking_items.any(),
        Booking.status.in_(ACTIVE_STATUSES),
        old_overlap,
    ).first() is None


def get_bookings_list(item_id):
    """Fetch all booking date ranges for this item (old-style and new-style).

    Only ACTIVE bookings block the calendar — returned and denied ones are kept
    as history and their dates must become selectable again.
    """
    bookings_list = []
    # Old-style: Booking directly on item, no BookingItem children
    for b in Booking.query.filter(
        Booking.item_id == item_id,
        ~Booking.booking_items.any(),
        Booking.status.in_(ACTIVE_STATUSES),
    ).all():
        bookings_list.append({"borrow_date": b.borrow_date, "return_date": b.return_date, "borrower_name": b.borrower_name})
    # New-style: via BookingItem
    for bi in BookingItem.query.join(BookingItem.booking).filter(
        BookingItem.item_id == item_id,
        Booking.status.in_(ACTIVE_STATUSES),
    ).all():
        bookings_list.append({"borrow_date": bi.borrow_date, "return_date": bi.return_date, "borrower_name": bi.booking.borrower_name})
    return bookings_list


def get_all_dates_between(start_date, end_date):
    """
    Generate all dates between two dates, 
    inclusive of both start and end dates
    """

    delta = end_date - start_date       # timedelta
    return [start_date + timedelta(days=i) for i in range(delta.days + 1)]


@app.route('/book', methods=['POST'])
@login_required
def book():
    """
    Main book function. Creates ONE Booking per cart session + one BookingItem per item.
    """
    if session.get("microsoft_user") and is_microsoft_token_expired():
        logout_user()
        pop_session()
        flash("Your session has expired. Please log in again.", "warning")
        return redirect(url_for('login'))

    borrower_info_list = session.get('borrower_info')
    if borrower_info_list:
        borrower_info  = borrower_info_list[0]
        borrower_name  = borrower_info["borrower_name"]
        borrower_email = borrower_info["borrower_email"]
        borrower_phone = borrower_info["borrower_phone"]
    else:
        items_raw_fb = request.form.get('itemsJSON')
        if items_raw_fb:
            fb = json.loads(items_raw_fb)
            if fb:
                borrower_name  = fb[0].get('borrower_name', '')
                borrower_email = fb[0].get('borrower_email', '')
                borrower_phone = fb[0].get('borrower_phone', '')
            else:
                flash('Booking info missing. Please try again.', 'danger')
                return redirect(url_for('home'))
        else:
            flash('Booking info missing. Please try again.', 'danger')
            return redirect(url_for('home'))
    user_email     = session.get('user_email', '')

    items_raw  = request.form.get('itemsJSON')
    if isinstance(items_raw, dict):
        items_list = [items_raw]
    elif isinstance(items_raw, str):
        items_list = json.loads(items_raw)
    else:
        items_list = items_raw

    note = items_list[0].get('note', '') if items_list else ''

    # A booking belongs to exactly one faculty: approval, denial and the
    # notification emails all have to resolve to a single set of admins.
    # Checked before per-item validation so this structural problem is reported
    # plainly, instead of surfacing as whichever item happens to fail first.
    faculty_ids = {Item.query.get_or_404(i["id"]).faculty_id for i in items_list}
    if len(faculty_ids) > 1:
        names = sorted(
            (Faculty.query.get(fid).short_name if fid else DEFAULT_FACULTY_CODE.upper())
            for fid in faculty_ids
        )
        flash(f'A booking cannot mix equipment from different faculties '
              f'({", ".join(names)}). Please book them separately.', 'danger')
        return redirect(url_for('cart'))
    booking_faculty_id = next(iter(faculty_ids)) if faculty_ids else None

    # Validate all items before writing anything
    for single_item in items_list:
        item = Item.query.get_or_404(single_item["id"])
        if item.is_bookable is False:
            flash(f'{item.name} is not available for booking.', 'danger')
            return redirect(url_for('cart'))
        borrow_date = datetime.strptime(single_item['borrow_date'], '%Y-%m-%d')
        return_date = datetime.strptime(single_item['return_date'], '%Y-%m-%d')
        if return_date < borrow_date:
            flash('Return date must be after borrow date.', 'danger')
            return redirect(url_for('cart'))
        max_days = item.max_booking_days or DEFAULT_MAX_BOOKING_DAYS
        if booking_length_days(borrow_date, return_date) > max_days:
            flash(f'{item.name} can be booked for at most {max_days} days.', 'danger')
            return redirect(url_for('cart'))
        if not is_item_available(item.id, borrow_date, return_date):
            flash(f'Selected dates are not available for {item.name}.', 'danger')
            return redirect(url_for('cart'))

    # Create ONE Booking (first item used for backwards-compat fields)
    first      = items_list[0]
    first_item = Item.query.get(first['id'])
    first_borrow = datetime.strptime(first['borrow_date'], '%Y-%m-%d')
    first_return = datetime.strptime(first['return_date'], '%Y-%m-%d')

    new_booking = Booking(
        item_id        = first_item.id,
        item_name      = first_item.name,
        faculty_id     = booking_faculty_id,
        borrower_name  = borrower_name,
        borrower_email = borrower_email,
        borrower_phone = borrower_phone,
        user_email     = user_email,
        borrow_date    = first_borrow,
        return_date    = first_return,
        note           = note,
    )
    db.session.add(new_booking)
    db.session.flush()  # get new_booking.id without committing

    # Create one BookingItem per item
    for single_item in items_list:
        item        = Item.query.get(single_item["id"])
        borrow_date = datetime.strptime(single_item['borrow_date'], '%Y-%m-%d')
        return_date = datetime.strptime(single_item['return_date'], '%Y-%m-%d')
        db.session.add(BookingItem(
            booking_id  = new_booking.id,
            item_id     = item.id,
            item_name   = item.name,
            borrow_date = borrow_date,
            return_date = return_date,
        ))

    db.session.commit()

    # 1. Send confirmation to borrower only
    send_email(
        borrower_email = borrower_email,
        borrower_name  = borrower_name,
        borrower_phone = borrower_phone,
        borrow_date    = first_borrow.date(),
        return_date    = first_return.date(),
        subject        = "Booking - Do Not Reply",
        text_content   = "",
        html_content   = "",
        items          = new_booking.booking_items,
        type_of_mail   = 'booking',
        user_email     = user_email,
        recipients     = [{"name": borrower_name, "email": borrower_email}],
    )

    # 2. Send admin notification with Approve / Deny action links.
    #    Only the admins of the faculty this booking belongs to.
    admin_contacts = admin_notification_contacts(new_booking.faculty)
    approve_url = url_for('approve_booking', booking_id=new_booking.id, _external=True)
    deny_url    = url_for('deny_booking',    booking_id=new_booking.id, _external=True)
    send_email(
        borrower_email = borrower_email,
        borrower_name  = borrower_name,
        borrower_phone = borrower_phone,
        borrow_date    = first_borrow.date(),
        return_date    = first_return.date(),
        subject        = "New Booking Request - MISC",
        text_content   = "",
        html_content   = "",
        items          = new_booking.booking_items,
        type_of_mail   = 'booking_admin',
        user_email     = user_email,
        approve_url    = approve_url,
        deny_url       = deny_url,
        note           = note,
        recipients     = admin_contacts,
    )

    flash('All items booked successfully!', 'success')
    session['cart'] = {}
    return redirect(url_for('home'))



@app.route('/bulk_details', methods=['GET'])
def bulk_details():
    """Get the details of items to book on bulk"""

    item_ids    = request.args.getlist('items')
    items       = Item.query.filter(Item.id.in_(item_ids)).all()

    all_booking_dates = []

    for item in items:
        bookings     = get_bookings_list(item_id=item.id)
        booked_dates = []
        for booking in bookings:
            booked_dates.extend(get_all_dates_between(booking["borrow_date"], booking["return_date"]))
        # Convert dates to string format
        booked_dates_str = [date.strftime('%Y-%m-%d') for date in booked_dates]

        all_booking_dates += booked_dates_str

    # Convert items to a list of dictionaries
    items_dicts = [model_to_dict(item) for item in items]

    return jsonify({
        "items"         : items_dicts,
        "booked_dates"  : json.dumps(all_booking_dates),
    }) 


@app.route('/bookings_list', methods=['GET'])
@admin_required
def bookings_list():
    """ Display all bookings on the database """

    if session.get("microsoft_user") and is_microsoft_token_expired():
        logout_user()

        pop_session()

        flash("Your session has expired. Please log in again.", "warning")
        return redirect(url_for('login'))

    # Active bookings only — this is a working list with lend/return actions.
    # Closed bookings are in the admin dashboard History section.
    bookings = Booking.query.filter(
        Booking.status.in_(ACTIVE_STATUSES)
    ).order_by(Booking.borrow_date.desc()).all()
    return render_template('bookings_list.html', bookings=bookings)


@app.route('/book_cart', methods=['POST','GET'])
@login_required
def book_cart():
    """
    Main "add to cart" function. For single or bulk addition 
    """

    borrower_name       = request.form.get("borrower_name")
    borrower_email      = request.form.get("borrower_email")
    borrower_phone      = request.form.get("borrower_phone")
    booking_note        = request.form.get("booking_note", "")
    items_json = request.form.get('itemsJSON')

    if not items_json:
        flash('No items selected. Please try again.', 'danger')
        return redirect(url_for('home'))
    json_data = json.loads(items_json)
    if not json_data:
        flash('No items selected. Please try again.', 'danger')
        return redirect(url_for('home'))

    # Set items to the session
    if 'cart' not in session:
        session['cart'] = []
        cart_items = []
    else:
        cart_items=session.get('cart')
        if session.get('cart') == {}:
            cart_items = []

    # Set borrower info to the session
    borrower_info = []
    borrower_info.append({
        "borrower_name"     : borrower_name,
        "borrower_email"    : borrower_email,
        "borrower_phone"    : borrower_phone ,
    })
    session['borrower_info'] = borrower_info

    for item in json_data:
        item_obj = Item.query.get(item['id'])
        if item_obj and item_obj.is_bookable is False:
            flash(f'{item_obj.name} is not available for booking.', 'danger')
            return redirect(url_for('home'))

        # Convert dates from string to date objects if needed
        borrow_date_str = item.get("borrow_date")
        return_date_str = item.get("return_date")
        if not borrow_date_str or not return_date_str:
            flash('Please select borrow and return dates for all items.', 'danger')
            return redirect(url_for('home'))
        borrow_date = datetime.strptime(borrow_date_str, '%Y-%m-%d')
        return_date = datetime.strptime(return_date_str, '%Y-%m-%d')

        if item_obj:
            max_days = item_obj.max_booking_days or DEFAULT_MAX_BOOKING_DAYS
            if booking_length_days(borrow_date, return_date) > max_days:
                flash(f'{item_obj.name} can be booked for at most {max_days} days.', 'danger')
                return redirect(url_for('home'))

        # Append to our cart_items list
        cart_items.append({
            "borrower_name"     : borrower_name,
            "borrower_email"    : borrower_email,
            "borrower_phone"    : borrower_phone,
            "note"              : booking_note,
            "id"                : item['id'],
            "name"              : item['name'],
            "location"          : item['location'],
            "max_booking_days"  : (item_obj.max_booking_days if item_obj else None) or DEFAULT_MAX_BOOKING_DAYS,
            "borrow_date"       : borrow_date.strftime('%Y-%m-%d'),  # Convert back to string for JSON serialization
            "return_date"       : return_date.strftime('%Y-%m-%d')
        })

    # Save to session (as JSON)
    session['cart'] = cart_items
    session.modified = True

    flash("Successfully added to cart", 'success' )

    return redirect(url_for('cart'))



def model_to_dict(model_instance):
    """
    Convert an SQLAlchemy model instance into a dictionary.
    Args: model_instance (db.Model): The SQLAlchemy model instance.
    Returns: dict: A dictionary representation of the model instance.
    """
    
    return {column.name: getattr(model_instance, column.name) for column in model_instance.__table__.columns}


@app.route('/cart')
@login_required
def cart():
    """ Display cart page"""

    # Combine item details with booking info from the session
    items_with_booking_info = []

    # Retrieve cart items from session
    cart_items = session.get('cart', '[]')  # Store as a JSON string
    all_booking_dates = []

    if cart_items != {} and cart_items != '[]':

        # Extract item IDs from cart items
        item_ids = [item['id'] for item in cart_items]

        
        # TODO: This maybe does not need to be sent to the front end and then back to the back
        for item_id in item_ids:
            bookings     = get_bookings_list(item_id=item_id)
            booked_dates = []
            for booking in bookings:
                booked_dates.extend(get_all_dates_between(booking["borrow_date"], booking["return_date"]))
            # Convert dates to string format
            booked_dates_str = [date.strftime('%Y-%m-%d') for date in booked_dates]

            all_booking_dates += booked_dates_str
        
        # Re-read caps from the DB rather than trusting the session copy, so an
        # admin change to max_booking_days applies to carts already in flight.
        max_days_by_id = {
            i.id: (i.max_booking_days or DEFAULT_MAX_BOOKING_DAYS)
            for i in Item.query.filter(Item.id.in_(item_ids)).all()
        }

        for cart_item in cart_items:
            item_detail = {}
            item_detail['id']               = cart_item.get('id')
            item_detail['name']             = cart_item.get('name')
            item_detail['location']         = cart_item.get('location')
            item_detail['borrow_date']      = cart_item.get('borrow_date')
            item_detail['return_date']      = cart_item.get('return_date')
            item_detail['borrower_name']    = cart_item.get('borrower_name')
            item_detail['borrower_email']   = cart_item.get('borrower_email')
            item_detail['borrower_phone']   = cart_item.get('borrower_phone')
            item_detail['max_booking_days'] = max_days_by_id.get(
                cart_item.get('id'), cart_item.get('max_booking_days') or DEFAULT_MAX_BOOKING_DAYS)

            items_with_booking_info.append(item_detail)

    items_for_cart = items_with_booking_info

    borrower_info_session = session.get('borrower_info')
    if borrower_info_session and borrower_info_session != {}:
        borrower_info = json.dumps(borrower_info_session)
    elif current_user.is_authenticated:
        borrower_info = _borrower_info_for_user(current_user)
    else:
        borrower_info = False

    return render_template('cart.html', items=items_with_booking_info, items_for_cart = items_for_cart, borrower_info=borrower_info, booked_dates=all_booking_dates)


@app.route('/remove_from_cart/<item_id>', methods=['GET', 'POST'])
@login_required
def remove_from_cart(item_id):
    cart = session.get('cart', [])

    if item_id == 'all':
        session['cart'] = {}
        flash('Cart emptied successfully', 'success')
    else:
        new_cart = [item for item in cart if str(item.get('id')) != str(item_id)]
        if len(new_cart) < len(cart):
            session['cart'] = new_cart
            flash('Item removed from cart successfully', 'success')
        else:
            flash('Item not found in cart', 'error')

    return redirect(url_for('cart'))


@app.route('/approve/<int:booking_id>')
@admin_required
def approve_booking(booking_id):
    """Admin accepts the request. The item stays reserved until it is collected."""
    booking = Booking.query.get_or_404(booking_id)
    # An admin may only act on bookings belonging to a faculty they administer.
    require_faculty_admin(booking.faculty)

    if booking.status in TERMINAL_STATUSES:
        flash(f'Booking {booking.id} is already {booking.status} and cannot be approved.', 'warning')
        return redirect(request.referrer or url_for('admin_dashboard', section='bookings'))

    booking.set_status(STATUS_APPROVED)
    db.session.commit()

    items_for_email = booking.booking_items if booking.booking_items else [booking]
    send_email(
        borrower_email = booking.borrower_email,
        borrower_name  = booking.borrower_name,
        borrower_phone = booking.borrower_phone,
        borrow_date    = booking.borrow_date.date(),
        return_date    = booking.return_date.date(),
        subject        = "Booking approved — ready to collect — Do Not Reply",
        text_content   = "",
        html_content   = "",
        items          = items_for_email,
        type_of_mail   = 'approved',
        faculty        = booking.faculty,
    )

    flash(f'Booking {booking.id} approved — borrower notified to collect.', 'success')
    return redirect(request.referrer or url_for('admin_dashboard', section='bookings'))


@app.route('/lend/<int:booking_id>')
@admin_required
def lend_item(booking_id):
    """Item physically handed over. Reachable from 'booked' too, so the admin can
    still lend in one step from the booking-request email."""
    booking = Booking.query.get_or_404(booking_id)
    # An admin may only act on bookings belonging to a faculty they administer.
    require_faculty_admin(booking.faculty)

    if booking.status in TERMINAL_STATUSES:
        flash(f'Booking {booking.id} is already {booking.status} and cannot be lent.', 'warning')
        return redirect(request.referrer or url_for('admin_dashboard', section='bookings'))

    booking.set_status(STATUS_LENT)
    db.session.commit()

    items_for_email = booking.booking_items if booking.booking_items else [booking]
    send_email(
        borrower_email = booking.borrower_email,
        borrower_name  = booking.borrower_name,
        borrower_phone = booking.borrower_phone,
        borrow_date    = booking.borrow_date.date(),
        return_date    = booking.return_date.date(),
        subject        = "Item(s) collected — Do Not Reply",
        text_content   = "",
        html_content   = "",
        items          = items_for_email,
        type_of_mail   = 'lent',
        faculty        = booking.faculty,
    )

    flash(f'Item {booking.item_name} marked as lent!', 'success')
    return redirect(request.referrer or url_for('home'))


@app.route('/return/<int:booking_id>', methods=['POST', 'GET'])
@admin_required
def return_item(booking_id):
    """Close a booking: 'returned' for a genuine return, 'denied' when the admin
    rejects the request. The row is kept as history — the status change is what
    releases the item, since every availability query ignores terminal statuses."""
    booking = Booking.query.get_or_404(booking_id)
    # An admin may only act on bookings belonging to a faculty they administer.
    require_faculty_admin(booking.faculty)

    actionType = request.form.get("formAction")
    note       = request.form.get('note')

    if booking.status in TERMINAL_STATUSES:
        flash(f'Booking {booking.id} is already {booking.status}.', 'warning')
        return redirect(url_for('admin_dashboard', section='bookings'))

    is_deny = actionType in ('deny', 'deny_no_note')
    if is_deny:
        # Keep the reason even for deny_no_note (no email, but the record stands).
        booking.denial_reason = (note or '').strip() or None
    booking.set_status(STATUS_DENIED if is_deny else STATUS_RETURNED)
    db.session.commit()

    items_for_email = booking.booking_items if booking.booking_items else [booking]

    if is_deny:
        flash(f'Booking for {booking.item_name} denied.', 'success')
    else:
        flash(f'Item {booking.item_name} marked as returned!', 'success')

    if actionType == 'deny':
        send_email(
            borrower_email = booking.borrower_email,
            borrower_name  = booking.borrower_name,
            borrower_phone = booking.borrower_phone,
            borrow_date    = booking.borrow_date.date(),
            return_date    = booking.return_date.date(),
            subject        = "Booking denied — Do Not Reply",
            text_content   = "",
            html_content   = "",
            items          = items_for_email,
            type_of_mail   = 'deny',
        faculty        = booking.faculty,
            note           = note,
        )
    elif actionType == 'deny_no_note':
        pass  # closed silently; no email sent
    else:
        send_email(
            borrower_email = booking.borrower_email,
            borrower_name  = booking.borrower_name,
            borrower_phone = booking.borrower_phone,
            borrow_date    = booking.borrow_date.date(),
            return_date    = booking.return_date.date(),
            subject        = "Item returned — Thank you — Do Not Reply",
            text_content   = "",
            html_content   = "",
            items          = items_for_email,
            type_of_mail   = 'returned',
        faculty        = booking.faculty,
        )

    if actionType in ('deny', 'deny_no_note'):
        return redirect(url_for('admin_dashboard', section='bookings'))
    return redirect(request.referrer or url_for('home'))


def _max_booking_days_from_form(form, fallback=DEFAULT_MAX_BOOKING_DAYS):
    """Read max_booking_days from an admin form, falling back on blank/invalid input."""
    raw = (form.get('max_booking_days') or '').strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        return fallback
    return max(1, value)


@app.route('/add_item', methods=['GET', 'POST'])
@admin_required
def add_item():
    if request.method == 'POST':
        name        = request.form.get('name')
        location    = request.form.get('location')
        is_bookable = 'is_bookable' in request.form
        faculty = current_faculty()
        new_item = Item(name=name, location=location, is_bookable=is_bookable,
                        max_booking_days=_max_booking_days_from_form(request.form),
                        faculty_id=faculty.id if faculty else None)
        db.session.add(new_item)
        db.session.commit()
        flash(f'Item {name} added successfully!', 'success')
        next_url = request.form.get('next') or url_for('home')
        return redirect(next_url)

    faculty = current_faculty()
    return render_template('add_item.html', locations=locations_for_faculty(faculty),
                           faculty=faculty)


@app.route('/edit_item/<int:item_id>', methods=['GET', 'POST'])
@admin_required
def edit_item(item_id):
    if request.method == 'GET':
        item = Item.query.get_or_404(item_id)
        require_faculty_admin(item.faculty)
        return render_template('edit_item.html', item=item,
                               locations=locations_for_faculty(item.faculty),
                               faculty=item.faculty)

    if request.method == 'POST':
        name        = request.form.get('name')
        location    = request.form.get('location')
        existing_item = Item.query.get_or_404(item_id)
        require_faculty_admin(existing_item.faculty)
        existing_item.name        = name
        existing_item.location    = location
        existing_item.is_bookable = 'is_bookable' in request.form
        existing_item.max_booking_days = _max_booking_days_from_form(
            request.form, fallback=existing_item.max_booking_days or DEFAULT_MAX_BOOKING_DAYS)
        db.session.commit()
        flash(f'Item {name} edited successfully!', 'success')
        next_url = request.form.get('next') or url_for('home')
        return redirect(next_url)


@app.route('/delete_item/<int:item_id>', methods=['POST'])
@admin_required
def delete_item(item_id):
    item = Item.query.get_or_404(item_id)
    require_faculty_admin(item.faculty)
    name = item.name
    db.session.delete(item)
    db.session.commit()
    flash(f'Item {name} deleted successfully!', 'success')
    next_url = request.form.get('next') or url_for('home')
    return redirect(next_url)


# ── Item export (CSV / XLSX / PDF) ────────────────────────────────────────
ITEM_EXPORT_HEADERS = ['ID', 'Name', 'Location', 'Bookable', 'Max Booking Days', 'Manual Link', 'Photo Path']


def _item_export_rows():
    """All items, ordered by name, as a list of row lists matching ITEM_EXPORT_HEADERS."""
    return [
        [
            item.id,
            (item.name or '').strip(),
            (item.location or '').strip(),
            'Yes' if item.is_bookable else 'No',
            item.max_booking_days or DEFAULT_MAX_BOOKING_DAYS,
            item.manual_link or '',
            item.photo_path or '',
        ]
        for item in Item.query.order_by(Item.name).all()
    ]


def _items_csv(headers, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    # utf-8-sig so Excel picks up the encoding when double-clicking the file
    return io.BytesIO(buffer.getvalue().encode('utf-8-sig')), 'text/csv', 'csv'


def _items_xlsx(headers, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Items'
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    for col_idx, header in enumerate(headers, start=1):
        longest = max([len(str(header))] + [len(str(row[col_idx - 1])) for row in rows] or [0])
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(longest + 2, 10), 60)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return (buffer,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'xlsx')


def _items_pdf(headers, rows):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle('cell', parent=styles['BodyText'], fontSize=8,
                                leading=10, alignment=TA_LEFT)
    head_style = ParagraphStyle('cellhead', parent=cell_style, fontName='Helvetica-Bold',
                                textColor=colors.white)

    data = [[Paragraph(str(h), head_style) for h in headers]]
    data += [[Paragraph(str(value), cell_style) for value in row] for row in rows]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title='MISC Booking — Items')

    table = Table(data, repeatRows=1,
                  colWidths=[12 * mm, 56 * mm, 28 * mm, 18 * mm, 20 * mm, 66 * mm, 56 * mm])
    table.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0), colors.HexColor('#343a40')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f2f2f2')]),
        ('GRID',         (0, 0), (-1, -1), 0.25, colors.HexColor('#b0b0b0')),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    story = [
        Paragraph('MISC Booking — Items', styles['Heading2']),
        Paragraph(f'{len(rows)} item(s) — generated {datetime.now().strftime("%Y-%m-%d %H:%M")}',
                  styles['Normal']),
        Spacer(1, 6 * mm),
        table,
    ]
    doc.build(story)
    buffer.seek(0)
    return buffer, 'application/pdf', 'pdf'


ITEM_EXPORT_BUILDERS = {
    'csv':  _items_csv,
    'xlsx': _items_xlsx,
    'pdf':  _items_pdf,
}


@app.route('/admin/export_items')
@admin_required
def export_items():
    """Download the full item list as CSV, XLSX or PDF."""
    fmt = (request.args.get('format') or 'csv').lower()
    if fmt in ('xls', 'excel'):
        fmt = 'xlsx'
    builder = ITEM_EXPORT_BUILDERS.get(fmt)
    if builder is None:
        flash(f'Unsupported export format: {fmt}', 'danger')
        return redirect(url_for('admin_dashboard', section='items'))

    rows = _item_export_rows()
    try:
        buffer, mimetype, extension = builder(ITEM_EXPORT_HEADERS, rows)
    except ImportError:
        missing = 'openpyxl' if fmt == 'xlsx' else 'reportlab'
        flash(f'{fmt.upper()} export needs the "{missing}" package — run '
              f'"pip install -r requirements.txt" on the server.', 'danger')
        return redirect(url_for('admin_dashboard', section='items'))

    filename = f'misc_items_{datetime.now().strftime("%Y%m%d")}.{extension}'
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=filename)


@app.route('/locations')
@admin_required
def locations():
    faculty = current_faculty()
    return render_template('locations.html',
                           locations=locations_for_faculty(faculty),
                           faculty=faculty)


@app.route('/add_location', methods=['POST'])
@admin_required
def add_location():
    name = request.form.get('name', '').strip()
    next_url = request.form.get('next') or url_for('locations')
    if name:
        faculty = current_faculty()
        fid = faculty.id if faculty else None
        # Names are unique per faculty, so the same room name may exist elsewhere.
        if not Location.query.filter_by(name=name, faculty_id=fid).first():
            db.session.add(Location(name=name, faculty_id=fid))
            db.session.commit()
            flash(f'Location "{name}" added.', 'success')
        else:
            flash(f'Location "{name}" already exists in this faculty.', 'warning')
    return redirect(next_url)


@app.route('/delete_location/<int:location_id>', methods=['POST'])
@admin_required
def delete_location(location_id):
    loc = Location.query.get_or_404(location_id)
    next_url = request.form.get('next') or url_for('locations')
    if Item.query.filter_by(location=loc.name).first():
        flash(f'Cannot delete "{loc.name}": items reference it.', 'danger')
    else:
        db.session.delete(loc)
        db.session.commit()
        flash(f'Location "{loc.name}" deleted.', 'success')
    return redirect(next_url)


@app.route('/edit_location/<int:location_id>', methods=['POST'])
@admin_required
def edit_location(location_id):
    loc = Location.query.get_or_404(location_id)
    name = request.form.get('name', '').strip()
    next_url = request.form.get('next') or url_for('locations')
    if not name:
        flash('Location name cannot be empty.', 'danger')
    elif Location.query.filter(Location.name == name, Location.id != location_id).first():
        flash(f'Location "{name}" already exists.', 'warning')
    else:
        loc.name = name
        db.session.commit()
        flash(f'Location renamed to "{name}".', 'success')
    return redirect(next_url)


@app.route('/bookings_admin')
@admin_required
def bookings_admin():
    bookings = Booking.query.order_by(Booking.id.desc()).all()
    return render_template('bookings_admin.html', bookings=bookings)


@app.route('/booking_detail_json/<int:booking_id>')
@admin_required
def booking_detail_json(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    # An admin may only act on bookings belonging to a faculty they administer.
    require_faculty_admin(booking.faculty)
    if booking.booking_items:
        items = [
            {
                "item_name":   bi.item_name,
                "borrow_date": bi.borrow_date.strftime('%Y-%m-%d'),
                "return_date": bi.return_date.strftime('%Y-%m-%d'),
                "item_id":     bi.item_id,
            }
            for bi in booking.booking_items
        ]
    else:
        items = [{
            "item_name":   booking.item_name,
            "borrow_date": booking.borrow_date.strftime('%Y-%m-%d'),
            "return_date": booking.return_date.strftime('%Y-%m-%d'),
            "item_id":     booking.item_id,
        }]
    return jsonify({
        "id":             booking.id,
        "borrower_name":  booking.borrower_name,
        "borrower_email": booking.borrower_email,
        "borrower_phone": booking.borrower_phone,
        "note":           booking.note or '',
        "denial_reason":  booking.denial_reason or '',
        "status":         booking.status,
        "items":          items,
        "approve_url":    url_for('approve_booking', booking_id=booking.id),
        "lend_url":       url_for('lend_item',   booking_id=booking.id),
        "return_url":     url_for('return_item', booking_id=booking.id),
    })


@app.route('/test-job')
@admin_required
def test_job():
    """
    Visit this route to execute the daily check
    for items due to return.
    """
    with app.app_context():
        check_and_send_reminders_tomorrow()
    return "Job executed", 200

# Daily check for items due to return
def check_and_send_reminders_tomorrow():
    with app.app_context():

        logger.info(f'Sending reminders started.')

        today       = datetime.now().date()
        tomorrow    = today + timedelta(days=1)

        # Query for bookings that are due today or tomorrow
        due_bookings = Booking.query.filter(
            (Booking.return_date == today) | (Booking.return_date == tomorrow),
            Booking.status.in_(ACTIVE_STATUSES),
        ).all()
        
        # Group bookings and items by borrower
        borrower_data = defaultdict(lambda: {'bookings': [], 'items': []})
        for booking in due_bookings:
            key = (booking.borrower_email, booking.borrower_name, booking.borrower_phone)
            borrower_data[key]['bookings'].append(booking)
            borrower_data[key]['items'].append(booking.item)

        # Send an email per borrower with all their bookings and items
        for (borrower_email, borrower_name, borrower_phone), data in borrower_data.items():
            # Send email and flash success message
            response = send_email(  borrower_email= borrower_email,
                                    borrower_name = borrower_name,
                                    borrower_phone= borrower_phone,
                                    borrow_date   = None,
                                    return_date   = tomorrow,
                                    subject       = "Booking - Reminder, return item(s)",
                                    text_content  = "",
                                    html_content  = "",
                                    items         = data['items'],
                                    type_of_mail  = 'return_reminder')

            # Log the execution of the function
            logger.info('check_and_send_reminders_tomorrow executed. Email sent to: ' + borrower_email)

        # Log additional details if needed, such as user info, email contents, etc.
        logger.info(f'Sending reminders finished.')


def send_email(borrower_email, borrower_name, borrower_phone, borrow_date, return_date, subject, text_content, html_content, items, type_of_mail=None, recipients=None, faculty=None, **kargs):
    mail_body = {}
    
    logger.info(f"Attempting to email {borrower_email} for {type_of_mail}")

    # Loading a different html template depending on what the email is about:
    if type_of_mail == 'return_reminder':
        plain_text_content = f"Hello, \n\nThis is a reminder to return the booked items by {return_date.strftime('%Y-%m-%d')}."
        html_content = render_template('email_return_item.html', 
                                    borrower_name   = borrower_name, 
                                    borrower_email  = borrower_email,
                                    borrower_phone  = borrower_phone,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date,
                                    now             = datetime.now(),
                                    items           = items)
    elif type_of_mail == 'booking':
        plain_text_content = "Your booking has been registered. Unless you receive a cancellation, please come to the MISC to take the item(s)"
        html_content = render_template('email_booking.html', 
                                    borrower_name   = borrower_name, 
                                    borrower_email  = borrower_email,
                                    user_email      = kargs['user_email'],
                                    borrower_phone  = borrower_phone,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date,
                                    now             = datetime.now(),
                                    items           = items)

    elif type_of_mail == 'booking_admin':
        plain_text_content = "A new booking request has been submitted."
        html_content = render_template('email_booking_admin.html',
                                    borrower_name   = borrower_name,
                                    borrower_email  = borrower_email,
                                    borrower_phone  = borrower_phone,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date,
                                    now             = datetime.now(),
                                    items           = items,
                                    # The template's primary button is Approve (v3.3).
                                    # Passing lend_url here left {{ approve_url }} undefined,
                                    # so the button rendered with an empty href.
                                    approve_url     = kargs.get('approve_url', ''),
                                    deny_url        = kargs.get('deny_url', ''),
                                    note            = kargs.get('note', ''))

    elif type_of_mail == 'deny':
        plain_text_content = "Your booking has been denied."
        html_content = render_template('email_deny.html',
                                    borrower_name   = borrower_name,
                                    borrower_email  = borrower_email,
                                    borrower_phone  = borrower_phone,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date,
                                    now             = datetime.now(),
                                    bookings        = items,
                                    note            = kargs['note'])

    elif type_of_mail == 'approved':
        plain_text_content = "Your booking has been approved. The item(s) are reserved for you — please collect them from MISC on the borrow date."
        html_content = render_template('email_approved.html',
                                    borrower_name   = borrower_name,
                                    borrower_email  = borrower_email,
                                    borrower_phone  = borrower_phone,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date,
                                    now             = datetime.now(),
                                    items           = items)

    elif type_of_mail == 'lent':
        plain_text_content = "Your item(s) have been handed over. Please return them by the agreed date."
        html_content = render_template('email_lent.html',
                                    borrower_name   = borrower_name,
                                    borrower_email  = borrower_email,
                                    borrower_phone  = borrower_phone,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date,
                                    now             = datetime.now(),
                                    items           = items)

    elif type_of_mail == 'returned':
        plain_text_content = "Your item(s) have been logged as returned. Thank you."
        html_content = render_template('email_returned.html',
                                    borrower_name   = borrower_name,
                                    borrower_email  = borrower_email,
                                    borrower_phone  = borrower_phone,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date,
                                    now             = datetime.now(),
                                    items           = items)

    mail_from = "booking@ideas-block.com"
    name_from = "MISC booking - DO NOT Reply"

    # Admins subscribed to notifications for the faculty the activity came from
    admin_contacts = admin_notification_contacts(faculty)

    if recipients is not None:
        # Caller supplied explicit recipient list — use it directly
        bcc = recipients
    else:
        # Build BCC list while avoiding duplicates
        borrower_email_lower = borrower_email.lower()
        admin_emails_set = {admin["email"].lower() for admin in admin_contacts}
        bcc = list(admin_contacts)

        # Add borrower if not already an admin
        if borrower_email_lower not in admin_emails_set:
            bcc.append({"name": borrower_name, "email": borrower_email_lower})

        # TODO: If return reminder, send only to borrower. Keep this logic?
        if type_of_mail == 'booking' or type_of_mail == 'deny':
            # mailer.set_bcc_recipients(bcc, mail_body)
            pass
        else:
            bcc = []
            bcc.append({ "name": borrower_name, "email": borrower_email.lower()})

    request = (IdentityBuilder()
          .identity_id("MISC")
          .name(name_from)
          .reply_to_email("misc@lmta.lt")
          .reply_to_name(name_from)
          .add_note(False)
          .build_update_request())

    # Send the email
    # TODO: still fix thy it cannot add bcc, it crashes. bug reported. 
    email = (EmailBuilder()
        .from_email(mail_from, name_from)
        .to_many(bcc)
        # .bcc(bcc)
        .subject(subject)
        .html(html_content)
        .text(plain_text_content)
        .build())

    response = mailer.emails.send(email)
    return response


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    section = request.args.get('section', 'bookings')

    # The dashboard always shows exactly one faculty.
    #   ?faculty=<code> asked for explicitly  -> refuse if it is not theirs,
    #                                            rather than quietly showing
    #                                            different data than the URL says
    #   nothing asked for                     -> use the remembered faculty, or
    #                                            fall back to their first, since a
    #                                            stale session is not a request
    my_faculties = admin_faculties_for(current_user)
    requested_code = request.args.get('faculty')
    if requested_code:
        faculty = Faculty.query.filter_by(code=requested_code).first()
        if faculty is None:
            abort(404)
        require_faculty_admin(faculty)
        session['faculty_code'] = faculty.code
    else:
        faculty = current_faculty()
        if faculty is None or not current_user.is_admin_of(faculty):
            faculty = my_faculties[0] if my_faculties else None
            if faculty is None:
                abort(403)
            session['faculty_code'] = faculty.code
    require_faculty_admin(faculty)

    data = {'section': section, 'faculty': faculty, 'faculties': my_faculties,
            'all_faculty_list': all_faculties()}
    fac_booking = faculty_filter(Booking.faculty_id, faculty)

    if section == 'bookings':
        data['bookings'] = Booking.query.filter(fac_booking).order_by(Booking.id.desc()).all()
    elif section == 'users':
        # Users are global, but only a super admin may change roles.
        data['users'] = User.query.order_by(User.username).all()
    elif section == 'locations':
        data['locations'] = locations_for_faculty(data['faculty'])
    elif section == 'programmes':
        data['programmes'] = Programme.query.order_by(Programme.name).all()
    elif section == 'items':
        fac = data['faculty']
        data['items'] = Item.query.filter(
            faculty_filter(Item.faculty_id, fac)).order_by(Item.name).all()
        data['locations'] = locations_for_faculty(fac)
    elif section == 'history':
        # Full booking log: every booking ever made, including terminal ones.
        # Optional ?status= filter; 'all' (or absent) shows everything.
        status_filter = request.args.get('status', 'all')
        query = Booking.query.filter(fac_booking)
        if status_filter in ALL_STATUSES:
            query = query.filter(Booking.status == status_filter)
            data['status_filter'] = status_filter
        else:
            data['status_filter'] = 'all'
        data['history']       = query.order_by(Booking.id.desc()).all()
        data['all_statuses']  = ALL_STATUSES
        data['status_counts'] = dict(
            db.session.query(Booking.status, db.func.count(Booking.id))
                      .filter(fac_booking).group_by(Booking.status).all()
        )
        data['total_bookings'] = Booking.query.filter(fac_booking).count()
    return render_template('admin_dashboard.html', **data)


@app.route('/admin/add_user', methods=['POST'])
@admin_required
def admin_add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    is_admin = bool(request.form.get('is_admin'))
    if not username or not password:
        flash('Username and password are required.', 'danger')
    elif User.query.filter_by(username=username).first():
        flash(f'User "{username}" already exists.', 'warning')
    else:
        user = User(
            username   = username,
            email      = username,
            password   = generate_password_hash(password, method='pbkdf2:sha256'),
            is_admin   = is_admin,
        )
        db.session.add(user)
        db.session.commit()
        flash(f'User "{username}" added.', 'success')
    return redirect(url_for('admin_dashboard', section='users'))


@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f'User "{user.username}" deleted.', 'success')
    return redirect(url_for('admin_dashboard', section='users'))


@app.route('/admin/toggle_faculty_admin/<int:user_id>/<int:faculty_id>', methods=['POST'])
@super_admin_required
def admin_toggle_faculty_admin(user_id, faculty_id):
    """Grant or revoke admin rights over one faculty. Super admins only."""
    user = User.query.get_or_404(user_id)
    faculty = Faculty.query.get_or_404(faculty_id)

    role = user.admin_role_for(faculty)
    if role:
        db.session.delete(role)
        flash(f'"{user.username}" is no longer an admin of {faculty.short_name}.', 'success')
    else:
        db.session.add(FacultyAdmin(user_id=user.id, faculty_id=faculty.id,
                                    receives_admin_emails=True))
        flash(f'"{user.username}" is now an admin of {faculty.short_name}.', 'success')

    refresh_admin_flag(user)
    db.session.commit()
    return redirect(request.referrer or url_for('admin_dashboard', section='users'))


@app.route('/admin/toggle_super_admin/<int:user_id>', methods=['POST'])
@super_admin_required
def admin_toggle_super_admin(user_id):
    """Promote or demote a super admin. Super admins only."""
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot change your own super admin status.', 'danger')
        return redirect(request.referrer or url_for('admin_dashboard', section='users'))

    if user.is_super_admin and User.query.filter_by(is_super_admin=True).count() <= 1:
        # Never leave the system without anyone who can grant rights.
        flash('This is the last super admin — promote someone else first.', 'danger')
        return redirect(request.referrer or url_for('admin_dashboard', section='users'))

    user.is_super_admin = not user.is_super_admin
    refresh_admin_flag(user)
    db.session.commit()
    flash(f'"{user.username}" {"is now a super admin" if user.is_super_admin else "is no longer a super admin"}.',
          'success')
    return redirect(request.referrer or url_for('admin_dashboard', section='users'))


@app.route('/admin/toggle_admin/<int:user_id>', methods=['POST'])
@admin_required
def admin_toggle_admin(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot change your own admin status.', 'danger')
    else:
        user.is_admin = not user.is_admin
        db.session.commit()
        flash(f'Admin status for "{user.username}" {"enabled" if user.is_admin else "disabled"}.', 'success')
    return redirect(url_for('admin_dashboard', section='users'))


@app.route('/admin/toggle_admin_emails/<int:user_id>', methods=['POST'])
@admin_required
def admin_toggle_admin_emails(user_id):
    """Subscribe/unsubscribe an admin from booking notification emails."""
    user = User.query.get_or_404(user_id)
    if not user.is_admin:
        flash(f'"{user.username}" is not an admin, so they receive no admin emails.', 'warning')
        return redirect(url_for('admin_dashboard', section='users'))

    faculty = current_faculty()
    role = user.admin_role_for(faculty)
    if role is not None:
        # Faculty admins subscribe per grant, so one faculty can be muted
        # without affecting the other.
        role.receives_admin_emails = not role.receives_admin_emails
        user.receives_admin_emails = role.receives_admin_emails
    else:
        # Super admins (and legacy rows) use the flag on the user.
        user.receives_admin_emails = not user.receives_admin_emails
    db.session.commit()

    if user.receives_admin_emails and not user.notification_email:
        flash(f'"{user.username}" is subscribed but has no valid email address, '
              f'so nothing can be delivered. Add one on their profile.', 'warning')
    else:
        flash(f'"{user.username}" {"will now receive" if user.receives_admin_emails else "will no longer receive"} '
              f'admin notification emails.', 'success')
    return redirect(url_for('admin_dashboard', section='users'))


@app.route('/admin/add_programme', methods=['POST'])
@admin_required
def admin_add_programme():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Programme name cannot be empty.', 'danger')
    elif Programme.query.filter_by(name=name).first():
        flash(f'Programme "{name}" already exists.', 'warning')
    else:
        db.session.add(Programme(name=name))
        db.session.commit()
        flash(f'Programme "{name}" added.', 'success')
    return redirect(url_for('admin_dashboard', section='programmes'))


@app.route('/admin/delete_programme/<int:programme_id>', methods=['POST'])
@admin_required
def admin_delete_programme(programme_id):
    prog = Programme.query.get_or_404(programme_id)
    db.session.delete(prog)
    db.session.commit()
    flash(f'Programme "{prog.name}" deleted.', 'success')
    return redirect(url_for('admin_dashboard', section='programmes'))


@app.route('/admin/edit_programme/<int:programme_id>', methods=['POST'])
@admin_required
def admin_edit_programme(programme_id):
    prog = Programme.query.get_or_404(programme_id)
    name = request.form.get('name', '').strip()
    if not name:
        flash('Programme name cannot be empty.', 'danger')
    elif Programme.query.filter(Programme.name == name, Programme.id != programme_id).first():
        flash(f'Programme "{name}" already exists.', 'warning')
    else:
        prog.name = name
        db.session.commit()
        flash(f'Programme renamed to "{name}".', 'success')
    return redirect(url_for('admin_dashboard', section='programmes'))


@app.route('/deny_booking/<int:booking_id>')
@admin_required
def deny_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    # An admin may only act on bookings belonging to a faculty they administer.
    require_faculty_admin(booking.faculty)
    return render_template('deny_booking.html', booking=booking)


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.first_name = request.form.get('first_name', '').strip() or None
        current_user.last_name  = request.form.get('last_name',  '').strip() or None
        current_user.phone      = request.form.get('phone',      '').strip() or None
        current_user.course     = request.form.get('course',     '').strip() or None
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile'))
    programmes = Programme.query.order_by(Programme.name).all()
    bookings   = Booking.query.filter_by(user_email=current_user.email).order_by(Booking.id.desc()).all()
    return render_template('profile.html', programmes=programmes, bookings=bookings)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, title='Forbidden',
                           message='You do not have permission to access this page.'), 403

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, title='Page Not Found',
                           message='The page you are looking for does not exist.'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, title='Server Error',
                           message='Something went wrong on our end. Please try again later.'), 500

# @app.route('/session-dump')
# def session_dump():
#     return jsonify(dict(session))

if not LOCALHOST:
    scheduler.add_job(func=check_and_send_reminders_tomorrow, trigger="cron", hour=22, minute=22)
    scheduler.start()


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description = 'MISC booking system')
    parser.add_argument('-d', '--dev', help="development mode enabled", default=False, action="store_true")      # development mode
    args = parser.parse_args()

    # -d forces development mode on. Without it, keep whatever MISC_DEV already
    # decided at import time — overwriting LOCALHOST here would contradict the
    # module-level setup (scheduler, APPLICATION_ROOT) that ran against that value.
    if args.dev:
        LOCALHOST = True

    create_default_faculties()
    backfill_faculty_assignments()
    migrate_admin_roles_to_faculties()
    create_admin_user()
    create_default_locations()
    create_default_programmes()
    seed_admin_notification_contacts()

    if not LOCALHOST:

        try:
            # Run the Flask app (this is a blocking call)
            app.run(debug=True, host='0.0.0.0', use_reloader=True)
        except (KeyboardInterrupt, SystemExit):
            # Shut down the scheduler when exiting the app. It only exists when the
            # module-level `if not LOCALHOST` block ran, and a failed app.run() lands
            # here too — so guard it, or the real error gets masked by a NameError.
            if 'scheduler' in globals():
                scheduler.shutdown()

    else:
        # Port 5006: macOS AirPlay Receiver (ControlCenter) occupies the Flask default 5000
        app.run(debug=True, host='0.0.0.0', port=5006, use_reloader=True)
    
