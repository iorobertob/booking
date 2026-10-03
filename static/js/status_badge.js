/* Booking status badge classes — JS mirror of the status_badge() macro in
   templates/macros.html, for statuses rendered client-side in detail modals.
   Keep the two in sync. */
function statusBadgeClass(status) {
    switch (status) {
        case 'booked':   return 'bg-warning text-dark';
        case 'approved': return 'bg-info text-dark';
        case 'lent':     return 'bg-primary';
        case 'returned': return 'bg-success';
        case 'denied':   return 'bg-danger';
        default:         return 'bg-secondary';
    }
}
