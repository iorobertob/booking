from flask import session, Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify
from mailersend import emails
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

LOCALHOST = False

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
    mailer = emails.NewEmail(str(vars_json.get("mailersend_api_key")))
    
db = SQLAlchemy(app)

migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

class Booking(db.Model):
    id              = db.Column(db.Integer,     primary_key=True)
    item_id         = db.Column(db.Integer,     db.ForeignKey('item.id'), nullable = False)
    item_name       = db.Column(db.String(100), nullable=False)
    borrower_name   = db.Column(db.String(100), nullable=False)
    borrower_contact= db.Column(db.String(100), nullable=False)
    borrow_date     = db.Column(db.DateTime,    nullable=False)
    return_date     = db.Column(db.DateTime,    nullable=False)
    status          = db.Column(db.String(20),  default='booked')
    item            = db.relationship('Item',   back_populates='bookings')

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
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'assets/images'),
                          'favicon.ico',mimetype='image/vnd.microsoft.icon')

@app.route('/assets/<path:filename>')
def custom_static(filename):
    # return render_template('login.html')
    return send_from_directory('assets', filename)

@app.route('/assets/<path:filename>', methods=['GET', 'POST'])
def custom_images(filename):
    # return render_template('login.html')
    return send_from_directory('assets/images', filename)

# Create an admin user
def create_admin_user():
    with app.app_context():
        db.create_all()
        # Check if admin user already exists to avoid duplicate entries
        admin_exists = User.query.filter_by(username='admin').first() is not None
        if not admin_exists:
            admin_user = User(username='admin', password=generate_password_hash('Onahquahn2', method='pbkdf2'), is_admin=True)
            db.session.add(admin_user)
            db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def home():
    items = Item.query.all()
    availability = check_all_items_availability()

    bookings = []
    for item in items:
        item_bookings = Booking.query.filter_by(item_id = item.id, status = "lent").first()
        bookings.append(item_bookings)
    # session.pop('_flashes', None)
    return render_template('home.html', items=items, availability=availability, bookings = bookings)

# Login route
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

# Logout route
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

# Dashboard route (only accessible by logged-in users)
@app.route('/dashboard')
@login_required
def dashboard():
    items = Item.query.all()
    return render_template('dashboard.html', items=items)

