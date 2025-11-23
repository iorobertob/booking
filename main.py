import argparse
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
import jsonpickle
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import logging
from logging.handlers import RotatingFileHandler
from collections import defaultdict
from msal import ConfidentialClientApplication
import time

# TODO: Fix this order of things so LOCALHOST can be set from __main__
LOCALHOST = False

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

app = Flask(__name__, static_folder='images')

# for prefix forwarding
app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

if LOCALHOST:
    vars_path = "vars/vars.json"
else:
    scheduler = BackgroundScheduler(daemon=True)

    # Shut down the scheduler when exiting the app
    atexit.register(lambda: scheduler.shutdown())

    app.config['APPLICATION_ROOT'] = '/booking'
    vars_path = "vars/vars.json"

with open(vars_path, "r") as file:
    vars_json = json.load(file)
    username  = str(vars_json.get("db_username"))
    password  = str(vars_json.get("db_password"))
    database  = str(vars_json.get("database"))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql://'+username+':'+password+'@localhost/'+database

    app.config['SECRET_KEY'] = vars_json.get("secret_key")

    # Initialize MailerSend
    mailer = MailerSendClient(api_key=str(vars_json.get("mailersend_api_key")))

    # Admin emails
    ADMIN_EMAILS = set(email.lower() for email in vars_json.get("admin_emails", []))

    # Microsoft Login
    CLIENT_ID     = vars_json.get("AZURE_CLIENT_ID")
    CLIENT_SECRET = vars_json.get("AZURE_CLIENT_SECRET")
    TENANT_ID     = vars_json.get("AZURE_TENANT_ID")
    AUTHORITY     = f"https://login.microsoftonline.com/{TENANT_ID}"
    REDIRECT_PATH = "/getAToken"
    SCOPE         = ["User.Read"]
    
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
        }

# Define the Item model
class Item(db.Model):
    id              = db.Column(db.Integer,     primary_key=True)
    name            = db.Column(db.String(100), nullable=False)
    location        = db.Column(db.String(100), nullable=False)
    manual_link     = db.Column(db.String(200), default='')
    photo_path      = db.Column(db.String(200), default='')
    bookings        = db.relationship('Booking', order_by=Booking.id, back_populates='item')

# Define the User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username    = db.Column(db.String(100), unique=True, nullable=False)
    password    = db.Column(db.String(128), nullable=False)
    email       = db.Column(db.String(100), nullable=True)
    is_admin    = db.Column(db.Boolean, default=False)
    first_name  = db.Column(db.String(50), nullable=True)  # start as nullable
    last_name   = db.Column(db.String(50), nullable=True)   # start as nullable


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'assets/images'),
                          'favicon.ico',mimetype='image/vnd.microsoft.icon')


@app.route('/assets/<path:filename>')
def custom_static(filename):
    return send_from_directory('assets', filename)


@app.route('/assets/<path:filename>', methods=['GET', 'POST'])
def custom_images(filename):
    return send_from_directory('assets/images', filename)


def create_admin_user():
    """Create an admin user"""

    with app.app_context():

        db.create_all()

        admin_exists = User.query.filter_by(username='admin').first() is not None
        if not admin_exists:
            admin_user = User(username='admin', password=generate_password_hash(str(vars_json.get("misc_password")), method='pbkdf2'), is_admin=True)
            db.session.add(admin_user)
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
        item_bookings = Booking.query.filter_by(item_id = item.id, status = "lent").first()
        bookings.append(item_bookings)

    if 'borrower_info' not in session:
        borrower_info = False
    else:
        if session.get('borrower_info') == {}:
            borrower_info = False
        else:
            borrower_info = json.dumps(session.get('borrower_info'))

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
    session["flow"] = _build_auth_code_flow(scopes=SCOPE)
    return redirect(session["flow"]["auth_uri"])


