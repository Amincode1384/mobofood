/* =========================================================
   پنل مدیریت منو داخل داشبورد آشپزخانه
   ========================================================= */
(function () {
    "use strict";

    var openBtn = document.getElementById("mmOpenBtn");
    var overlay = document.getElementById("mmOverlay");

    if (!openBtn || !overlay) return;

    var closeBtn = document.getElementById("mmOverlayClose");
    var body = overlay.querySelector(".mm-overlay-body");

    function focusFirstField() {
        var target = overlay.querySelector("#mmName") || closeBtn;
        if (target) target.focus();
    }

    function isOpen() {
        return !overlay.hidden;
    }

    function open() {
        overlay.hidden = false;
        document.body.classList.add("mm-overlay-open");
        openBtn.setAttribute("aria-expanded", "true");
        overlay.scrollTop = 0;
        if (body) body.scrollTop = 0;
        focusFirstField();
    }

    function close() {
        if (!isOpen()) return;
        overlay.hidden = true;
        document.body.classList.remove("mm-overlay-open");
        openBtn.setAttribute("aria-expanded", "false");
        openBtn.focus();
    }

    openBtn.addEventListener("click", function () {
        if (isOpen()) close();
        else open();
    });

    if (closeBtn) closeBtn.addEventListener("click", close);

    overlay.addEventListener("click", function (event) {
        if (event.target === overlay) close();
    });

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape" || !isOpen()) return;

        // اگر نوار تأیید حذف باز است، اول آن بسته می‌شود
        var strip = overlay.querySelector("[data-confirm-strip]:not([hidden])");
        if (strip) return;

        close();
    });
})();
