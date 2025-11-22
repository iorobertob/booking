# Booking System

## TODO:
* add the lmta email to the booking item, for if they change and falsify identity
* for the pages that need admin privileges, now there is duplicate with decorator and logic inside the function. check that. 
* Turn add item and other mini pages into modals
* Add logic to refuse overlapping lending of booked items. 
* Tidy up emailing process once the bcc bug is fixed on mailersend
* Aggregate all booked items at once under the same booking model instance. In this case the booking instance should contain sub dicts for items with their own borrow dates. this must be backwards compatible. this shoul be for version 3.0
* network on LMTA_guest ????
* Find out if it is really necessary to send the booked dates back and forth or is it ok to handle all of that in the session. 
* Look for DATATABLES ALTERNATIVE
* Why is create_admin_user() where it is in the __main__ of main.py?
* refactor, url for book_cart and for book are always the same, no need to pass them as data to the CUSTOM MODAL JS script
* fix LOCALHOST logic so it can be set from __main__
* when typing data into the modal contact details there are errors in the console, what are those? = icloud passwords cannot deal with dynamicly created modals. 
* Delete button on home page is dangerous, move elsewhere or make a safety check (deleteSelectedItems)
* keep on sending reminders to return item for a long as it is "lent" and include that if its returned please ask the admin to mark it so. 
* When adding items, the locations should be on a dropdown menu.
* return to same page search/filter conditions when going back. 
* Send only one aggregated reminder email, not one per borrowed item.
* Decide whether or not to send remidner email to admins.?
* Bulk delete, by checking a box?
* Bulk deny booking, by checking a box.
* Mark as lent or returned in bulk 
* Grouping items and show qty available 
* Categories
* Booking rooms in the same website.
* Connect to Google calendar
* Note on return about state of item
* Comments on items page
* Some items whould be not bookable.
* Add [random] colours to booking table to display on the fullcalendar
* Check password created with MS Login


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

### v2.1 - 20251121 
- Collect borrower's phone also.
- On "add to cart" button, pre-populate borrow date with today day.
- Block on the calendar interface all days before today and those that are booked already.
- Add to cart also when selecting from the map.
- Saving borrower info in session and other details for better ux.
- New admin page for all the bookings / lent instances.
- Send email with a note on denial of bookings.
- Added about and terms of use pages. 
- "book" on item detail now shows a modal just like add to cart and clicking on the calendar.
- Refactor several variable names.
- Implemment login using MS Office 365
- Fixed bugs:
-- catch when there are duplicate recipient emails in the list. 
-- fixed some pages and table overflowing
-- improved phone interface
-- fixed empty booking
