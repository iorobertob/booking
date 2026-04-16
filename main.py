import argparse
import re
from functools import wraps
from flask import session, Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, abort
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
import os
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging
from logging.handlers import RotatingFileHandler
from collections import defaultdict
from types import SimpleNamespace
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
    status          = db.Column(db.String(20),  default='booked')
    note            = db.Column(db.String(300), default='', nullable=True)
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
        }

# Define the Item model
class Item(db.Model):
    id              = db.Column(db.Integer,     primary_key=True)
    name            = db.Column(db.String(100), nullable=False)
    location        = db.Column(db.String(100), nullable=False)
    manual_link     = db.Column(db.String(200), default='')
    photo_path      = db.Column(db.String(200), default='')
    is_bookable     = db.Column(db.Boolean,     default=True, nullable=False)
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
    name = db.Column(db.String(100), nullable=False, unique=True)


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
    """Pre-populate Location table with default locations A, B, C."""
    with app.app_context():
        for name in ['A', 'B', 'C']:
            if not Location.query.filter_by(name=name).first():
                db.session.add(Location(name=name))
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

    items = Item.query.all()
    availability = check_all_items_availability()

    bookings = []
    for item in items:
        # New-style: lent booking via BookingItem
        booking = db.session.query(Booking).join(BookingItem).filter(
            BookingItem.item_id == item.id, Booking.status == 'lent'
        ).first()
        # Fallback old-style
        if not booking:
            booking = Booking.query.filter_by(item_id=item.id, status='lent').first()
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

    return render_template('home.html', items=items, availability=availability, bookings = bookings, borrower_info=borrower_info)

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

    # Old-style (no BookingItem children)
    old_bookings = Booking.query.filter(
        Booking.item_id == item_id,
        ~Booking.booking_items.any(),
    ).all()
    # New-style: Booking objects whose BookingItems reference this item
    new_bookings = db.session.query(Booking).join(BookingItem).filter(
        BookingItem.item_id == item_id
    ).all()
    bookings = old_bookings + new_bookings

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


def check_all_items_availability():
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
            bi = db.session.query(BookingItem).filter(
                BookingItem.item_id == item.id, now_overlap
            ).first()
            if bi:
                status = bi.booking.status
            else:
                old = Booking.query.filter(
                    Booking.item_id == item.id,
                    ~Booking.booking_items.any(),
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
    # New-style: check BookingItem table
    if db.session.query(BookingItem).filter(BookingItem.item_id == item_id, overlap).first():
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
        old_overlap,
    ).first() is None