@app.route("/getAToken")
def authorized():
    result = _build_msal_app().acquire_token_by_auth_code_flow(
        session.get("flow", {}), request.args)
    
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
            "borrower_phone"    : None
        })
        session['borrower_info'] = borrower_info
        session.modified = True

        session['user_email'] = email

        # Try to find existing user by email
        user = User.query.filter_by(username=email).first()

        if not user:
            # Create a new user
            # TODO: is the password in here created the same not a security hole?
            user = User(
                username    = email,
                email       = email,
                first_name  = first_name,
                last_name   = last_name,
                password    = generate_password_hash("office_placeholder_password", method='pbkdf2:sha256'),
                is_admin    = email.lower() in ADMIN_EMAILS  # optional: set based on your policy
            )
            db.session.add(user)
            db.session.commit()

        # Log the user in
        login_user(user)

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

    # Remove any custom session keys, but keep Flask-Login's session structure intact
    session.pop("microsoft_user", None)
    session.pop("flow", None)
    session.pop("borrower_info", None)
    session.pop("user_email", None)
    # Don't use session.clear()

    session.modified = True  #  force session to update in some Flask versions
    return redirect(url_for('home'))



@app.route('/set_borrower', methods=['POST'])
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
    
    bookings = Booking.query.filter_by(item_id=item_id).all()

    booking_dates = get_bookings_list(item_id=item_id)
    booked_dates  = []

    for booking in booking_dates:
        booked_dates.extend(get_all_dates_between(booking["borrow_date"], booking["return_date"]))
    # Convert dates to string format
    booked_dates_str = [date.strftime('%Y-%m-%d') for date in booked_dates]

    item_for_cart = row2dict(item)
    booked_dates  = json.dumps(booked_dates_str)

    if 'borrower_info' not in session:
        borrower_info = False
    else:
        if session.get('borrower_info') == {}:
            borrower_info = False
        else:
            borrower_info = json.dumps(session.get('borrower_info'))

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
        if is_item_available(item.id,now, now):
            availability.append("Available")
        else:
            # Get the overlapping bookings to extract status
            overlapping_bookings = Booking.query.filter(
                Booking.item_id == item.id,
                db.or_(
                    db.and_(Booking.borrow_date <= now, Booking.return_date >= now),
                    db.and_(Booking.borrow_date >= now, Booking.return_date <= now)
                )
            ).all()

            # Use first overlapping booking's status as the reason
            if overlapping_bookings:
                current_status = overlapping_bookings[0].status
                availability.append(current_status.capitalize())  # e.g., "Booked" or "Lent"
            else:
                availability.append("Lent/booked")
    return availability


def is_item_available(item_id, start_date, end_date):
    bookings = Booking.query.filter(
        Booking.item_id == item_id,
        db.or_(
            db.and_(Booking.borrow_date <= start_date, Booking.return_date >= start_date),
            db.and_(Booking.borrow_date <= end_date, Booking.return_date >= end_date),
            db.and_(Booking.borrow_date >= start_date, Booking.return_date <= end_date)
        )
    ).all()
    return len(bookings) == 0


