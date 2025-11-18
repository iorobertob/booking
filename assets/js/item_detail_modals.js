
// Example disabled dates: ["2023-01-01", "2023-01-02"]
let data = document.currentScript.dataset;
let disabledDates = [];
let items = [];

function initDatePickers(){

    // Get disabled dates to grey out in date pickers. 
    // This needs a json loaaded into a form
    if (data.disabled_dates != null || data.disabled_dates == ""){

        disabledDates = JSON.parse(data.disabled_dates || '[]');
    }
    else{

        disabledDates   = JSON.parse(document.getElementById("booked_dates").value);
        items           = JSON.parse(document.getElementById("itemsJSON").value);
    }

    // Disable dates before today on flatpickr calendar drop down menu
    var returnDatePickerRaw = flatpickr(".datepicker", {
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
    const returnDatePickers = Array.isArray(returnDatePickerRaw)
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


function updateReturnDatePickerMinDate(dateStr) {
    // returnDatePicker.set("minDate", dateStr);
    returnDatePickers.forEach(instance => {
        instance.set("minDate", dateStr);
    });
}

function loadBorrowerInfo(form, info){

    // Try in case info is not the righ type
    try{
        form.elements['borrower_name'].value    = info.borrower_name
        form.elements['borrower_email'].value = info.borrower_email
        form.elements['borrower_phone'].value   = info.borrower_phone
    }
    catch (error){
        console.log(error);
    }
}


async function setBorrowerInfoOnBackend(name, contact, phone){

    var name    = name.trim();
    var contact = contact.trim();
    var phone   = phone.trim();

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

function updateJSON(form, items_json){

    var borrowing_date  = form.elements['borrow_date'].value;
    var returning_date  = form.elements['return_date'].value;
    
    // items_json is null when items_for_cart is given from flask
    if (items_json == null ){
        items = JSON.parse(data.item_for_cart || '{}');

    }
    else{
        items = items_json;
    }
    
    const   itemsArray = Array.isArray(items) ? items : [items];
    var     itemsJSON = [];

    itemsArray.forEach(item => {
        itemsJSON.push({id         : item.id,
                        name       : item.name,
                        location   : item.location,
                        borrow_date: borrowing_date,
                        return_date: returning_date});
    });

    form.elements['itemsJSON'].value = JSON.stringify(itemsJSON);

}

async function submitForm(actionType, form) {

    // Set the action type in the hidden input
    form.elements['formAction'].value = actionType;

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
    

    // Now check the entire form
    var isValidForm = form.checkValidity();

    if (isValidForm) {
        // Optional: handle additional checks like return_date here
    } else {
        form.reportValidity(); // show errors
        return false;
    }
    
    // Set form action based on the actionType
    if (actionType === 'book') {
        updateJSON(form, items )
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
        

    } else if (actionType === 'bulk_book') {

        form.action = data.action_bulk || "#";

        updateJSON(form, items )
    }

    if(form.elements['borrower_name']){
        var name    = form.elements['borrower_name'].value;
        var contact = form.elements['borrower_email'].value;
        var phone   = form.elements['borrower_phone'].value;

        const result = await setBorrowerInfoOnBackend(name, contact, phone); 

    }

    // Submit the form
    form.submit();
}