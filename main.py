from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pymysql
import json
pymysql.install_as_MySQLdb()

LOCALHOST = False

app = Flask(__name__, static_folder='images')

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
    
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Define the Item model
class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='available')
    borrower_name = db.Column(db.String(100), default='')
    borrower_contact = db.Column(db.String(100), default='')
    borrow_date = db.Column(db.DateTime, nullable=True)
    return_date = db.Column(db.DateTime, nullable=True)
    manual_link = db.Column(db.String(200), default='')
    photo_path = db.Column(db.String(200), default='')

# Define the User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

@app.route('/boo0king/images/<path:filename>')
def custom_static(filename):
    return render_template('login.html')
    # return send_from_directory('images', filename)

@app.route('/images/<path:filename>', methods=['GET', 'POST'])
def custom_images(filename):
    return render_template('login.html')
    # return send_from_directory('images', filename)

@app.route('/images', methods=['GET', 'POST'])
def custom_images2():
    return render_template('login.html')
    # return send_from_directory('images', filename)

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
    return render_template('home.html', items=items)

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
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

# Book item route
@app.route('/book/<int:item_id>', methods=['GET', 'POST'])
def book_item(item_id):
    item = Item.query.get_or_404(item_id)

    if request.method == 'POST':
        borrower_name = request.form.get('borrower_name')
        borrower_contact = request.form.get('borrower_contact')
        borrow_date = datetime.strptime(request.form.get('borrow_date'), '%Y-%m-%d')
        return_date = datetime.strptime(request.form.get('return_date'), '%Y-%m-%d')
        
        item.status = 'booked'
        item.borrower_name = borrower_name
        item.borrower_contact = borrower_contact
        item.borrow_date = borrow_date
        item.return_date = return_date
        
        db.session.commit()
        
        flash(f'Item {item.name} booked successfully!', 'success')
        return redirect(url_for('home'))

    return render_template('book_item.html', item=item)

# Lent item route (accessible only by admin users)
@app.route('/lend/<int:item_id>')
@login_required
def lend_item(item_id):
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    item = Item.query.get_or_404(item_id)
    item.status = 'lent'
    db.session.commit()
    
    flash(f'Item {item.name} marked as lent!', 'success')
    return redirect(url_for('home'))

# Return item route (accessible only by admin users)
@app.route('/return/<int:item_id>')
@login_required
def return_item(item_id):
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    item = Item.query.get_or_404(item_id)
    item.status = 'available'
    item.borrower_name = ''
    item.borrower_contact = ''
    item.borrow_date = None
    item.return_date = None
    db.session.commit()
    
    flash(f'Item {item.name} marked as returned!', 'success')
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
def edit_item():
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    item = Item.query.get_or_404(item_id)
    return render_template('edit_item.html', item=item)

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
    app.run(debug=False, host='0.0.0.0')
