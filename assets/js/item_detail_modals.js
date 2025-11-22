
let data = document.currentScript.dataset;
let disabledDates = [];
let items = [];

let  returnDatePickerRaw = null;
let  returnDatePickers   = null;

/**
 * Initializes the FullCalendar instance and renders booked dates and date selections.
 * 
 * @param {Array<Object>} booking_dates - List of booking records to block in the calendar.
 * Each object should contain:
 *   - {string} borrower_name - The name shown on the event
 *   - {string} borrow_date   - ISO date string for the start of the booking
 *   - {string} return_date   - ISO date string for the end of the booking
 * 
 * Functionality includes:
 * - Blocking out booked date ranges as background events
 * - Preventing overlap selection
 * - Handling long press selection for touch devices
 * - Updating all `.datepicker_borrow` and `.datepicker` fields on date selection
 * - Opening a booking modal when a valid range or date is clicked
 */
function setupCalendar(booking_dates){
    // Define this FIRST
    const isMobile = window.innerWidth < 768;

    // Convert booked dates to an array of event objects for FullCalendar
    var bookingDates  = booking_dates;

    var events = bookingDates.map(function(bookingDates) {
        var rand = Math.floor(Math.random() * 10);
        var colorQ = "rgb(" + (215 - rand * 3) + "," + (185 - rand * 5) + "," + (185 - rand * 10) + " )"; 

        return {
            title: bookingDates.borrower_name,
            start: new Date(new Date(bookingDates.borrow_date).getTime()),
            end: new Date(new Date(bookingDates.return_date).getTime() + 86400000), // Add one day to include the return date
            display: "background",
            allDay:true,
            color: colorQ,
        };
    });

    const calendarEl = document.getElementById('calendar')
    const calendar   = new FullCalendar.Calendar(calendarEl,{

        initialView: 'dayGridMonth',
        selectable: true,
        selectMirror: true,
        longPressDelay: 200, // in ms, how long you must press on touch to start selection
        selectLongPressDelay: 200, // iOS-friendly version
        selectMinDistance: 2, // how far you must drag before selection starts

        height: 'auto',
        expandRows: true,
        contentHeight: 'auto',
        themeSystem: 'bootstrap5', // optional, improves scaling
        aspectRatio: isMobile ? 0.85 : 1.35,

        headerToolbar: {
            left: 'prev,next today',
            center: 'title',
            right: isMobile ? '' : 'dayGridMonth,timeGridWeek',
        },

        // Block all days before today
        validRange: {
            start: new Date() 
        },

        events: events,
        selectAllow: function(selectInfo) {

            const start = selectInfo.start;
            const end   = selectInfo.end;

            // Check if any event overlaps with the selected date range
            const isOverlap = calendar.getEvents().some(function(event) {
                return (start < event.endStr && end > event.startStr);
            });

            // Block past days
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            if (start < today) return false;


            return !isOverlap; // If there is an overlap, disallow selection
        },
        select: function (info) {
                    // Format the date as needed, YYYY-MM-DD format
                    const selectedDate = moment(info.startStr).format('YYYY-MM-DD');

                    updateReturnDatePickerMinDate(selectedDate);

                    const startDate = info.startStr;
                    const endDate   = moment(info.end).subtract(1, 'days').format('YYYY-MM-DD');

                    // Format start & end dates as YYYY-MM-DD
                    const borrowDate = moment(info.startStr).format('YYYY-MM-DD');
                    const returnDate = moment(info.end).subtract(1, 'days').format('YYYY-MM-DD');

                    // Update all borrow date fields 
                    document.querySelectorAll('.datepicker_borrow').forEach(input => {
                        // Set raw input value
                        input.value = borrowDate;

                        // Update Flatpickr instance if present
                        if (input._flatpickr) {
                            input._flatpickr.setDate(borrowDate, true);
                        }
                    });

                    // Update all return date fields 
                    document.querySelectorAll('.datepicker').forEach(input => {
                        input.value = returnDate;

                        if (input._flatpickr) {
                            input._flatpickr.setDate(returnDate, true);
                        }
                    });

                    // Update minDate logic for all return pickers
                    if (typeof updateReturnDatePickerMinDate === 'function') {
                        updateReturnDatePickerMinDate(borrowDate);
                    }

                    $('#bookingModal').modal('show');
                                        
                    // Pre-populate the borrow_date field in the modal
                    // TODO: shouldn't this be not _1 but abstracted?
                    $('#borrow_date_1').val(selectedDate);
                    
                    // Show the modal
                    $('#bookingModal').modal('show');
        } ,          
        // For single-tap (mobile and desktop clicks)
        dateClick: function(info) {
            // Format the clicked date as YYYY-MM-DD
            const selectedDate = moment(info.dateStr).format('YYYY-MM-DD');

            // Update all borrow date fields
            document.querySelectorAll('.datepicker_borrow').forEach(input => {
                input.value = selectedDate;
                if (input._flatpickr) {
                    input._flatpickr.setDate(selectedDate, true);
                }
            });

            // Update minDate logic for all return pickers
            if (typeof updateReturnDatePickerMinDate === 'function') {
                updateReturnDatePickerMinDate(selectedDate);
            }
            $('#bookingModal').modal('show');
        },

    });
    calendar.render()

    let pressTimer;
    let isSelecting = false;

    calendarEl.addEventListener('touchstart', function(e) {
      pressTimer = setTimeout(() => {
        isSelecting = true;
        calendarEl.classList.add('fc-long-pressing');
      }, 300);
    });

    calendarEl.addEventListener('touchend', function(e) {
      clearTimeout(pressTimer);
      if (isSelecting) {
        isSelecting = false;
        calendarEl.classList.remove('fc-long-pressing');
      }
    });

}

