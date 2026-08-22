(function () {
    let theme = 'system';
    try {
        const saved = localStorage.getItem('theme');
        if (saved === 'light' || saved === 'dark' || saved === 'system') theme = saved;
    } catch (_error) {
        // Private browsing can disable localStorage.
    }
    document.documentElement.dataset.theme = theme;

    // 脚本健康看门狗：script.js 完成初始化后会设置 __videoToNoReady。
    // 如果页面渲染成功但脚本因缓存错位、浏览器过旧等没有运行，
    // 所有按钮都会「点了没反应」——这里给出可见提示而不是静默失败。
    window.__videoToNoReady = false;
    window.addEventListener('DOMContentLoaded', function () {
        window.setTimeout(function () {
            if (window.__videoToNoReady) return;
            var banner = document.createElement('div');
            banner.className = 'script-failure-banner';
            banner.setAttribute('role', 'alert');
            banner.textContent =
                '页面脚本未正常加载，按钮可能无响应。请按 Ctrl+F5 强制刷新；' +
                '若仍无效，请通过托盘菜单「打开界面」重新访问。';
            document.body.appendChild(banner);
        }, 2500);
    });
})();