# Items details 
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

    action = ''
    if request.method == 'POST':
        action = request.form.get('action')


    if action == "add_to_cart":

        borrower_name       = request.form.get("borrower_name")
        borrower_contact    = request.form.get("borrower_contact")

        items_json = request.form.get('itemsJSON')

        if items_json:
            json_data = json.loads(items_json)

        if 'cart' not in session:
            session['cart'] = []
            cart_items = []
        else:
            cart_items=session.get('cart')
            if session.get('cart') == {}:
                cart_items = []

        for item in json_data:
            # Convert dates from string to date objects if needed
            borrow_date = datetime.strptime(item["borrow_date"], '%Y-%m-%d')
            return_date = datetime.strptime(item["return_date"], '%Y-%m-%d')

            # Append to our cart_items list
            cart_items.append({
                "borrower_name"     : borrower_name,
                "borrower_contact"  : borrower_contact,
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

    item_for_cart = json.dumps(row2dict(item))

    return render_template('item_details.html', item=item, item_for_cart = item_for_cart, bookings=bookings, booking_dates=booking_dates, booked_dates=booked_dates)

# Utility function to get a dict from an SQALchemy result that is only one item (row) and
# convert it to a dict, for the purpose of building a JSON
def row2dict(row):
    d = {}
    for column in row.__table__.columns:
        d[column.name] = str(getattr(row, column.name))

    return d

# Check if all items passed are available
def check_all_items_availability():
    items = Item.query.all()
    date  = datetime.now()
    availability = []
    for item in items:
        if is_item_available(item.id,date, date):
            availability.append("Available")
        else:
            availability.append("Lent/booked")
    return availability

# Check if item is available.
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

# Function to get bookings
def get_bookings_list(item_id):
    # Fetch all bookings from the database
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

# Function to generate all dates between two dates, inclusive of both start and end dates
def get_all_dates_between(start_date, end_date):
    delta = end_date - start_date       # timedelta
    return [start_date + timedelta(days=i) for i in range(delta.days + 1)]

@app.route('/book/<int:item_id>', methods=['GET', 'POST'])
def book_item(item_id):
    # Displaying the booking form
    item = Item.query.get_or_404(item_id)

    bookings     = get_bookings_list(item_id=item_id)
    booked_dates = []
    for booking in bookings:
        booked_dates.extend(get_all_dates_between(booking["borrow_date"], booking["return_date"]))
    
    # Convert dates to string format
    booked_dates_str = [date.strftime('%Y-%m-%d') for date in booked_dates]

    if request.method == 'POST':
        # Process the booking form
        borrower_name = request.form.get('borrower_name')
        borrower_contact = request.form.get('borrower_contact')
        borrow_date = datetime.strptime(request.form.get('borrow_date'), '%Y-%m-%d')
        return_date = datetime.strptime(request.form.get('return_date'), '%Y-%m-%d')

        if is_item_available(item_id, borrow_date, return_date):
            # Proceed with booking
            item.status = 'booked'

            new_booking = Booking(  item_id         =item_id, 
                                    item_name       =item.name,
                                    borrower_name   =borrower_name, 
                                    borrower_contact=borrower_contact,
                                    borrow_date     =borrow_date,
                                    return_date     =return_date)

            db.session.add(new_booking)
            db.session.commit()

            items = [new_booking]

            # Send email and flash success message
            response = send_email(  borrower_email= borrower_contact,
                                    borrower_name = borrower_name,
                                    borrow_date   = borrow_date.date(),
                                    return_date   = return_date.date(),
                                    subject       = "Booking - Do Not Reply",
                                    text_content  = "",
                                    html_content  = "",
                                    items         = items,
                                    type_of_mail  = 'booking')
        
            flash(f'Item {item.name} booked successfully!', 'success')
            return redirect(url_for('home'))
        
        else:
            flash(f'Selected dates are not available for booking.', 'danger')
            return redirect(url_for('book_item', item_id=item_id))

        return redirect(url_for('book_item', item_id=item_id))
        
    item_for_cart = json.dumps(row2dict(item)) #otherwise cannot made them into a json in jinja

    return render_template('book_item.html', item=item, item_for_cart = item_for_cart, booked_dates=booked_dates_str)

# Bulk booking
@app.route('/book_bulk', methods=['POST','GET'])
def book_bulk():
    
    # Fetch the item details from the database
    item_ids    = request.args.getlist('items')
    items       = Item.query.filter(Item.id.in_(item_ids)).all()

    action = request.args.get('action')
    if request.method == 'POST':
        action = request.form.get('action')

    if action == "book":
        # Process booking for items
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
        items_dicts = json.dumps(items_dicts)       
        # return render_template('book_bulk.html', items= jsonpickle.encode(items), booked_dates=all_booking_dates)
        return render_template('book_bulk.html', items= items_dicts, booked_dates=all_booking_dates)

    elif action == "delete":
        # Process deletion for selected items
        return redirect(url_for('home'))

    elif action == "add_to_cart":

        borrower_name       = request.form.get("borrower_name")
        borrower_contact    = request.form.get("borrower_contact")

        items_json = request.form.get('itemsJSON')

        if items_json:
            json_data = json.loads(items_json)

        if 'cart' not in session:
            session['cart'] = []
            cart_items = []
        else:
            cart_items=session.get('cart')
            if session.get('cart') == {}:
                cart_items = []

        for item in json_data:
            # Convert dates from string to date objects if needed
            borrow_date = datetime.strptime(item["borrow_date"], '%Y-%m-%d')
            return_date = datetime.strptime(item["return_date"], '%Y-%m-%d')

            # Append to our cart_items list
            cart_items.append({
                "borrower_name"     : borrower_name,
                "borrower_contact"  : borrower_contact,
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

    if request.method == 'POST':
        # items = request.args.getlist('items')

        items_json = request.form.get('itemsJSON')

        if items_json:
            json_data = json.loads(items_json)


        booked_dates = request.args.getlist('booked_dates')

        from_cart = request.form.get('from_cart')

        if from_cart is None: # We are coming from simple bulk book modal
            # Process the booking form
            borrower_name       = request.form.get('borrower_name')
            borrower_contact    = request.form.get('borrower_contact')
            borrow_date = datetime.strptime(request.form.get('borrow_date'), '%Y-%m-%d')
            return_date = datetime.strptime(request.form.get('return_date'), '%Y-%m-%d')

        booked_items = []

        for single_item in json_data:
            item = Item.query.get_or_404(single_item["id"])

            if from_cart is not None:
                borrower_name       = single_item['borrower_name']
                borrower_contact    = single_item['borrower_contact']
                borrow_date         = datetime.strptime(single_item['borrow_date'], '%Y-%m-%d')
                return_date         = datetime.strptime(single_item['return_date'], '%Y-%m-%d')


            if is_item_available(item.id, borrow_date, return_date):
                # Proceed with booking
                new_booking = Booking(  item_id         =item.id, 
                                        item_name       =item.name,
                                        borrower_name   =borrower_name, 
                                        borrower_contact=borrower_contact,
                                        borrow_date     =borrow_date,
                                        return_date     =return_date)

                db.session.add(new_booking)
                
                booked_items += [new_booking]

            else:
                flash(f'Selected dates are not available for booking.', 'danger')
                return redirect(url_for('book_bulk',items=items, booked_dates=booked_dates))

        db.session.commit()

        # Send email and flash success message
        response = send_email(  borrower_email= borrower_contact,
                                borrower_name = borrower_name,
                                borrow_date   = borrow_date.date(),
                                return_date   = return_date.date(),
                                subject       = "Booking - Do Not Reply",
                                text_content  = "",
                                html_content  = "",
                                items         = booked_items,
                                type_of_mail  = 'booking')


        flash(f'All item booked successfully!', 'success')
        session['cart'] = {}  # Clear the cart
        return redirect(url_for('home'))

    return redirect(url_for('book_bulk',items= jsonpickle.encode(items), action="book"))

"""
Convert an SQLAlchemy model instance into a dictionary.
Args: model_instance (db.Model): The SQLAlchemy model instance.
Returns: dict: A dictionary representation of the model instance.
"""
def model_to_dict(model_instance):
    
    return {column.name: getattr(model_instance, column.name) for column in model_instance.__table__.columns}

# Cart page
@app.route('/cart')
def cart():

    # Combine item details with booking info from the session
    items_with_booking_info = []

    # Retrieve cart items from session
    cart_items = session.get('cart', '[]')  # Store as a JSON string
    
    if cart_items != {} and cart_items != '[]':

        # Extract item IDs from cart items
        item_ids = [item['id'] for item in cart_items]
        
        for cart_item in cart_items:
            item_detail = {}
            item_detail['id']               = cart_item.get('id')
            item_detail['name']             = cart_item.get('name')
            item_detail['location']         = cart_item.get('location')
            item_detail['borrow_date']      = cart_item.get('borrow_date')
            item_detail['return_date']      = cart_item.get('return_date')
            item_detail['borrower_name']    = cart_item.get('borrower_name')
            item_detail['borrower_contact'] = cart_item.get('borrower_contact')

            items_with_booking_info.append(item_detail)
  
    items_for_cart = json.dumps(items_with_booking_info)
    return render_template('cart.html', items=items_with_booking_info, items_for_cart = items_for_cart)

@app.route('/remove_from_cart/<item_id>', methods=['GET', 'POST'])
def remove_from_cart(item_id):
    # Retrieve the current cart from the session
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

# Lent item route (accessible only by admin users)
@app.route('/lend/<int:booking_id>')
@login_required
def lend_item(booking_id):
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    booking = Booking.query.get_or_404(booking_id)
    booking.status = 'lent'
    db.session.commit()
    
    flash(f'Item {booking.item_name} marked as lent!', 'success')
    return redirect(url_for('home'))

# Return item route (accessible only by admin users)
@app.route('/return/<int:booking_id>')
@login_required
def return_item(booking_id):
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    booking_to_delete = Booking.query.get_or_404(booking_id)
    name_of_deleted_item = booking_to_delete.item_name
    db.session.delete(booking_to_delete)
    db.session.commit()
    
    flash(f'Item {name_of_deleted_item} marked as returned!', 'success')
    # return redirect(url_for('home'))
    return redirect(request.referrer)

# Add item route (accessible only by admin users)
@app.route('/add_item', methods=['GET', 'POST'])
@login_required
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

# Add item route (accessible only by admin users)
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

# Add item route (accessible only by admin users)
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

# Visit this route to execute the daily check for items due to return
@app.route('/test-job')
@login_required
def test_job():

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
            key = (booking.borrower_contact, booking.borrower_name)
            borrower_data[key]['bookings'].append(booking)
            borrower_data[key]['items'].append(booking.item)

        # Send an email per borrower with all their bookings and items
        for (email, borrower_name), data in borrower_data.items():
            # Send email and flash success message
            response = send_email(  borrower_email= email,
                                    borrower_name = borrower_name,
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

# Email delivery system 
def send_email(borrower_email, borrower_name, borrow_date, return_date, subject, text_content, html_content, items, type_of_mail=None):
    mail_body = {}

    # Loading a different html template depending on what the email is about:
    if type_of_mail == 'return_reminder':
        plain_text_content = f"Hello, \n\nThis is a reminder to return the booked items by {return_date.strftime('%Y-%m-%d')}."
        html_content = render_template('return_item_email.html', 
                                    borrower_name  = borrower_name, 
                                    borrower_email = borrower_email,
                                    borrow_date = borrow_date,
                                    return_date = return_date,
                                    now         = datetime.now(),
                                    items       = items)
    elif type_of_mail == 'booking':
        plain_text_content = "Your booking has been registered. Unless you receive a cancellation, please come to the MISC to take the item(s)"
        html_content = render_template('booking_email.html', 
                                    borrower_name  = borrower_name, 
                                    borrower_email = borrower_email,
                                    borrow_date = borrow_date,
                                    return_date = return_date,
                                    now         = datetime.now(),
                                    items       = items)
    
    mail_from = {
    "name": "MISC booking - DO NOT Reply",
    "email": "booking@ideas-block.com",
    }

    recipients = [
        {
            "name": borrower_name,
            "email": borrower_email,
        }
    ]

    if LOCALHOST:
        bcc = [
            {
                "name": "Roberto",
                "email": "roberto.becerra@lmta.lt",
            }
        ]
    else:

        bcc = [
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

    reply_to = [
        {
            "name": "MISC",
            "email": "misc@lmta.lt",
        }
    ]

    mailer.set_mail_from(mail_from, mail_body)
    mailer.set_subject(subject, mail_body)
    mailer.set_mail_to(recipients, mail_body)
    mailer.set_plaintext_content(plain_text_content, mail_body)
    mailer.set_html_content(html_content, mail_body)
    mailer.set_bcc_recipients(bcc, mail_body)
    mailer.set_reply_to(reply_to, mail_body)

    # Send the email
    response = mailer.send(mail_body)
    return response

# Admin page route (accessible only by admin users)
@app.route('/admin_page')
@login_required
def admin_page():
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('home'))

    return render_template('admin_page.html')

if not LOCALHOST:
    scheduler.add_job(func=check_and_send_reminders_tomorrow, trigger="cron", hour=22, minute=22)
    scheduler.start()

if __name__ == '__main__':
    
    create_admin_user()  # Call the function to create admin user

    if not LOCALHOST:

        try:
            # Run the Flask app (this is a blocking call)
            app.run(debug=True, host='0.0.0.0', use_reloader=True)
        except (KeyboardInterrupt, SystemExit):
            # Shut down the scheduler when exiting the app
            scheduler.shutdown() 

    else:
       app.run(debug=True, host='0.0.0.0', use_reloader=True)
    
