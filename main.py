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
pymysql.install_as_MySQLdb()

LOCALHOST = True

app = Flask(__name__, static_folder='images')

# for prefix forwarding
app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

if not LOCALHOST:
    app.config['APPLICATION_ROOT'] = '/booking'

if LOCALHOST:
    vars_path = "vars/vars.json"
else:
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
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)
    borrower_name = db.Column(db.String(100), nullable=False)
    borrower_contact = db.Column(db.String(100), nullable=False)
    borrow_date = db.Column(db.DateTime, nullable=False)
    return_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='booked')
    item = db.relationship('Item', back_populates='bookings')

# Define the Item model
class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    # borrower_name = db.Column(db.String(100), default='')
    # borrower_contact = db.Column(db.String(100), default='')
    # borrow_date = db.Column(db.DateTime, nullable=True)
    # return_date = db.Column(db.DateTime, nullable=True)
    manual_link = db.Column(db.String(200), default='')
    photo_path = db.Column(db.String(200), default='')
    bookings = db.relationship('Booking', order_by=Booking.id, back_populates='item')

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
@app.route('/item/<int:item_id>')
def item_details(item_id):
    item = Item.query.get_or_404(item_id)
    bookings = Booking.query.filter_by(item_id=item_id).all()

    booking_dates = get_bookings_list(item_id=item_id)
    booked_dates  = []
    for booking in booking_dates:
        booked_dates.extend(get_all_dates_between(booking["borrow_date"], booking["return_date"]))
    # Convert dates to string format
    booked_dates_str = [date.strftime('%Y-%m-%d') for date in booked_dates]
    return render_template('item_details.html', item=item, bookings=bookings, booking_dates=booking_dates, booked_dates=booked_dates)

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

            items = [item]

            # Send email and flash success message
            response = send_email(  borrower_email= borrower_contact,
                                borrower_name = borrower_name,
                                borrow_date   = borrow_date.date(),
                                return_date   = return_date.date(),
                                subject       = "Booking - Do Not Reply",
                                text_content  = "",
                                html_content  = "",
                                items         = items)
        
            flash(f'Item {item.name} booked successfully!', 'success')
            return redirect(url_for('home'))
        
        else:
            flash(f'Selected dates are not available for booking.', 'danger')
            return redirect(url_for('book_item', item_id=item_id))

        return redirect(url_for('book_item', item_id=item_id))
        

    return render_template('book_item.html', item=item, booked_dates=booked_dates_str)

# Bulk booking
@app.route('/book_bulk', methods=['POST','GET'])
def book_bulk():
    items = request.args.getlist('items')
    action = request.args.get('action')
    if action == "book":
        # Process booking for items
        all_booking_dates = []
        for item in items:
            bookings     = get_bookings_list(item_id=item)
            booked_dates = []
            for booking in bookings:
                booked_dates.extend(get_all_dates_between(booking["borrow_date"], booking["return_date"]))
            # Convert dates to string format
            booked_dates_str = [date.strftime('%Y-%m-%d') for date in booked_dates]

            all_booking_dates += booked_dates_str
  
        return render_template('book_bulk.html', items=items, booked_dates=all_booking_dates)

    elif action == "delete":
        # Process deletion for selected items
        return redirect(url_for('home'))

    if request.method == 'POST':
        items = request.args.getlist('items')
        booked_dates = request.args.getlist('booked_dates')

        # Process the booking form
        borrower_name = request.form.get('borrower_name')
        borrower_contact = request.form.get('borrower_contact')
        borrow_date = datetime.strptime(request.form.get('borrow_date'), '%Y-%m-%d')
        return_date = datetime.strptime(request.form.get('return_date'), '%Y-%m-%d')

        booked_items = []

        for item_id in items:
            item = Item.query.get_or_404(item_id)


            if is_item_available(item_id, borrow_date, return_date):
                # Proceed with booking
                # item.status = 'booked'

                new_booking = Booking(  item_id         =item_id, 
                                        item_name       =item.name,
                                        borrower_name   =borrower_name, 
                                        borrower_contact=borrower_contact,
                                        borrow_date     =borrow_date,
                                        return_date     =return_date)

                db.session.add(new_booking)
                
                booked_items += [item]

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
                            items         = booked_items)

        flash(f'All item booked successfully!', 'success')
        return redirect(url_for('home'))
    return redirect(url_for('book_bulk',items=items, action="book"))


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
    return redirect(url_for('home'))

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

# Email delivery system 
def send_email(borrower_email, borrower_name, borrow_date, return_date, subject, text_content, html_content, items):
    mail_body = {}

    # Assuming you're passing variables like 'name' and 'booking_date' to your template
    html_content = render_template('booking_email.html', 
                                    borrower_name  = borrower_name, 
                                    borrower_email = borrower_email,
                                    borrow_date = borrow_date,
                                    return_date = return_date,
                                    now         = datetime.utcnow(),
                                    items       = items)
    
    text_content = "Your booking has been registered. Unless you receive a cancellation, please come to the MISC to take the item(s)"
    
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

    bcc = [
        # {
        #     "name": "Edvinas",
        #     "email": "edvinas.siliunas@lmta.lt",
        # },
        # {
        #     "name": "Edvinas Gmail",
        #     "email": "siliunas.edvinas@gmail.com",
        # },
        {
            "name": "Roberto",
            "email": "roberto.becerra@lmta.lt",
        },
        # {
        #     "name": "Julius",
        #     "email": "julius.aglinskas@lmta.lt",
        # },
        # {
        #     "name": "Mantautas",
        #     "email": "mantautas.krukauskas@lmta.lt",
        # }
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
    mailer.set_plaintext_content(text_content, mail_body)
    mailer.set_html_content(html_content, mail_body)
    mailer.set_bcc_recipients(bcc, mail_body)
    mailer.set_reply_to(reply_to, mail_body)

    # Send the email
    response = mailer.send(mail_body)
    print(response)
    return response



# Admin page route (accessible only by admin users)
@app.route('/admin_page')
@login_required
def admin_page():
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('home'))

    return render_template('admin_page.html')

if __name__ == '__main__':
    
    create_admin_user()  # Call the function to create admin user
    app.run(debug=True, host='0.0.0.0')
