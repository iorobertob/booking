# booking


## TODO:
* Bulk booking, by checking a ceckbox
* Email notifications
* Grouping items and show qty available ?
* Possiblity to book in advance even if item is lent now. 
* make an item's page
* Categories
* Booking rooms in the same website.
* Connect to Google calendar
* Note on return about state of item
* Comments on items page
* some items are not bookable.




## DB migration

1. Install Flask-Migratte

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