from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SECRET_KEY'] = 'your_secret_key'
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
    password = db.Column(db.String(60), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

# Create an admin user
admin_user = User(username='admin', password=generate_password_hash('password', method='sha256'), is_admin=True)
db.create_all()
db.session.add(admin_user)
db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def home():
    return render_template('home.html')

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
            return redirect(url_for('dashboard'))
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
@login_required
def book_item(item_id):
    item = Item.query.get_or_404(item_id)

    if request.method == 'POST':
        borrower_name = request.form.get('borrower_name')
        borrower_contact = request.form.get('borrower_contact')
        borrow_date = datetime.strptime(request.form.get('borrow_date'), '%Y-%m-%d')
        
        item.status = 'booked'
        item.borrower_name = borrower_name
        item.borrower_contact = borrower_contact
        item.borrow_date = borrow_date
        
        db.session.commit()
        
        flash(f'Item {item.name} booked successfully!', 'success')
        return redirect(url_for('dashboard'))

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
    return redirect(url_for('dashboard'))

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
    db.session.commit()
    
    flash(f'Item {item.name} marked as returned!', 'success')
    return redirect(url_for('dashboard'))

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
        return redirect(url_for('dashboard'))

    return render_template('add_item.html')

# Admin page route (accessible only by admin users)
@app.route('/admin_page')
@login_required
def admin_page():
    if not current_user.is_admin:
        flash('Permission denied. You do not have admin privileges.', 'danger')
        return redirect(url_for('dashboard'))

    return render_template('admin_page.html')

if __name__ == '__main__':
    app.run(debug=True)
