# Booking System


## TODO:
* Ask for email AND phone from borrowers
* Send only one aggregated reminder email, not one per borrowed item.
* Decide whether or not to send remidner email to admins.?
* Add cart - DONE
* Add discard X button to flash messages
* Bulk delete, by checking a box
* Bulk deny booking, by checking a box
* Grouping items and show qty available ?
* Categories
* Booking rooms in the same website.
* Connect to Google calendar
* Note on return about state of item
* Comments on items page
* Some items are not bookable.
* Add [random] colours to booking table to display on the fullcalendar
* Migrate to Django?



## DB migration

1. Install Flask-Migrate

` pip install Flask-Migrate`

Or install al packages from requirements.txt

2. Export app variable

`export FLASK_APP=main.py`

3. Init the migration

`flask db init`

4. Add migrations/ folder to .gitignore

5. Generate migration script:

`flask db migrate -m "Description of the changes"`

6. Apply the migration:

`flask db upgrade`


## Restart Gunicorn service
booking.service
1. sudo systemctl start booking.service
2. sudo systemctl enable booking.service
3. sudo systemctl daemon-reload



## [Potential] Issues
* When migrating the database, the flashed messages remain, and have to be force deleted. 
* When migrating the database, user cannot log out. 



## Changelog

### v2.0 - 20240826 
- Added menu bar
- Added cart functionality
- Changes in layout
- Added Bulk booking
