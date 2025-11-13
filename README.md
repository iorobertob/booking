# Booking System
## IMMEDIATE TODOs before current release
* when typing data into the modal contact details there are errors in the console, what are those? = icloud passwords cannot deal with dynamicly created modals. 
* delete book_item_modal.html and book_item_modal_add_to_cart.html
* table overflows, does not scale well. it is not reactive when mid size window. 
* the return date flatpickr is failing in bulk book, not updating after selection of borrow date



## TODO:
* add "add to cart" on home page
* cart interface for phone. 
* empty booking bug
* make an admin page for all the bookings / lent instances
* cross to close alerts on modals.
* Ask for email AND phone from borrowers
* Improve add to cart functionality, does not need name and contact every time. only on checkout. 
* on cart table add links to the name or id of the items. 
* Take add_to_cart functionality out of item_details (why is it there) and
* send email on aceptance or denial of bookings !!! important
* write a comment on why booking was denied
* specify if the status is clearly lent OR booked, not together
* keep on sending reminders to return item for a long as it is "lent" and include that if its returned please ask the admin to mark it so. 
* make list of booked items separately. 
* When adding items, the locations should be on a dropdown menu.
* return to same page search/filter conditions when going back. 
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
* Add the possibility to mark as lent or returned in bulk somehow
* Migrate to Django?
* Add policy text. 
* Tidy up emailing process once the bcc bug is fixed on mailersend
* add a back button in the modal for booking that comes from home page.	
* network on LMTA_guest




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

### v2.1 - 20251111 
- On "add to cart" button, pre-populate borrow date with today's day
- block on the calendar interface all days before today and those that are booked already
- add to cart also when selecting from the map
- "book" on item detail now shows a modal just like add to cart and clicking on the calendar
- Fixed bugs:
-- catch when there are duplicate recipient emails in the list. 
-- fixed cart page overflows
-- improved phone interface