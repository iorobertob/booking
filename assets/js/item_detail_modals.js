// Example disabled dates: ["2023-01-01", "2023-01-02"]

let data = document.currentScript.dataset;
console.log("data");
console.log(data)
let disabledDates = [];
let items = [];
if (data.disabled_dates != null || data.disabled_dates == ""){
    console.log("if data disabled dates");
    disabledDates = JSON.parse(data.disabled_dates || '[]');
}
else{
    console.log("if not data disabled dates");
    console.log(document.getElementById("booked_dates").value);
    disabledDates = JSON.parse(document.getElementById("booked_dates").value);
    console.log(disabledDates);
    items = JSON.parse(document.getElementById("itemsJSON").value);
}



// Disable dates before today on flatpickr calendar drop down menu
var returnDatePicker = flatpickr(".datepicker", {
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
        returnDatePicker.forEach(instance => {
            instance.set("minDate", dateStr);
        });
    }
});

function updateReturnDatePickerMinDate(dateStr) {
    // returnDatePicker.set("minDate", dateStr);
    returnDatePicker.forEach(instance => {
        instance.set("minDate", dateStr);
    });
}

function updateJSON(form, items_json){

    var borrowing_date  = form.elements['borrow_date'].value;
    var returning_date  = form.elements['return_date'].value;
    
    // items_json is null when items_for_cart is given from flask
    if (items_json == null ){
        items = JSON.parse(data.item_for_cart || '{}');
        console.log("items");
        console.log(items);
    }
    else{
        items = items_json;
        console.log("items_json");
        console.log(items);
    }
    
    console.log("items");
    console.log(items);
    const   itemsArray = Array.isArray(items) ? items : [items];
    var     itemsJSON = [];

    itemsArray.forEach(item => {
        itemsJSON.push({id         : item.id,
                        name       : item.name,
                        location   : item.location,
                        borrow_date: borrowing_date,
                        return_date: returning_date});
    });

    console.log("itemsJSON");
    console.log(itemsJSON);
    form.elements['itemsJSON'].value = JSON.stringify(itemsJSON);

}

function submitForm(actionType, form) {
    // Set the action type in the hidden input
    form.elements['formAction'].value = actionType;

    var isValidForm = form.checkValidity();
    if (isValidForm)
    {
        var returndate = form.elements['return_date'].value
        
        var input = form.elements['return_date']
        
        input.setCustomValidity("p");
        if(input.checkValidity()) {
            input.setCustomValidity("watch me break");
            input.reportValidity();
        }
    }
    else
    {
        document.getElementById('booking_form').reportValidity();
        return false;
    }
    
    // Set form action based on the actionType
    if (actionType === 'book') {

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

    // Submit the form
    form.submit();
}