/**
 * Initialize Flatpickr date pickers and apply disabled dates.
 * Example disabled dates: ["2023-01-01", "2023-01-02"]
 */
function initDatePickers(){

    /* 
    Get disabled dates to grey out in date pickers. 
    This needs a json loaded into a form
    */
    if (data.disabled_dates != null){

        disabledDates = JSON.parse(data.disabled_dates || '[]');
    }
    else{
        disabledDates   = JSON.parse(document.getElementById("booked_dates").value);
        items           = JSON.parse(document.getElementById("itemsJSON").value);
    }

    // Disable dates before today on flatpickr calendar drop down menu
    returnDatePickerRaw = flatpickr(".datepicker", {
        "disable": [
            function(date) {
                const today = new Date();
                today.setHours(0, 0, 0, 0); // normalize
                return date < today; // disable any past date
            },
            ...disabledDates
        ],
        "dateFormat": "Y-m-d",
    });

    // Normalize to array if it's not already
    returnDatePickers = Array.isArray(returnDatePickerRaw)
        ? returnDatePickerRaw
        : [returnDatePickerRaw];
        
    // Disable dates before today and on return flatpick, on flatpickr calendar drop down menu   
    flatpickr(".datepicker_borrow", {
        "disable": [
            function(date) {
                const today = new Date();
                today.setHours(0, 0, 0, 0); // normalize
                return date < today; // disable any past date
            },
            ...disabledDates
        ],
        "dateFormat": "Y-m-d",
        onChange: function(selectedDates, dateStr, instance) {
            // Update the minDate for the return date picker
            returnDatePickers.forEach(instance => {
                instance.set("minDate", dateStr);
            });
        }
    }); 
}

/**
 * Update the minimum allowed return date based on the selected borrow date.
 * @param {string} dateStr - The new minimum return date in YYYY-MM-DD format.
 */
function updateReturnDatePickerMinDate(dateStr) {
    returnDatePickers.forEach(instance => {
        instance.set("minDate", dateStr);
    });
}

/**
 * Load borrower information into a form from a given object.
 * @param {HTMLFormElement} form - The form to populate.
 * @param {Object} info - Object containing borrower_name, borrower_email, borrower_phone.
 */
function loadBorrowerInfo(form, info){

    // Try in case info is not the righ type
    if(form.elements['borrower_name']){
        
        form.elements['borrower_name'].value    = info.borrower_name
        form.elements['borrower_email'].value   = info.borrower_email
        form.elements['borrower_phone'].value   = info.borrower_phone  
    }
}

/**
 * Set the value of booked dates into the form's hidden field.
 * @param {HTMLFormElement} form - The form element.
 * @param {string} booked_dates - A JSON string of disabled/used dates.
 */
function loadBookedDatesInfo(form, booked_dates){
    try{
        form.elements["booked_dates"].value = booked_dates;
    }
    catch (error){
        console.log(error);
    }
}

/**
 * Sends borrower info to the backend and sets it in the session.
 * @param {string} name - Borrower's name.
 * @param {string} contact - Borrower's email.
 * @param {string} phone - Borrower's phone.
 * @returns {Promise<Response>} - A promise resolving to the fetch response.
 */
async function setBorrowerInfoOnBackend(name, contact, phone){
    name    = name.trim();
    contact = contact.trim();
    phone   = phone.trim();

    try{
        const response = await fetch('/set-borrower', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name, contact, phone }),
            credentials: 'same-origin'
        });

        if (!response.ok){
            throw new Error("Failed setting borrower info");
        }

        const borrower_info_response = await response;

        return borrower_info_response;

    }
    catch (error){
        console.error("Error on setting session: ", error);
        throw error;
    }
}

/**
 * Update the itemsJSON hidden input with current cart/form data.
 * @param {HTMLFormElement} form - The form being submitted.
 * @param {Object|null} items_json - Optional JSON data to override the item list.
 */