def get_bookings_list(item_id):
    """Fetch all booking date ranges for this item (old-style and new-style)."""
    bookings_list = []
    # Old-style: Booking directly on item, no BookingItem children
    for b in Booking.query.filter(Booking.item_id == item_id, ~Booking.booking_items.any()).all():
        bookings_list.append({"borrow_date": b.borrow_date, "return_date": b.return_date, "borrower_name": b.borrower_name})
    # New-style: via BookingItem
    for bi in BookingItem.query.filter_by(item_id=item_id).all():
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

    # 2. Send admin notification with Lend / Deny action links
    DEV_ADMIN_CONTACTS  = [{"name": "Roberto", "email": "roberto.becerra@lmta.lt"}]
    ALL_ADMIN_CONTACTS  = [
        {"name": "Edvinas",       "email": "edvinas.siliunas@lmta.lt"},
        {"name": "Edvinas Gmail", "email": "siliunas.edvinas@gmail.com"},
        {"name": "Roberto",       "email": "roberto.becerra@lmta.lt"},
        {"name": "Julius",        "email": "julius.aglinskas@lmta.lt"},
        {"name": "Mantautas",     "email": "mantautas.krukauskas@lmta.lt"},
    ]
    admin_contacts = ALL_ADMIN_CONTACTS if not LOCALHOST else DEV_ADMIN_CONTACTS
    lend_url = url_for('lend_item',   booking_id=new_booking.id, _external=True)
    deny_url = url_for('deny_booking', booking_id=new_booking.id, _external=True)
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
        lend_url       = lend_url,
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

    bookings = Booking.query.order_by(Booking.borrow_date.desc()).all()
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

        # Append to our cart_items list
        cart_items.append({
            "borrower_name"     : borrower_name,
            "borrower_email"    : borrower_email,
            "borrower_phone"    : borrower_phone,
            "note"              : booking_note,
            "id"                : item['id'],
            "name"              : item['name'],
            "location"          : item['location'],
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


@app.route('/lend/<int:booking_id>')
@admin_required
def lend_item(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    booking.status = 'lent'
    db.session.commit()

    items_for_email = booking.booking_items if booking.booking_items else [booking]
    send_email(
        borrower_email = booking.borrower_email,
        borrower_name  = booking.borrower_name,
        borrower_phone = booking.borrower_phone,
        borrow_date    = booking.borrow_date.date(),
        return_date    = booking.return_date.date(),
        subject        = "Booking approved and collected — Do Not Reply",
        text_content   = "",
        html_content   = "",
        items          = items_for_email,
        type_of_mail   = 'lent',
    )

    flash(f'Item {booking.item_name} marked as lent!', 'success')
    return redirect(request.referrer or url_for('home'))


@app.route('/return/<int:booking_id>', methods=['POST', 'GET'])
@admin_required
def return_item(booking_id):
    booking = Booking.query.get_or_404(booking_id)

    # Capture all needed data before the booking is deleted from the DB
    saved_email     = booking.borrower_email
    saved_name      = booking.borrower_name
    saved_phone     = booking.borrower_phone
    saved_borrow    = booking.borrow_date.date()
    saved_return    = booking.return_date.date()
    saved_item_name = booking.item_name

    # Capture email items before cascade-delete removes BookingItems
    if booking.booking_items:
        items_for_email = [
            SimpleNamespace(
                item_name   = bi.item_name,
                borrow_date = bi.borrow_date,
                return_date = bi.return_date,
            )
            for bi in booking.booking_items
        ]
    else:
        items_for_email = [booking]  # old-style; SQLAlchemy keeps in-memory attrs

    actionType = request.form.get("formAction")
    note       = request.form.get('note')

    db.session.delete(booking)
    db.session.commit()

    if actionType in ('deny', 'deny_no_note'):
        flash(f'Booking for {saved_item_name} denied.', 'success')
    else:
        flash(f'Item {saved_item_name} marked as returned!', 'success')

    if actionType == 'deny':
        send_email(
            borrower_email = saved_email,
            borrower_name  = saved_name,
            borrower_phone = saved_phone,
            borrow_date    = saved_borrow,
            return_date    = saved_return,
            subject        = "Booking denied — Do Not Reply",
            text_content   = "",
            html_content   = "",
            items          = items_for_email,
            type_of_mail   = 'deny',
            note           = note,
        )
    elif actionType == 'deny_no_note':
        pass  # booking deleted silently; no email sent
    else:
        send_email(
            borrower_email = saved_email,
            borrower_name  = saved_name,
            borrower_phone = saved_phone,
            borrow_date    = saved_borrow,
            return_date    = saved_return,
            subject        = "Item returned — Thank you — Do Not Reply",
            text_content   = "",
            html_content   = "",
            items          = items_for_email,
            type_of_mail   = 'returned',
        )

    if actionType in ('deny', 'deny_no_note'):
        return redirect(url_for('admin_dashboard', section='bookings'))
    return redirect(request.referrer or url_for('home'))


@app.route('/add_item', methods=['GET', 'POST'])
@admin_required
def add_item():
    if request.method == 'POST':
        name        = request.form.get('name')
        location    = request.form.get('location')
        is_bookable = 'is_bookable' in request.form
        new_item = Item(name=name, location=location, is_bookable=is_bookable)
        db.session.add(new_item)
        db.session.commit()
        flash(f'Item {name} added successfully!', 'success')
        next_url = request.form.get('next') or url_for('home')
        return redirect(next_url)

    locations = Location.query.order_by(Location.name).all()
    return render_template('add_item.html', locations=locations)


@app.route('/edit_item/<int:item_id>', methods=['GET', 'POST'])
@admin_required
def edit_item(item_id):
    if request.method == 'GET':
        item = Item.query.get_or_404(item_id)
        locations = Location.query.order_by(Location.name).all()
        return render_template('edit_item.html', item=item, locations=locations)

    if request.method == 'POST':
        name        = request.form.get('name')
        location    = request.form.get('location')
        existing_item = Item.query.get(item_id)
        existing_item.name        = name
        existing_item.location    = location
        existing_item.is_bookable = 'is_bookable' in request.form
        db.session.commit()
        flash(f'Item {name} edited successfully!', 'success')
        next_url = request.form.get('next') or url_for('home')
        return redirect(next_url)


@app.route('/delete_item/<int:item_id>', methods=['POST'])
@admin_required
def delete_item(item_id):
    item = Item.query.get_or_404(item_id)
    name = item.name
    db.session.delete(item)
    db.session.commit()
    flash(f'Item {name} deleted successfully!', 'success')
    next_url = request.form.get('next') or url_for('home')
    return redirect(next_url)


@app.route('/locations')
@admin_required
def locations():
    all_locations = Location.query.order_by(Location.name).all()
    return render_template('locations.html', locations=all_locations)


@app.route('/add_location', methods=['POST'])
@admin_required
def add_location():
    name = request.form.get('name', '').strip()
    next_url = request.form.get('next') or url_for('locations')
    if name:
        if not Location.query.filter_by(name=name).first():
            db.session.add(Location(name=name))
            db.session.commit()
            flash(f'Location "{name}" added.', 'success')
        else:
            flash(f'Location "{name}" already exists.', 'warning')
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
        "status":         booking.status,
        "items":          items,
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
        due_bookings = Booking.query.filter((Booking.return_date == today) | (Booking.return_date == tomorrow)).all()
        
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


def send_email(borrower_email, borrower_name, borrower_phone, borrow_date, return_date, subject, text_content, html_content, items, type_of_mail=None, recipients=None, **kargs):
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
                                    lend_url        = kargs.get('lend_url', ''),
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

    elif type_of_mail == 'lent':
        plain_text_content = "Your item(s) have been approved and handed over. Please return them by the agreed date."
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

    # Define all admin contacts 
    DEV_ADMIN_CONTACTS = [
        {"name": "Roberto",   "email": "roberto.becerra@lmta.lt"}
    ]
    ALL_ADMIN_CONTACTS = [
        {"name": "Edvinas",         "email": "edvinas.siliunas@lmta.lt"},
        {"name": "Edvinas Gmail",   "email": "siliunas.edvinas@gmail.com"},
        {"name": "Roberto",         "email": "roberto.becerra@lmta.lt"},
        {"name": "Julius",          "email": "julius.aglinskas@lmta.lt"},
        {"name": "Mantautas",       "email": "mantautas.krukauskas@lmta.lt"},
    ]

    # Filter admins based on environment
    admin_contacts = ALL_ADMIN_CONTACTS if not LOCALHOST else DEV_ADMIN_CONTACTS

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
    data = {'section': section}
    if section == 'bookings':
        data['bookings'] = Booking.query.order_by(Booking.id.desc()).all()
    elif section == 'users':
        data['users'] = User.query.order_by(User.username).all()
    elif section == 'locations':
        data['locations'] = Location.query.order_by(Location.name).all()
    elif section == 'programmes':
        data['programmes'] = Programme.query.order_by(Programme.name).all()
    elif section == 'items':
        data['items'] = Item.query.order_by(Item.name).all()
        data['locations'] = Location.query.order_by(Location.name).all()
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

    if args.dev:
        LOCALHOST = True  # Only affects app.run() below; for gunicorn use MISC_DEV=true env var
    else:
        LOCALHOST = False

    create_admin_user()
    create_default_locations()
    create_default_programmes()

    if not LOCALHOST:

        try:
            # Run the Flask app (this is a blocking call)
            app.run(debug=True, host='0.0.0.0', use_reloader=True)
        except (KeyboardInterrupt, SystemExit):
            # Shut down the scheduler when exiting the app
            scheduler.shutdown() 

    else:
        # Using 127.0.0.1 instead of 0.0.0.0 to avoid port overlap with airPlay
        app.run(debug=True, host='0.0.0.0', port=5006, use_reloader=True)
    
