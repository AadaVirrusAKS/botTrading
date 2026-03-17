/* Close user-menu dropdown when clicking outside */
document.addEventListener('click', function(e) {
    document.querySelectorAll('.user-menu.open').forEach(function(menu) {
        if (!menu.contains(e.target)) {
            menu.classList.remove('open');
        }
    });
});
