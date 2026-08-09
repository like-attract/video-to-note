(function () {
    let theme = 'system';
    try {
        const saved = localStorage.getItem('theme');
        if (saved === 'light' || saved === 'dark' || saved === 'system') theme = saved;
    } catch (_error) {
        // Private browsing can disable localStorage.
    }
    document.documentElement.dataset.theme = theme;
})();
