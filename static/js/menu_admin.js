/* =========================================================
   مدیریت منو — افزودن، ویرایش، حذف، دسته‌بندی و موجودی
   ========================================================= */
(function () {
    "use strict";

    var form = document.getElementById("mmForm");
    if (!form) return;

    var nameInput = document.getElementById("mmName");
    var categorySelect = document.getElementById("mmCategory");
    var priceInput = document.getElementById("mmPrice");
    var stockInput = document.getElementById("mmStock");
    var descInput = document.getElementById("mmDesc");
    var imageInput = document.getElementById("mmImage");
    var previewBox = document.getElementById("mmPreview");
    var previewImg = document.getElementById("mmPreviewImg");
    var clearImageBtn = document.getElementById("mmImageClear");
    var submitBtn = document.getElementById("mmSubmit");
    var cancelEditBtn = document.getElementById("mmCancelEdit");
    var messageBox = document.getElementById("mmMessage");
    var listBox = document.getElementById("mmList");
    var totalBadge = document.getElementById("mmTotal");
    var formTitleText = document.getElementById("mmFormTitleText");
    var formIcon = document.getElementById("mmFormIcon");
    var editChip = document.getElementById("mmEditChip");
    var errorSummary = document.getElementById("mmErrorSummary");
    var confirmBox = document.getElementById("mmConfirm");
    var formCard = form.closest(".mm-card");

    var catToggle = document.getElementById("mmCategoryToggle");
    var catForm = document.getElementById("mmCategoryForm");
    var catName = document.getElementById("mmCategoryName");
    var catIcon = document.getElementById("mmCategoryIcon");
    var catSubmit = document.getElementById("mmCategorySubmit");
    var catCancel = document.getElementById("mmCategoryCancel");
    var catList = document.getElementById("mmCategoryList");
    var catCount = document.getElementById("mmCategoryCount");

    var CREATE_URL = "/api/foods/create/";
    var FOODS_URL = "/api/foods/";
    var CATEGORY_URL = "/api/categories/create/";
    var LOGIN_URL = "/admin/login/?next=/manage/menu/";

    var MAX_IMAGE_BYTES = 5 * 1024 * 1024;
    var ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];
    var NAME_MAX = 100;
    var DESC_MAX = 1000;

    var inFlight = false;
    var previewUrl = null;

    var mode = "add";
    var editingId = null;
    var editingRow = null;
    var baseline = null;
    var lastEditButton = null;
    var pendingDeleteRow = null;

    var FIELD_IDS = {
        name: "mmName",
        price: "mmPrice",
        stock: "mmStock",
        description: "mmDesc",
        category_id: "mmCategory",
        image: "mmImage"
    };

    var FIELD_LABELS = {
        name: "نام غذا",
        price: "قیمت",
        stock: "موجودی",
        description: "توضیحات",
        category_id: "دسته‌بندی",
        image: "تصویر"
    };

    /* ---------- helpers ---------- */

    function csrfToken() {
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    function esc(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function prefersReducedMotion() {
        return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    }

    function errorNode(field, scope) {
        return (scope || form).querySelector('.mm-error[data-for="' + field + '"]');
    }

    function setError(field, message) {
        var node = errorNode(field);
        if (!node) return;
        node.textContent = message;
        var control = form.querySelector('[name="' + field + '"]');
        if (control) control.setAttribute("aria-invalid", "true");
    }

    function clearError(field) {
        var node = errorNode(field);
        if (node) node.textContent = "";
        var control = form.querySelector('[name="' + field + '"]');
        if (control) control.removeAttribute("aria-invalid");
    }

    function clearErrors() {
        form.querySelectorAll(".mm-error").forEach(function (node) {
            node.textContent = "";
        });
        form.querySelectorAll('[aria-invalid="true"]').forEach(function (node) {
            node.removeAttribute("aria-invalid");
        });
        hideErrorSummary();
    }

    function showErrorSummary(fields) {
        if (!errorSummary || !fields.length) return;
        var items = fields.map(function (field) {
            var id = FIELD_IDS[field];
            return '<li><a href="#' + id + '">' + esc(FIELD_LABELS[field] || field) + "</a></li>";
        }).join("");

        errorSummary.innerHTML = "<strong>چند فیلد نیاز به اصلاح دارد:</strong><ul>" + items + "</ul>";
        errorSummary.hidden = false;
        errorSummary.focus();
    }

    function hideErrorSummary() {
        if (!errorSummary) return;
        errorSummary.hidden = true;
        errorSummary.innerHTML = "";
    }

    function showMessage(text, kind) {
        messageBox.textContent = text;
        messageBox.className = "mm-msg " + (kind || "info");
        messageBox.hidden = false;
    }

    function showAuthMessage() {
        messageBox.className = "mm-msg error";
        messageBox.innerHTML =
            'نشست شما منقضی شده یا دسترسی ندارید. ' +
            '<a href="' + LOGIN_URL + '">دوباره وارد شوید</a>؛ اطلاعات فرم حفظ شد.';
        messageBox.hidden = false;
    }

    function toAsciiDigits(value) {
        return String(value)
            .replace(/[۰-۹]/g, function (d) { return String(d.charCodeAt(0) - 0x06F0); })
            .replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 0x0660); });
    }

    function parsePositiveInt(raw) {
        var clean = toAsciiDigits(raw).replace(/[\s,٬٫]/g, "");
        if (!/^\d+$/.test(clean)) return null;
        var num = parseInt(clean, 10);
        if (!isFinite(num) || num < 0) return null;
        return num;
    }

    function normalizeForCompare(value) {
        return toAsciiDigits(value).replace(/[\s,٬٫]/g, "").trim();
    }

    function readJson(response) {
        return response.json()
            .catch(function () { return {}; })
            .then(function (data) {
                return {
                    response: response,
                    data: data,
                    isJson: typeof data === "object" && data !== null && Object.keys(data).length > 0
                };
            });
    }

    /* ---------- image preview ---------- */

    function revokePreview() {
        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
            previewUrl = null;
        }
    }

    function clearPreview() {
        revokePreview();
        previewImg.removeAttribute("src");
        previewBox.hidden = true;
    }

    function clearImage() {
        imageInput.value = "";
        clearPreview();
        clearError("image");
    }

    imageInput.addEventListener("change", function () {
        clearError("image");
        var file = imageInput.files[0];
        if (!file) {
            clearPreview();
            return;
        }
        if (ALLOWED_IMAGE_TYPES.indexOf(file.type) === -1) {
            setError("image", "فرمت تصویر مجاز نیست. فقط JPG، PNG یا WebP.");
            clearImage();
            return;
        }
        if (file.size > MAX_IMAGE_BYTES) {
            setError("image", "حجم تصویر نباید بیشتر از ۵ مگابایت باشد.");
            clearImage();
            return;
        }
        clearPreview();
        previewUrl = URL.createObjectURL(file);
        previewImg.src = previewUrl;
        previewImg.alt = "پیش‌نمایش " + file.name;
        previewBox.hidden = false;
    });

    clearImageBtn.addEventListener("click", clearImage);

    [nameInput, priceInput, stockInput, descInput, categorySelect].forEach(function (el) {
        el.addEventListener("input", function () { clearError(el.name); });
        el.addEventListener("change", function () { clearError(el.name); });
    });

    /* ---------- validation ---------- */

    function validate(isEdit) {
        var invalid = [];
        var firstInvalid = null;

        function fail(field, message, control) {
            setError(field, message);
            invalid.push(field);
            firstInvalid = firstInvalid || control;
        }

        var name = nameInput.value.trim();
        if (!name) {
            fail("name", "نام غذا را وارد کنید.", nameInput);
        } else if (name.length > NAME_MAX) {
            fail("name", "نام غذا نباید بیشتر از " + NAME_MAX + " کاراکتر باشد.", nameInput);
        }

        if (parsePositiveInt(priceInput.value) === null) {
            fail("price", "قیمت باید یک عدد صحیح و نامنفی باشد.", priceInput);
        }

        if (parsePositiveInt(stockInput.value) === null) {
            fail("stock", "موجودی باید یک عدد صحیح و نامنفی باشد.", stockInput);
        }

        if (descInput.value.trim().length > DESC_MAX) {
            fail("description", "توضیحات نباید بیشتر از " + DESC_MAX + " کاراکتر باشد.", descInput);
        }

        if (!isEdit && !categorySelect.value) {
            fail("category_id", "دسته‌بندی غذا را انتخاب کنید.", categorySelect);
        }

        if (invalid.length) {
            showErrorSummary(invalid);
            if (firstInvalid) firstInvalid.focus();
            return false;
        }

        return true;
    }

    /* ---------- list rendering ---------- */

    function activePillHtml(food, isActive) {
        return '<button type="button" class="mm-active-pill" role="switch"' +
            ' aria-checked="' + (isActive ? "true" : "false") + '"' +
            ' aria-label="نمایش «' + esc(food.name) + '» در منوی مشتری"' +
            ' data-active-switch="' + esc(food.id) + '">' +
            '<i class="bi ' + (isActive ? "bi-eye" : "bi-eye-slash") + '"></i>' +
            "<span data-active-text>" + (isActive ? "در منو" : "خارج از منو") + "</span>" +
            "</button>";
    }

    function rowHtml(food) {
        var thumb = food.image
            ? '<img src="' + esc(food.image) + '" alt="' + esc(food.name) + '">'
            : "<span>🍽</span>";
        var desc = food.description
            ? "<small>" + esc(food.description).slice(0, 140) + "</small>"
            : "";
        var inStock = Number(food.stock) > 0;
        var isActive = food.is_active !== false;

        return '<li class="mm-row' + (isActive ? "" : " is-inactive") + '" data-id="' + esc(food.id) + '"' +
            ' data-name="' + esc(food.name) + '"' +
            ' data-price="' + esc(food.price) + '"' +
            ' data-stock="' + esc(food.stock) + '"' +
            ' data-active="' + (isActive ? "1" : "0") + '"' +
            ' data-description="' + esc(food.description || "") + '">' +
            '<div class="mm-thumb">' + thumb + "</div>" +
            '<div class="mm-info"><strong>' + esc(food.name) + "</strong>" + desc + "</div>" +
            '<div class="mm-price">' + esc(food.price) + " تومان</div>" +
            '<span class="mm-badge ' + (inStock ? "in" : "out") + '" data-badge>' +
            (inStock ? "موجود" : "ناموجود") + "</span>" +
            '<div class="mm-row-actions">' +
            activePillHtml(food, isActive) +
            '<button type="button" class="mm-switch" role="switch" aria-checked="' +
            (inStock ? "true" : "false") + '" aria-label="تغییر وضعیت موجودی ' + esc(food.name) + '"' +
            ' data-stock-switch="' + esc(food.id) + '">' +
            '<span class="mm-switch-track"><span class="mm-switch-thumb"></span></span></button>' +
            '<button type="button" class="mm-edit-btn" data-edit="' + esc(food.id) + '"' +
            ' aria-label="ویرایش ' + esc(food.name) + '">' +
            '<i class="bi bi-pencil"></i> ویرایش</button>' +
            '<button type="button" class="mm-del-btn" data-delete="' + esc(food.id) + '"' +
            ' aria-label="حذف ' + esc(food.name) + '"><i class="bi bi-trash"></i></button>' +
            "</div>" +
            '<div class="mm-confirm-strip" data-confirm-strip hidden>' +
            "<span>«" + esc(food.name) + "» حذف شود؟ این کار برگشت‌پذیر نیست.</span>" +
            '<button type="button" class="mm-btn mm-btn-danger" data-confirm-delete="' + esc(food.id) + '">حذف قطعی</button>' +
            '<button type="button" class="mm-btn mm-btn-ghost" data-cancel-delete>انصراف</button>' +
            "</div>" +
            "</li>";
    }

    function refreshCounts() {
        listBox.querySelectorAll(".mm-group").forEach(function (group) {
            var rows = group.querySelectorAll(".mm-row").length;
            var count = group.querySelector(".mm-count");
            if (count) count.textContent = String(rows);
        });

        if (totalBadge) {
            totalBadge.textContent = String(listBox.querySelectorAll(".mm-row").length);
        }
    }

    function insertFoodRow(food) {
        var group = listBox.querySelector('.mm-group[data-category-id="' + food.category_id + '"]');

        if (!group) {
            group = document.createElement("section");
            group.className = "mm-group";
            group.setAttribute("data-category-id", food.category_id);
            group.innerHTML =
                "<h3><span class=\"mm-group-icon\">" + esc(food.category_icon || "🍽") + "</span>" +
                "<span class=\"mm-group-name\">" + esc(food.category) + "</span>" +
                '<span class="mm-count">0</span></h3>' +
                '<ul class="mm-rows"></ul>';
            listBox.appendChild(group);
        }

        var rows = group.querySelector(".mm-rows");
        rows.insertAdjacentHTML("afterbegin", rowHtml(food));

        var empty = document.getElementById("mmEmpty");
        if (empty) empty.hidden = true;

        refreshCounts();
    }

    function updateFoodRow(food) {
        var row = listBox.querySelector('.mm-row[data-id="' + food.id + '"]');
        if (!row) return null;

        row.setAttribute("data-name", food.name);
        row.setAttribute("data-price", String(food.price));
        row.setAttribute("data-stock", String(food.stock));
        row.setAttribute("data-description", food.description || "");

        var strong = row.querySelector(".mm-info strong");
        if (strong) strong.textContent = food.name;

        var info = row.querySelector(".mm-info");
        var small = row.querySelector(".mm-info small");
        if (food.description) {
            if (!small) {
                info.insertAdjacentHTML("beforeend", "<small></small>");
                small = row.querySelector(".mm-info small");
            }
            small.textContent = food.description.slice(0, 140);
        } else if (small) {
            small.remove();
        }

        var priceEl = row.querySelector(".mm-price");
        if (priceEl) priceEl.textContent = food.price + " تومان";

        var editBtn = row.querySelector(".mm-edit-btn");
        if (editBtn) editBtn.setAttribute("aria-label", "ویرایش " + food.name);

        applyStock(row, food.stock);

        if (food.is_active !== undefined) {
            applyActive(row, food.is_active);
        }

        row.classList.add("flash");
        window.setTimeout(function () { row.classList.remove("flash"); }, 1500);
        row.scrollIntoView({
            block: "nearest",
            behavior: prefersReducedMotion() ? "auto" : "smooth"
        });

        return row;
    }

    function applyStock(row, stock) {
        var value = Number(stock) || 0;
        var inStock = value > 0;

        row.setAttribute("data-stock", String(value));

        var sw = row.querySelector("[data-stock-switch]");
        if (sw) sw.setAttribute("aria-checked", inStock ? "true" : "false");

        var badge = row.querySelector("[data-badge]");
        if (badge) {
            badge.textContent = inStock ? "موجود" : "ناموجود";
            badge.className = "mm-badge " + (inStock ? "in" : "out");
        }

        if (mode === "edit" && Number(editingId) === Number(row.getAttribute("data-id"))) {
            stockInput.value = String(value);
        }
    }

    function applyActive(row, active) {
        var isActive = active !== false;

        row.setAttribute("data-active", isActive ? "1" : "0");
        row.classList.toggle("is-inactive", !isActive);

        var pill = row.querySelector("[data-active-switch]");
        if (!pill) return;

        pill.setAttribute("aria-checked", isActive ? "true" : "false");

        var text = pill.querySelector("[data-active-text]");
        if (text) text.textContent = isActive ? "در منو" : "خارج از منو";

        var icon = pill.querySelector("i");
        if (icon) icon.className = "bi " + (isActive ? "bi-eye" : "bi-eye-slash");
    }

    function toggleActive(row) {
        if (!row || row.getAttribute("data-busy") === "1") return;

        var id = row.getAttribute("data-id");
        var name = row.getAttribute("data-name") || "این غذا";
        var current = row.getAttribute("data-active") === "1";
        var next = !current;

        // از روی خوش‌بینی چیزی را عوض نمی‌کنیم: وضعیت فقط بعد از پاسخ موفق
        // سرور تغییر می‌کند، پس UI هرگز چیزی را که ذخیره نشده نشان نمی‌دهد.
        setRowBusy(row, true);

        fetch(FOODS_URL + id + "/toggle-active/", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken()
            },
            body: JSON.stringify({ is_active: next }),
            credentials: "same-origin"
        })
            .then(readJson)
            .then(function (result) {
                var response = result.response;
                var data = result.data;

                if (response.status === 403) {
                    showAuthMessage();
                    return;
                }

                if (response.status === 404) {
                    removeRow(row);
                    showMessage("این غذا دیگر وجود ندارد.", "error");
                    return;
                }

                if (!response.ok || data.success !== true) {
                    showMessage(data.error || "تغییر وضعیت غذا انجام نشد.", "error");
                    return;
                }

                var food = data.food || data;
                applyActive(row, food.is_active !== false);

                if (next) {
                    showMessage("«" + name + "» دوباره در منوی مشتری نمایش داده می‌شود.", "success");
                } else {
                    showMessage(
                        "«" + name + "» از منوی مشتری خارج شد؛ سفارش‌های قبلی تغییری نکردند.",
                        "success"
                    );
                }
            })
            .catch(function () {
                showMessage("ارتباط با سرور برقرار نشد؛ وضعیت قبلی حفظ شد.", "error");
            })
            .then(function () {
                setRowBusy(row, false);
            });
    }

    function removeRow(row) {
        if (!row) return;
        var group = row.closest(".mm-group");
        row.remove();
        if (group) group.classList.remove("editing");
        refreshCounts();
    }

    function setRowBusy(row, busy) {
        row.setAttribute("data-busy", busy ? "1" : "");
        row.querySelectorAll("button").forEach(function (btn) {
            btn.disabled = busy;
        });
    }

    /* ---------- modes ---------- */

    function submitLabel() {
        return mode === "edit"
            ? '<i class="bi bi-check2"></i> ذخیره تغییرات'
            : '<i class="bi bi-plus-lg"></i> افزودن غذا';
    }

    function busyLabel() {
        return mode === "edit"
            ? '<i class="bi bi-hourglass-split"></i> در حال ذخیره…'
            : '<i class="bi bi-hourglass-split"></i> در حال ثبت…';
    }

    function isDirty() {
        if (mode !== "edit" || !baseline) return false;

        return normalizeForCompare(nameInput.value) !== baseline.name ||
            normalizeForCompare(priceInput.value) !== baseline.price ||
            normalizeForCompare(descInput.value) !== baseline.description ||
            normalizeForCompare(stockInput.value) !== baseline.stock;
    }

    function setEditUi(isEditing) {
        form.classList.toggle("editing", isEditing);
        editChip.hidden = !isEditing;
        cancelEditBtn.hidden = !isEditing;
        formIcon.className = isEditing ? "bi bi-pencil-square" : "bi bi-plus-circle";
        submitBtn.innerHTML = submitLabel();
        confirmBox.hidden = true;
        form.querySelectorAll(".mm-add-only").forEach(function (node) {
            node.hidden = isEditing;
        });
    }

    function closeDeleteConfirm(focusBack) {
        if (!pendingDeleteRow) return;
        var row = pendingDeleteRow;
        row.classList.remove("confirming");
        var strip = row.querySelector("[data-confirm-strip]");
        if (strip) strip.hidden = true;
        pendingDeleteRow = null;

        if (focusBack) {
            var btn = row.querySelector("[data-delete]");
            if (btn) btn.focus();
        }
    }

    function enterEditMode(row, button) {
        if (inFlight) return;
        closeDeleteConfirm(false);

        if (mode === "edit" && !isDirty() && editingRow === row) {
            nameInput.focus();
            return;
        }

        if (mode === "edit" && isDirty() && editingRow !== row) {
            showMessage("ابتدا تغییرات غذای فعلی را ذخیره یا لغو کنید.", "error");
            formCard.scrollIntoView({ block: "start", behavior: "smooth" });
            return;
        }

        mode = "edit";
        editingId = row.getAttribute("data-id");
        editingRow = row;
        lastEditButton = button;

        nameInput.value = row.getAttribute("data-name") || "";
        priceInput.value = row.getAttribute("data-price") || "";
        stockInput.value = row.getAttribute("data-stock") || "0";
        descInput.value = row.getAttribute("data-description") || "";

        baseline = {
            name: normalizeForCompare(nameInput.value),
            price: normalizeForCompare(priceInput.value),
            stock: normalizeForCompare(stockInput.value),
            description: normalizeForCompare(descInput.value)
        };

        clearErrors();
        clearImage();
        messageBox.hidden = true;

        listBox.querySelectorAll(".mm-row.editing").forEach(function (node) {
            node.classList.remove("editing");
        });
        row.classList.add("editing");

        formTitleText.textContent = "ویرایش: " + (row.getAttribute("data-name") || "");
        setEditUi(true);

        formCard.scrollIntoView({
            block: "start",
            behavior: prefersReducedMotion() ? "auto" : "smooth"
        });
        nameInput.focus();
        nameInput.select();
    }

    function resetToAddMode(focusTarget) {
        mode = "add";
        editingId = null;
        baseline = null;

        if (editingRow) editingRow.classList.remove("editing");
        editingRow = null;

        form.reset();
        formTitleText.textContent = "افزودن غذای جدید";
        clearImage();
        clearErrors();
        setEditUi(false);
        submitBtn.innerHTML = submitLabel();

        if (focusTarget) focusTarget.focus();
        else nameInput.focus();
    }

    function cancelEdit(force) {
        if (inFlight) return;

        if (!force && isDirty()) {
            confirmBox.hidden = false;
            confirmBox.querySelector('[data-confirm="discard"]').focus();
            return;
        }

        var target = lastEditButton;
        lastEditButton = null;
        resetToAddMode(target);
    }

    cancelEditBtn.addEventListener("click", function () { cancelEdit(false); });

    confirmBox.addEventListener("click", function (event) {
        var action = event.target.getAttribute("data-confirm");
        if (!action) return;

        if (action === "discard") {
            var target = lastEditButton;
            lastEditButton = null;
            resetToAddMode(target);
        } else {
            confirmBox.hidden = true;
            nameInput.focus();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") return;

        if (pendingDeleteRow) {
            event.preventDefault();
            closeDeleteConfirm(true);
            return;
        }

        if (mode === "edit" && !confirmBox.hidden) {
            confirmBox.hidden = true;
            nameInput.focus();
        }
    });

    window.addEventListener("beforeunload", function (event) {
        if (mode === "edit" && isDirty()) {
            event.preventDefault();
            event.returnValue = "";
            return "";
        }
        return undefined;
    });

    /* ---------- row actions (delegated) ---------- */

    function openDeleteConfirm(row) {
        if (row.getAttribute("data-busy") === "1") return;

        closeDeleteConfirm(false);

        row.classList.add("confirming");
        var strip = row.querySelector("[data-confirm-strip]");
        if (strip) strip.hidden = false;
        pendingDeleteRow = row;

        var confirmBtn = row.querySelector("[data-confirm-delete]");
        if (confirmBtn) confirmBtn.focus();
    }

    function deleteFood(row) {
        if (!row || row.getAttribute("data-busy") === "1") return;

        var id = row.getAttribute("data-id");
        var name = row.getAttribute("data-name") || "این غذا";

        setRowBusy(row, true);

        fetch(FOODS_URL + id + "/delete/", {
            method: "DELETE",
            headers: { "X-CSRFToken": csrfToken() },
            credentials: "same-origin"
        })
            .then(readJson)
            .then(function (result) {
                var response = result.response;
                var data = result.data;

                if (response.status === 403) {
                    setRowBusy(row, false);
                    showAuthMessage();
                    return;
                }

                if (response.status === 404) {
                    closeDeleteConfirm(false);
                    removeRow(row);
                    showMessage("این غذا از قبل حذف شده بود.", "info");
                    return;
                }

                if (!response.ok || data.success !== true) {
                    setRowBusy(row, false);
                    showMessage(data.error || "حذف غذا انجام نشد. چیزی تغییر نکرد.", "error");
                    return;
                }

                if (Number(editingId) === Number(id)) {
                    lastEditButton = null;
                    resetToAddMode(null);
                    showMessage("«" + (data.name || name) + "» حذف شد؛ ویرایش آن لغو شد.", "info");
                } else {
                    showMessage("«" + (data.name || name) + "» حذف شد.", "success");
                }

                closeDeleteConfirm(false);
                removeRow(row);
            })
            .catch(function () {
                setRowBusy(row, false);
                showMessage("ارتباط با سرور برقرار نشد؛ چیزی حذف نشد.", "error");
            });
    }

    function toggleStock(row) {
        if (!row || row.getAttribute("data-busy") === "1") return;

        var id = row.getAttribute("data-id");
        var name = row.getAttribute("data-name") || "این غذا";
        var current = Number(row.getAttribute("data-stock") || 0);
        var next = current > 0 ? 0 : 1;

        setRowBusy(row, true);

        fetch(FOODS_URL + id + "/update/", {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken()
            },
            body: JSON.stringify({ stock: next }),
            credentials: "same-origin"
        })
            .then(readJson)
            .then(function (result) {
                var response = result.response;
                var data = result.data;

                if (response.status === 403) {
                    showAuthMessage();
                    return;
                }

                if (response.status === 404) {
                    removeRow(row);
                    showMessage("این غذا دیگر وجود ندارد.", "error");
                    return;
                }

                if (!response.ok || data.success !== true) {
                    showMessage(data.error || "تغییر وضعیت موجودی انجام نشد.", "error");
                    return;
                }

                var food = data.food || data;
                applyStock(row, food.stock);

                if (next > 0) {
                    showMessage(
                        "«" + name + "» موجود شد. موجودی روی " + food.stock +
                        " تنظیم شد؛ برای تعداد دقیق از «ویرایش» استفاده کنید.",
                        "success"
                    );
                } else {
                    showMessage("«" + name + "» ناموجود شد.", "success");
                }
            })
            .catch(function () {
                showMessage("ارتباط با سرور برقرار نشد؛ وضعیت قبلی حفظ شد.", "error");
            })
            .then(function () {
                setRowBusy(row, false);
            });
    }

    listBox.addEventListener("click", function (event) {
        var target = event.target;

        if (target.closest("[data-cancel-delete]")) {
            event.preventDefault();
            closeDeleteConfirm(true);
            return;
        }

        var confirmBtn = target.closest("[data-confirm-delete]");
        if (confirmBtn) {
            event.preventDefault();
            deleteFood(confirmBtn.closest(".mm-row"));
            return;
        }

        var delBtn = target.closest("[data-delete]");
        if (delBtn) {
            event.preventDefault();
            openDeleteConfirm(delBtn.closest(".mm-row"));
            return;
        }

        var activeSw = target.closest("[data-active-switch]");
        if (activeSw) {
            event.preventDefault();
            toggleActive(activeSw.closest(".mm-row"));
            return;
        }

        var sw = target.closest("[data-stock-switch]");
        if (sw) {
            event.preventDefault();
            toggleStock(sw.closest(".mm-row"));
            return;
        }

        var editBtn = target.closest("[data-edit]");
        if (editBtn) {
            event.preventDefault();
            enterEditMode(editBtn.closest(".mm-row"), editBtn);
        }
    });

    /* ---------- dish form submit ---------- */

    function setBusy(busy) {
        submitBtn.disabled = busy;
        cancelEditBtn.disabled = busy;

        if (busy) {
            submitBtn.setAttribute("aria-busy", "true");
            submitBtn.innerHTML = busyLabel();
        } else {
            submitBtn.removeAttribute("aria-busy");
            submitBtn.innerHTML = submitLabel();
        }

        listBox.querySelectorAll(".mm-row").forEach(function (row) {
            row.querySelectorAll("button").forEach(function (btn) {
                btn.disabled = busy;
            });
        });
    }

    function handleSuccess(data, isEdit) {
        var food = data.food || data;

        if (isEdit) {
            var row = updateFoodRow(food);
            var button = row ? row.querySelector(".mm-edit-btn") : null;

            showMessage("تغییرات «" + food.name + "» ذخیره شد.", "success");

            lastEditButton = button;
            resetToAddMode(button || null);
            if (row) {
                row.scrollIntoView({
                    block: "nearest",
                    behavior: prefersReducedMotion() ? "auto" : "smooth"
                });
            }
            return;
        }

        insertFoodRow(food);
        showMessage("«" + food.name + "» به منو اضافه شد.", "success");

        var keptCategory = categorySelect.value;
        form.reset();
        categorySelect.value = keptCategory;
        clearImage();
        clearErrors();
        nameInput.focus();
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (inFlight) return;

        clearErrors();
        messageBox.hidden = true;
        confirmBox.hidden = true;
        closeDeleteConfirm(false);

        var isEdit = mode === "edit" && editingId !== null;

        if (!validate(isEdit)) {
            showMessage("چند فیلد نیاز به اصلاح دارد؛ موارد مشخص‌شده را بررسی کنید.", "error");
            return;
        }

        inFlight = true;
        setBusy(true);

        var request;

        if (isEdit) {
            request = fetch(FOODS_URL + editingId + "/update/", {
                method: "PUT",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken()
                },
                body: JSON.stringify({
                    name: nameInput.value.trim(),
                    price: parsePositiveInt(priceInput.value),
                    stock: parsePositiveInt(stockInput.value),
                    description: descInput.value.trim()
                }),
                credentials: "same-origin"
            });
        } else {
            var payload = new FormData();
            payload.append("name", nameInput.value.trim());
            payload.append("category_id", categorySelect.value);
            payload.append("price", String(parsePositiveInt(priceInput.value)));
            payload.append("stock", String(parsePositiveInt(stockInput.value)));

            var description = descInput.value.trim();
            if (description) payload.append("description", description);
            if (imageInput.files[0]) payload.append("image", imageInput.files[0]);

            request = fetch(CREATE_URL, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken() },
                body: payload,
                credentials: "same-origin"
            });
        }

        request
            .then(readJson)
            .then(function (result) {
                var response = result.response;
                var data = result.data;

                if (response.status === 403) {
                    showAuthMessage();
                    return;
                }

                if (response.status === 404) {
                    showMessage("این غذا دیگر در منو وجود ندارد. صفحه را بازخوانی کنید.", "error");
                    return;
                }

                if (!response.ok || data.success !== true) {
                    if (!result.isJson && response.status >= 500) {
                        showMessage("خطای سرور؛ تغییرات ذخیره نشد. دوباره تلاش کنید.", "error");
                        return;
                    }

                    showMessage(
                        data.error || (isEdit
                            ? "ذخیره تغییرات انجام نشد. اطلاعات شما حفظ شد؛ دوباره تلاش کنید."
                            : "ثبت غذا انجام نشد. اطلاعات شما حفظ شد؛ دوباره تلاش کنید."),
                        "error"
                    );
                    return;
                }

                handleSuccess(data, isEdit);
            })
            .catch(function () {
                showMessage(
                    "ارتباط با سرور برقرار نشد. اطلاعات شما حفظ شد؛ دوباره تلاش کنید.",
                    "error"
                );
            })
            .then(function () {
                inFlight = false;
                setBusy(false);
            });
    });

    /* ---------- categories ---------- */

    function ensureCategoryOption(category) {
        var value = String(category.id);
        var option = categorySelect.querySelector('option[value="' + value + '"]');

        if (!option) {
            option = document.createElement("option");
            option.value = value;
            option.textContent = (category.icon ? category.icon + " " : "") + category.name;
            categorySelect.appendChild(option);
        }

        return option;
    }

    function appendCategoryRow(category) {
        var item = document.createElement("li");
        item.setAttribute("data-category-id", category.id);
        item.innerHTML =
            '<span class="mm-cat-name">' + esc(category.icon) + " " + esc(category.name) + "</span>" +
            '<span class="mm-count">0</span>';

        var empty = document.getElementById("mmCategoryEmpty");
        if (empty) empty.remove();

        catList.appendChild(item);

        if (catCount) catCount.textContent = String(catList.querySelectorAll("li").length);
    }

    catToggle.addEventListener("click", function () {
        catForm.hidden = false;
        catToggle.hidden = true;
        catName.focus();
    });

    catCancel.addEventListener("click", function () {
        catForm.hidden = true;
        catToggle.hidden = false;
        catForm.reset();
        errorNode("new_category", catForm).textContent = "";
    });

    catForm.addEventListener("submit", function (event) {
        event.preventDefault();

        errorNode("new_category", catForm).textContent = "";
        var name = catName.value.trim();

        if (!name) {
            errorNode("new_category", catForm).textContent = "نام دسته را وارد کنید.";
            catName.focus();
            return;
        }

        if (name.length > NAME_MAX) {
            errorNode("new_category", catForm).textContent =
                "نام دسته نباید بیشتر از " + NAME_MAX + " کاراکتر باشد.";
            return;
        }

        catSubmit.disabled = true;
        catSubmit.textContent = "در حال ثبت…";

        var payload = new FormData();
        payload.append("name", name);
        payload.append("icon", catIcon.value.trim());

        fetch(CATEGORY_URL, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken() },
            body: payload,
            credentials: "same-origin"
        })
            .then(readJson)
            .then(function (result) {
                var response = result.response;
                var data = result.data;

                if (response.status === 403) {
                    showAuthMessage();
                    return;
                }

                if (!response.ok || data.success !== true) {
                    errorNode("new_category", catForm).textContent =
                        data.error || "ثبت دسته انجام نشد.";
                    return;
                }

                var category = data.category;
                ensureCategoryOption(category);

                if (data.existing) {
                    showMessage("این دسته از قبل وجود داشت و انتخاب شد.", "info");
                } else {
                    appendCategoryRow(category);
                    showMessage("دستهٔ «" + category.name + "» ساخته شد.", "success");
                }

                categorySelect.value = String(category.id);
                catForm.reset();
                catForm.hidden = true;
                catToggle.hidden = false;
            })
            .catch(function () {
                errorNode("new_category", catForm).textContent =
                    "ارتباط با سرور برقرار نشد؛ دوباره تلاش کنید.";
            })
            .then(function () {
                catSubmit.disabled = false;
                catSubmit.textContent = "ثبت دسته";
            });
    });
})();