function updateJSON(form, items_json){

    // Dates from form if available
    let borrowing_date = form.elements['borrow_date']?.value || null;
    let returning_date = form.elements['return_date']?.value || null;   
    
    // items_json is null when items_for_cart is given from flask
    // TODO: This passing of information can be cleaner, only through variables
    if (items_json == null){
        if(data.item_for_cart != null){
            items = JSON.parse(data.item_for_cart || '{}')
        }
        else{
            items = items;
        }
    } 
    else{
        items = items_json;
    }

    const   itemsArray = Array.isArray(items) ? items : [items];
    var     itemsJSON  = [];

    var borrower_name   = form.elements['borrower_name' ] ? form.elements['borrower_name' ].value : null;
    var borrower_email  = form.elements['borrower_email'] ? form.elements['borrower_email'].value : null;
    var borrower_phone  = form.elements['borrower_phone'] ? form.elements['borrower_phone'].value : null; 

    for (const item of itemsArray) {
        borrower_name   = item.borrower_name    ? item.borrower_name    : borrower_name;
        borrower_email  = item.borrower_email   ? item.borrower_email   : borrower_email;
        borrower_phone  = item.borrower_phone   ? item.borrower_phone   : borrower_phone; 
        borrowing_date  = item.borrow_date      ? item.borrow_date      : borrowing_date;
        returning_date  = item.return_date      ? item.return_date      : returning_date; 
        
        itemsJSON.push({id              : item.id,
                        name            : item.name,
                        borrower_name   : borrower_name,
                        borrower_email  : borrower_email, 
                        borrower_phone  : borrower_phone,
                        location        : item.location,
                        borrow_date     : borrowing_date,
                        return_date     : returning_date});
    }

    form.elements['itemsJSON'].value = JSON.stringify(itemsJSON);
}

/**
 * Update the booked_dates hidden input in the form.
 * @param {HTMLFormElement} form - The target form.
 * @param {string} booked_dates - JSON string representing disabled dates.
 */
function updateBookedDates(form, booked_dates){
    form.elements['booked_dates'].value = booked_dates;
}

/**
 * Deny a booking by submitting a form with the given action type.
 * @param {string} actionType - The action being submitted (e.g. 'deny').
 * @param {HTMLFormElement} form - The form element to submit.
 * @param {number} item_id - The ID of the item (optional use).
 */
async function denyBooking(actionType, form, item_id){

    form.elements['formAction'].value = actionType;

    form.submit();

}

/**
 * Validates and submits a booking or cart form.
 * Handles borrower info, item JSON packing, route setting, and validation.
 *
 * @param {string} actionType - Either 'book' or 'add_to_cart'.
 * @param {HTMLFormElement} form - The form element to submit.
 * @returns {Promise<void>}
 */
async function submitForm(actionType, form) {

    // Get the email field only if it exists in the current modal
    if (form.elements['borrower_email']){
        const emailField = form.elements['borrower_email'];
        const email = emailField.value.trim();
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

        // Apply custom email validation first
        if (!emailRegex.test(email)) {
            emailField.setCustomValidity("Please enter a valid email address.");
        } else {
            emailField.setCustomValidity(""); // clear previous error
        }
    }

    // Validate borrow_date and return_date
    const borrowDateField = form.elements['borrow_date'];
    const returnDateField = form.elements['return_date'];
    if (borrowDateField) {
        if (!borrowDateField || !borrowDateField.value.trim()) {
            borrowDateField?.focus();
            return false;
        }
    }
    if (returnDateField) {
        if (!returnDateField || !returnDateField.value.trim()) {
            returnDateField?.focus();
            return false;
        }
    }
    
    // Now check the entire form
    var isValidForm = form.checkValidity();

    if (isValidForm) {
        // Handle additional checks like return_date here
    } else {
        form.reportValidity(); // show errors
        return false;
    }
    
    // Set the action type in the hidden input
    form.elements['formAction'].value = actionType;

    // Set form action based on the actionType
    if (actionType === 'book') {
        updateJSON(form, null )
        form.action = data.action_book || "#";


    } else if (actionType === 'add_to_cart') {

        form.action = data.action_cart || "#";

        // if diabled_dates exists we come from item_details page, otherwise from home
        if (data.disabled_dates != null || data.disabled_dates == ""){
            updateJSON(form, null )
        }
        else{
            updateJSON(form, items )
        }
    }

    if(form.elements['borrower_name']){
        var name    = form.elements['borrower_name'].value;
        var contact = form.elements['borrower_email'].value;
        var phone   = form.elements['borrower_phone'].value;

        const result = await setBorrowerInfoOnBackend(name, contact, phone); 
    }

    form.submit();
}