def get_bookings_list(item_id):
    """ Fetch all bookings from the database for this item"""
    
    bookings_query = Booking.query.filter_by(item_id=item_id).all()
    
    # Initialize an empty list to hold booking dictionaries
    bookings_list = []
    
    # Iterate over the fetched bookings and add them to the list as dictionaries
    for booking in bookings_query:
        booking_dict = {
            "borrow_date": booking.borrow_date,
            "return_date": booking.return_date,
            "borrower_name":booking.borrower_name
        }
        bookings_list.append(booking_dict)
    
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
    Main book function, for single and bulk booking
    """

    if session.get("microsoft_user") and is_microsoft_token_expired():
        logout_user()

        # Remove any custom session keys, but keep Flask-Login's session structure intact
        session.pop("microsoft_user", None)
        session.pop("flow", None)
        session.pop("borrower_info", None)
        session.pop("user_email", None)
        # Don't use session.clear()

        session.modified = True  #  force session to update in some Flask versions

        flash("Your session has expired. Please log in again.", "warning")
        return redirect(url_for('login'))

    borrower_info   = session.get('borrower_info')[0]
    borrower_name   = borrower_info["borrower_name"]
    borrower_email  = borrower_info["borrower_email"]
    borrower_phone  = borrower_info["borrower_phone"]

    items           = request.form.get('itemsJSON')
    booked_dates    = request.form.get('booked_dates')

    # Filter the type, force list 
    if isinstance(items, dict):
        items_list = []
        items.list.append(items)
    elif isinstance(items, str):
        items_list = json.loads(items)
    else:
        items_list = items

    booked_items = []

    user_email = session.get('user_email', '')
    print(user_email)

    for single_item in items_list:

        item = Item.query.get_or_404(single_item["id"])

        borrow_date     = datetime.strptime(single_item['borrow_date'], '%Y-%m-%d')
        return_date     = datetime.strptime(single_item['return_date'], '%Y-%m-%d')

        if is_item_available(item.id, borrow_date, return_date):

            item.status = 'booked'

            new_booking = Booking(  item_id         = item.id, 
                                    item_name       = item.name,
                                    borrower_name   = borrower_name, 
                                    borrower_email  = borrower_email,
                                    borrower_phone  = borrower_phone,
                                    user_email      = user_email,
                                    borrow_date     = borrow_date,
                                    return_date     = return_date)

            db.session.add(new_booking)
            booked_items += [new_booking]

        else:
            flash(f'Selected dates are not available for booking.', 'danger')
            return redirect(url_for('cart',items=items, booked_dates=booked_dates))

    db.session.commit()

    # Send email and flash success message
    response = send_email(  borrower_email= borrower_email,
                            borrower_name = borrower_name,
                            borrower_phone= borrower_phone,
                            borrow_date   = borrow_date.date(),
                            return_date   = return_date.date(),
                            subject       = "Booking - Do Not Reply",
                            text_content  = "",
                            html_content  = "",
                            items         = booked_items,
                            type_of_mail  = 'booking',
                            user_email    = user_email)

    flash(f'All items booked successfully!', 'success')
    session['cart'] = {}  # Clear the cart
        
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

        # Remove any custom session keys, but keep Flask-Login's session structure intact
        session.pop("microsoft_user", None)
        session.pop("flow", None)
        session.pop("borrower_info", None)
        session.pop("user_email", None)
        # Don't use session.clear()

        session.modified = True  #  force session to update in some Flask versions
        flash("Your session has expired. Please log in again.", "warning")
        return redirect(url_for('login'))

    bookings = Booking.query.order_by(Booking.borrow_date.desc()).all()
    return render_template('bookings_list.html', bookings=bookings)


@app.route('/book_cart', methods=['POST','GET'])
# @login_required
def book_cart():
    """
    Main "add to cart" function. For single or bulk addition 
    """

    borrower_name       = request.form.get("borrower_name")
    borrower_email      = request.form.get("borrower_email")
    borrower_phone      = request.form.get("borrower_phone")
    items_json          = request.form.get('itemsJSON')

    if items_json:
        json_data = json.loads(items_json)

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
        # Convert dates from string to date objects if needed
        borrow_date = datetime.strptime(item["borrow_date"], '%Y-%m-%d')
        return_date = datetime.strptime(item["return_date"], '%Y-%m-%d')

        # Append to our cart_items list
        cart_items.append({
            "borrower_name"     : borrower_name,
            "borrower_email"  : borrower_email,
            "borrower_phone"    : borrower_phone,
            "id"                : item['id'],
            "name"              : item['name'],
            "location"          : item['location'],
            "borrow_date"       : borrow_date.strftime('%Y-%m-%d'),  # Convert back to string for JSON serialization
            "return_date"       : return_date.strftime('%Y-%m-%d')
        })

    # Save to session (as JSON)
    session['cart'] = cart_items

    flash("Successfully added to cart", 'success' )

    return redirect(url_for('home'))


# Bulk delete. TODO:implement, inactive as of now
@app.route('/delete_bulk', methods=['POST','GET'])
def delete_bulk():
    pass


def model_to_dict(model_instance):
    """
    Convert an SQLAlchemy model instance into a dictionary.
    Args: model_instance (db.Model): The SQLAlchemy model instance.
    Returns: dict: A dictionary representation of the model instance.
    """
    
    return {column.name: getattr(model_instance, column.name) for column in model_instance.__table__.columns}


@app.route('/cart')
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
            item_detail['borrower_email'] = cart_item.get('borrower_email')
            item_detail['borrower_phone']   = cart_item.get('borrower_phone')

            items_with_booking_info.append(item_detail)

    items_for_cart = items_with_booking_info

    if 'borrower_info' not in session:
        borrower_info = False
    else:
        if session.get('borrower_info') == {}:
            borrower_info = False
        else:
            borrower_info = json.dumps(session.get('borrower_info'))

    return render_template('cart.html', items=items_with_booking_info, items_for_cart = items_for_cart, borrower_info=borrower_info, booked_dates=all_booking_dates)


@app.route('/remove_from_cart/<item_id>', methods=['GET', 'POST'])
def remove_from_cart(item_id):
    
    cart = session.get('cart', {})

    # Check if we should remove a single item or clear the entire cart
    if item_id == 'all':
        session['cart'] = {}  # Clear the cart
        flash('Cart emptied successfully', 'success')
    elif (int(item_id)-1) <= len(cart):
        del cart[int(item_id)-1]
        session['cart'] = cart
        flash('Item removed from cart successfully', 'success')
    else:
        flash('Item not found in cart', 'error')

    # Redirect back to the cart page
    return redirect(url_for('cart'))


@app.route('/lend/<int:booking_id>')
@admin_required
def lend_item(booking_id):
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    booking = Booking.query.get_or_404(booking_id)
    booking.status = 'lent'
    db.session.commit()
    
    flash(f'Item {booking.item_name} marked as lent!', 'success')
    return redirect(request.referrer or url_for('home'))


@app.route('/return/<int:booking_id>', methods=['POST', 'GET'])
@admin_required
def return_item(booking_id):

    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    booking = Booking.query.get_or_404(booking_id)
    name_of_deleted_item = booking.item_name
    db.session.delete(booking)
    db.session.commit()
    
    flash(f'Item {name_of_deleted_item} marked as returned!', 'success')

    # Send email
    actionType  = request.form.get("formAction")
    note        = request.form.get('note')
    if (actionType == 'deny'):
        response = send_email(  borrower_email= booking.borrower_email,
                                borrower_name = booking.borrower_name,
                                borrower_phone= booking.borrower_phone,
                                borrow_date   = booking.borrow_date.date(),
                                return_date   = booking.return_date.date(),
                                subject       = "Booking denied - Do Not Reply",
                                text_content  = "",
                                html_content  = "",
                                items         = [booking],
                                type_of_mail  = 'deny',
                                note          = note)

    return redirect(request.referrer)


@app.route('/add_item', methods=['GET', 'POST'])
@admin_required 
def add_item():
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        new_item = Item(name=name, location=location)
        db.session.add(new_item)
        db.session.commit()
        flash(f'Item {name} added successfully!', 'success')
        return redirect(url_for('home'))

    return render_template('add_item.html')


@app.route('/edit_item/<int:item_id>', methods=['GET', 'POST'])
@login_required
def edit_item(item_id):
    
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'GET':
        item = Item.query.get_or_404(item_id)
        return render_template('edit_item.html', item=item)

    if request.method == 'POST':
        name        = request.form.get('name')
        location    = request.form.get('location')
        
        existing_item = Item.query.get(item_id)
        
        existing_item.name      = name 
        existing_item.location  = location 

        db.session.commit()

        flash(f'Item {name} edited successfully!', 'success')
        return redirect(url_for('home'))

    return render_template('add_item.html')


@app.route('/delete_item/<int:item_id>', methods=['GET', 'POST'])
@login_required
def delete_item(item_id):
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('home'))

    if request.method == 'GET':
        item = Item.query.get_or_404(item_id)
        name = item.name
        Item.query.filter_by(id=item_id).delete()
        db.session.commit()
        flash(f'Item {name} deleted successfully!', 'success')
        return redirect(url_for('home'))

    return redirect(url_for('home'))


@app.route('/test-job')
@admin_required
def test_job():
    """
    Visit this route to execute the daily check 
    for items due to return.
    """
    
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('home'))

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
            key = (booking.borrower_email, booking.borrower_name)
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
            logger.info('check_and_send_reminders_tomorrow executed. Email sent to: ' + email)

        # Log additional details if needed, such as user info, email contents, etc.
        logger.info(f'Sending reminders finished.')


def send_email(borrower_email, borrower_name, borrower_phone, borrow_date, return_date, subject, text_content, html_content, items, type_of_mail=None, **kargs):
    mail_body = {}

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
    
    mail_from = "booking@ideas-block.com"
    name_from = "MISC booking - DO NOT Reply"

    recipients = [
        {
            "name": borrower_name,
            "email": borrower_email,
        }
    ]

    if LOCALHOST:
        admin_contacts = [
            # {
            #     "name": borrower_name,
            #     "email": borrower_email,
            # },
            {
                "email":"roberto.becerra@lmta.lt",
                "name":"Roberto"
            }
        ]
    else:

        admin_contacts = [
            # {
            #     "name": borrower_name,
            #     "email": borrower_email,
            # },
            {
                "name": "Edvinas",
                "email": "edvinas.siliunas@lmta.lt",
            },
            {
                "name": "Edvinas Gmail",
                "email": "siliunas.edvinas@gmail.com",
            },
            {
                "name": "Roberto",
                "email": "roberto.becerra@lmta.lt",
            },
            {
                "name": "Julius",
                "email": "julius.aglinskas@lmta.lt",
            },
            {
                "name": "Mantautas",
                "email": "mantautas.krukauskas@lmta.lt",
            }
        ]

    # build BCC list while avoiding duplicates
    bcc = admin_contacts.copy()
    for admin in admin_contacts:
        
        admin_email          = admin["email"].lower()
        borrower_email_lower = borrower_email.lower()

        # skip if borrower is also one of the admins
        if borrower_email_lower != admin_email:
            bcc.append({ "name": borrower_name, "email": borrower_email_lower})

    if type_of_mail == 'booking' or type_of_mail == 'deny':
        # mailer.set_bcc_recipients(bcc, mail_body)
        pass
    else:
        bcc = []
        bcc.append({ "name": borrower_name, "email": borrower_email_lower})

    request = (IdentityBuilder()
          .identity_id("MISC")
          .name(name_from)
          .reply_to_email("misc@lmta.lt")
          .reply_to_name(name_from)
          .add_note(False)
          .build_update_request())

    # response = mailer.identities.update_identity(request) 

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

if not LOCALHOST:
    scheduler.add_job(func=check_and_send_reminders_tomorrow, trigger="cron", hour=22, minute=22)
    scheduler.start()


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description = 'MISC booking system')
    parser.add_argument('-d', '--dev', help="development mode enabled", default=False, action="store_true")      # development mode
    args = parser.parse_args()

    if args.dev:
        LOCALHOST = True
    else:
        LOCALHOST = False

    # TODO: Why is this here?
    create_admin_user()  # Call the function to create admin user

    if not LOCALHOST:

        try:
            # Run the Flask app (this is a blocking call)
            app.run(debug=True, host='0.0.0.0', use_reloader=True)
        except (KeyboardInterrupt, SystemExit):
            # Shut down the scheduler when exiting the app
            scheduler.shutdown() 

    else:
        # Using 127.0.0.1 instead of 0.0.0.0 to avoid port overlap with airPlay
        app.run(debug=True, host='0.0.0.0', port=5001, use_reloader=True)
    
