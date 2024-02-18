# Booking System


## TODO:
* Bulk booking, by checking a ceckbox
* Grouping items and show qty available ?
* Categories
* Booking rooms in the same website.
* Connect to Google calendar
* Note on return about state of item
* Comments on items page
* Some items are not bookable.
* Add [random] colours to booking table to display on the fullcalendar




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