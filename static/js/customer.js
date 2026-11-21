/*
==========================
LUMEN — Smart Menu
customer.js
==========================
API endpoints used:
  GET  /api/foods/
  POST /api/create_order/
  GET  /api/orders/<id>/          (نیازمند Header: X-Order-Token)
  POST /api/orders/<id>/confirm/  (نیازمند Header: X-Order-Token)
  GET  /api/seats/status/
  GET  /api/customer/check/
*/

// ==========================
// CSRF
// ==========================

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + "=")) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie("csrftoken");


// ==========================
// State
// ==========================

let menuData = [];
let cart = [];
let currentCategoryId = "all";
let currentSearch = "";
let selectedSeat = null;   // { tableIndex, chairIds: [...], tableLabel }
let foodsLoaded = false;

// QR Code: شماره میز از URL و وضعیت قفل
let initialTableNumber = null;  // شماره میز از QR Code
let isTableLocked = false;      // آیا میز قفل شده است؟
let tableSignature = null;      // امضای امنیتی QR Code
let isSubmittingOrder = false;  // جلوگیری از ارسال چندبارهٔ سفارش


// ==========================
// Body scroll lock (used while the auth / seat "gate" overlays are open)
// ==========================

let _scrollLockCount = 0;
function lockBodyScroll() {
    _scrollLockCount++;
    document.body.style.overflow = "hidden";
}
function unlockBodyScroll() {
    _scrollLockCount = Math.max(0, _scrollLockCount - 1);
    if (_scrollLockCount === 0) document.body.style.overflow = "";
}


// ==========================
// QR Code: خواندن پارامترهای URL
// ==========================

function getUrlParams() {
    const params = new URLSearchParams(window.location.search);
    return {
        table: params.get('table'),
        sig: params.get('sig')
    };
}

function initQRCodeTable() {
    const params = getUrlParams();

    // اولویت با پارامترهای URL است (اسکن تازهٔ QR).
    // اگر URL پارامتر نداشت (مثلاً بازگشت/رفرش بدون query)، ابتدا از
    // مقادیر تزریق‌شده توسط Backend (context) و سپس از sessionStorage
    // بازیابی می‌کنیم تا اطلاعات معتبر میز از بین نرود.
    let tableParam = params.table;
    let sigParam = params.sig;

    if (!tableParam && window.__LUMEN_TABLE__ !== null && window.__LUMEN_TABLE__ !== undefined) {
        tableParam = String(window.__LUMEN_TABLE__);
        sigParam = window.__LUMEN_TABLE_SIGNATURE__ || null;
    }

    if (!tableParam) {
        try {
            const saved = JSON.parse(sessionStorage.getItem("tableAccess") || "null");
            if (saved && saved.table) {
                tableParam = String(saved.table);
                sigParam = saved.sig || null;
            }
        } catch (e) { /* ignore */ }
    }

    if (tableParam) {
        initialTableNumber = parseInt(tableParam);
        tableSignature = sigParam || null;

        // اگر امضا وجود داشت، میز قفل می‌شود
        if (sigParam) {
            isTableLocked = true;
            console.log(`✅ QR Code معتبر: میز ${initialTableNumber} قفل شد`);
            // نگهداری برای رفرش/بازگشت
            try {
                sessionStorage.setItem("tableAccess", JSON.stringify({ table: initialTableNumber, sig: tableSignature }));
            } catch (e) { /* ignore */ }
        } else {
            console.warn(`⚠️ شماره میز ${initialTableNumber} بدون امضا - قفل نشد`);
        }
    }
}


// ==========================
// Start
// ==========================

window.onload = function () {
    // ابتدا پارامترهای QR Code را بررسی می‌کنیم
    initQRCodeTable();

    initLoader();
    initAuthGate();
    initMarquee();
    initMusicToggle();
    initSeatPicker();
    initTrackDrawer();
    initContinueBar();

    loadFoods();

    const savedCart = sessionStorage.getItem("cart");
    if (savedCart) cart = JSON.parse(savedCart);

    const savedSeat = sessionStorage.getItem("selectedSeat");
    if (savedSeat) {
        selectedSeat = JSON.parse(savedSeat);
        applySeatSelectionUI();
    }

    renderCart();
    setupCartDrawer();
    setupSearch();
    checkForActiveOrder();
};


// ==========================================================
// LOADER  (progress ring + particles + optional 3D shape)
// ==========================================================

let loaderProgress = 0;
let loaderDone = false;

function initLoader() {
    spawnLoaderParticles();
    start3DLoaderShape();

    const fakeTimer = setInterval(function () {
        if (loaderProgress >= 92 || loaderDone) {
            clearInterval(fakeTimer);
            return;
        }
        loaderProgress += Math.random() * 9 + 3;
        if (loaderProgress > 92) loaderProgress = 92;
        setLoaderProgress(loaderProgress);
    }, 180);

    const enterBtn = document.getElementById("loaderEnter");
    if (enterBtn) {
        enterBtn.addEventListener("click", function () {
            hideLoader();
        });
    }
}

function setLoaderProgress(pct) {
    pct = Math.max(0, Math.min(100, Math.round(pct)));
    const ring = document.getElementById("loaderRingFill");
    const label = document.getElementById("loaderPct");
    if (ring) {
        const circumference = 565;
        ring.style.strokeDashoffset = circumference - (circumference * pct / 100);
    }
    if (label) label.textContent = pct + "%";
}

function finishLoaderProgress() {
    loaderDone = true;
    loaderProgress = 100;
    setLoaderProgress(100);
    const enterBtn = document.getElementById("loaderEnter");
    if (enterBtn) enterBtn.classList.add("ready");
    setTimeout(hideLoader, 900);
}

function hideLoader() {
    const loader = document.getElementById("pageLoader");
    if (!loader || loader.classList.contains("hidden")) return;
    loader.classList.add("hidden");
    stop3DLoaderShape();

    // Onboarding gate, in order:
    // 1) if we don't know who's ordering yet -> ask for name/phone
    // 2) once we do (or already did) -> make sure a seat is chosen
    //    before letting them loose on the menu.
    const savedUser = localStorage.getItem("lumenUser");
    if (!savedUser) {
        openAuthOverlay();
    } else {
        maybeOpenSeatGate();
    }
}

function spawnLoaderParticles() {
    const box = document.getElementById("loaderParticles");
    if (!box) return;
    const count = 22;
    for (let i = 0; i < count; i++) {
        const p = document.createElement("span");
        const size = Math.random() * 4 + 2;
        p.style.width = size + "px";
        p.style.height = size + "px";
        p.style.left = Math.random() * 100 + "%";
        p.style.bottom = "0px";
        p.style.animationDuration = (Math.random() * 3 + 3) + "s";
        p.style.animationDelay = (Math.random() * 3) + "s";
        box.appendChild(p);
    }
}

let _loaderRenderer = null;
let _loaderAnimId = null;

function start3DLoaderShape() {
    const canvas = document.getElementById("loader3d");
    if (!canvas || typeof THREE === "undefined") return;

    try {
        const size = canvas.clientWidth || 150;
        const renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
        renderer.setSize(size, size, false);
        _loaderRenderer = renderer;

        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
        camera.position.z = 4.2;

        const geo = new THREE.IcosahedronGeometry(1.4, 0);
        const wire = new THREE.WireframeGeometry(geo);
        const mat = new THREE.LineBasicMaterial({ color: 0xc9a227, transparent: true, opacity: 0.85 });
        const shape = new THREE.LineSegments(wire, mat);
        scene.add(shape);

        function animate() {
            shape.rotation.x += 0.006;
            shape.rotation.y += 0.009;
            renderer.render(scene, camera);
            _loaderAnimId = requestAnimationFrame(animate);
        }
        animate();
    } catch (e) {
        console.warn("Loader 3D shape skipped:", e);
    }
}

function stop3DLoaderShape() {
    if (_loaderAnimId) cancelAnimationFrame(_loaderAnimId);
    if (_loaderRenderer) {
        _loaderRenderer.dispose();
        _loaderRenderer = null;
    }
}


// ==========================================================
// AUTH GATE (name for the invoice + optional phone, so returning
// customers on the same device/phone number don't have to retype)
// ==========================================================

const CUSTOMER_CHECK_URL = "/api/customer/check/";
let phoneCheckTimer = null;

function initAuthGate() {
    const overlay = document.getElementById("authOverlay");
    const form = document.getElementById("authForm");
    if (!overlay) return;

    // Same-device recognition: localStorage survives across sessions
    // (unlike sessionStorage), so a returning visitor on this same
    // browser never sees the name/phone form again — they go straight
    // to the seat gate instead (handled in hideLoader()).
    const saved = localStorage.getItem("lumenUser");
    if (saved) {
        try {
            applyUserIdentity(JSON.parse(saved));
        } catch (e) { /* fall through to showing the form */ }
    }

    if (form) {
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            const nameInput = document.getElementById("authName");
            const phoneInput = document.getElementById("authPhone");
            const name = nameInput ? nameInput.value.trim() : "";
            const phone = phoneInput ? phoneInput.value.trim() : "";

            if (!name) {
                showToast("لطفاً اسمت رو وارد کن.", true);
                return;
            }

            const user = { name: name, phone: phone || null };
            localStorage.setItem("lumenUser", JSON.stringify(user));
            applyUserIdentity(user);
            closeAuthOverlay();

            // Step 2 of onboarding: pick a seat before the menu.
            maybeOpenSeatGate();
        });
    }

    // Cross-device / cache-cleared recognition: if they type a phone
    // number that matches a previous order, prefill their name from
    // the database so they don't have to type it again either.
    const phoneField = document.getElementById("authPhone");
    if (phoneField) {
        phoneField.addEventListener("input", function () {
            const phone = phoneField.value.trim();
            clearTimeout(phoneCheckTimer);
            if (phone.length < 10) return;
            phoneCheckTimer = setTimeout(function () { checkKnownPhone(phone); }, 500);
        });
    }
}

async function checkKnownPhone(phone) {
    try {
        const res = await fetch(CUSTOMER_CHECK_URL + "?phone=" + encodeURIComponent(phone));
        if (!res.ok) return;
        const data = await res.json();
        if (data.found && data.name) {
            const nameInput = document.getElementById("authName");
            if (nameInput && !nameInput.value.trim()) {
                nameInput.value = data.name;
                showToast("خوش برگشتی، " + data.name + "!", false);
            }
        }
    } catch (e) {
        // silent — this is just a convenience prefill, never blocking
    }
}

function openAuthOverlay() {
    const overlay = document.getElementById("authOverlay");
    if (overlay) { overlay.classList.add("open"); lockBodyScroll(); }
    const nameInput = document.getElementById("authName");
    if (nameInput) setTimeout(function () { nameInput.focus(); }, 300);
}

function closeAuthOverlay() {
    const overlay = document.getElementById("authOverlay");
    if (overlay && overlay.classList.contains("open")) {
        overlay.classList.remove("open");
        unlockBodyScroll();
    }
}

function applyUserIdentity(user) {
    const chip = document.getElementById("userChip");
    const label = document.getElementById("userChipLabel");
    if (label) label.textContent = user.name;
    if (chip) chip.classList.add("chosen");
}

function getCurrentUser() {
    try {
        return JSON.parse(localStorage.getItem("lumenUser") || "null");
    } catch (e) {
        return null;
    }
}

function getCurrentUserName() {
    const u = getCurrentUser();
    return u ? u.name : null;
}

// Step 2 of onboarding — force a seat choice before the menu is usable.
// Only triggers once per browser tab session (selectedSeat is remembered
// in sessionStorage), so reloading mid-visit doesn't re-prompt.
function maybeOpenSeatGate() {
    if (selectedSeat) return;
    openSeatModal();
}


// ==========================
// Marquee
// ==========================

function initMarquee() {
    const track = document.getElementById("marqueeTrack");
    if (!track) return;

    const phrases = [
        "مواد اولیه تازه",
        "سرو سریع",
        "پیگیری زنده سفارش",
        "کیفیت بدون مصالحه",
        "پرداخت امن"
    ];

    let html = "";
    for (let rep = 0; rep < 2; rep++) {
        phrases.forEach(function (text) {
            html += `<span>${text}<span class="sep">✦</span></span>`;
        });
    }
    track.innerHTML = html;
}


// ==========================
// Background music toggle
// ==========================

const AUDIO_SRC = "/static/audio/ambient.mp3"; // update to your actual track
let bgAudio = null;
let musicPlaying = false;

function initMusicToggle() {
    const btn = document.getElementById("musicToggle");

    bgAudio = new Audio(AUDIO_SRC);
    bgAudio.loop = true;
    bgAudio.volume = 0.4;

    function toggleMusic() {
        if (!bgAudio) return;
        if (musicPlaying) {
            bgAudio.pause();
            musicPlaying = false;
        } else {
            bgAudio.play().catch(function (err) {
                console.warn("Background music could not start:", err);
            });
            musicPlaying = true;
        }
        updateMusicIcon();
    }

    if (btn) btn.addEventListener("click", toggleMusic);
}

function updateMusicIcon() {
    const el = document.getElementById("musicToggle");
    if (!el) return;
    const icon = el.querySelector("i");
    el.classList.toggle("active", musicPlaying);
    if (icon) icon.className = musicPlaying ? "bi bi-volume-up-fill" : "bi bi-volume-mute-fill";
}


// ==========================
// Stock Helper
// ==========================

function isInactive(food) {
    return !!food && food.is_active === false;
}

function isSoldOut(food) {
    if (!food) return false;
    if (food.available === false) return true;
    if (food.is_available === false) return true;
    if (food.in_stock === false) return true;
    if (food.sold_out === true) return true;
    if (typeof food.stock === "number" && food.stock <= 0) return true;
    return false;
}


// ==========================
// Load Foods
// ==========================

async function loadFoods() {
    try {
        const response = await fetch("/api/foods/");
        menuData = await response.json();

        foodsLoaded = true;
        finishLoaderProgress();

        if (!menuData || menuData.length === 0) {
            showEmptyState();
            return;
        }

        renderCategories();
        renderFoods(menuData);

    } catch (error) {
        console.error("Load Foods Error:", error);
        foodsLoaded = true;
        finishLoaderProgress();
        showEmptyState();
    }
}


// ==========================
// Empty State
// ==========================

function showEmptyState() {
    const container = document.getElementById("menuContainer");
    if (!container) return;
    container.innerHTML = `
    <div class="empty-state">
        <p class="title">چیزی پیدا نشد</p>
        <p>آشپزخانه مشغول کار است، کمی بعد دوباره سر بزن.</p>
    </div>
    `;
}


// ==========================
// Categories
// ==========================

function renderCategories() {
    const box = document.getElementById("categoryContainer");
    if (!box) return;

    box.innerHTML = "";

    const allTab = document.createElement("div");
    allTab.className = "cat-chip active";
    allTab.dataset.categoryId = "all";
    allTab.innerHTML = `<div>✦</div>همه`;
    allTab.addEventListener("click", function () {
        currentCategoryId = "all";
        setActiveCategory(allTab);
        applyFilters();
    });
    box.appendChild(allTab);

    menuData.forEach(function (cat) {
        const item = document.createElement("div");
        item.className = "cat-chip";
        item.dataset.categoryId = cat.id;
        item.innerHTML = `<div>${cat.icon || ""}</div>${cat.name}`;
        item.addEventListener("click", function () {
            currentCategoryId = cat.id;
            setActiveCategory(item);
            applyFilters();
        });
        box.appendChild(item);
    });
}

function setActiveCategory(activeEl) {
    document.querySelectorAll("#categoryContainer .cat-chip").forEach(function (el) {
        el.classList.remove("active");
    });
    activeEl.classList.add("active");
}


// ==========================
// Filters (Category + Search)
// ==========================

function getAllFoodsFlat() {
    return menuData.flatMap(cat => (cat.foods || []).map(f => Object.assign({}, f, { category: cat.name, category_id: cat.id })));
}

function applyFilters() {
    let foods = getAllFoodsFlat();

    if (currentCategoryId !== "all") {
        foods = foods.filter(f => f.category_id === currentCategoryId);
    }
    if (currentSearch.trim() !== "") {
        const q = currentSearch.trim();
        foods = foods.filter(f => (f.name || "").indexOf(q) !== -1);
    }

    renderFoods([{ foods: foods }]);
}


// ==========================
// Search
// ==========================

function setupSearch() {
    const input = document.getElementById("searchInput");
    if (!input) return;
    input.addEventListener("input", function () {
        currentSearch = input.value;
        applyFilters();
    });
}


// ==========================
// Render Foods
// ==========================

function renderFoods(data) {
    const container = document.getElementById("menuContainer");
    if (!container) return;

    container.innerHTML = "";
    let totalCount = 0;

    data.forEach(function (cat) {
        (cat.foods || []).forEach(function (food) {
            totalCount++;

            const soldOut = isSoldOut(food);
            const inactive = isInactive(food);
            const blocked = soldOut || inactive;
            const imgSrc = food.image || "/static/images/no-food.png";

            const row = document.createElement("div");
            row.className = "menu-row glass" +
                (soldOut ? " sold-out" : "") +
                (inactive ? " unavailable" : "");
            row.dataset.foodId = food.id;

            const badge = inactive
                ? '<span class="badge off">امروز ارائه نمی‌شود</span>'
                : (soldOut ? '<span class="badge out">ناموجود</span>' : '');

            row.innerHTML = `
                <div class="thumb">
                    <img src="${imgSrc}" onerror="this.src='/static/images/no-food.png'" alt="${food.name}">
                    ${badge}
                </div>
                <div class="body">
                    <h4>${food.name}</h4>
                    <div class="stat-line"><i class="bi bi-tag-fill"></i> ${food.category || ""}</div>
                    <p class="desc">${food.description || ""}</p>
                    <div class="price-chip"><span class="mono-num">${Number(food.price).toLocaleString()}</span><span class="unit">تومان</span></div>
                </div>
                <div class="side">
                    <button class="add-btn" ${blocked ? "disabled" : ""}
                            aria-label="${blocked ? (inactive ? `${food.name} امروز ارائه نمی‌شود` : `${food.name} ناموجود است`) : `افزودن ${food.name}`}">
                        <i class="bi bi-plus-lg"></i>
                    </button>
                </div>
            `;

            if (!blocked) {
                const btn = row.querySelector(".add-btn");
                btn.addEventListener("click", function () {
                    addFood(food.id, food.name, food.price, btn);
                });
            }

            container.appendChild(row);
        });
    });

    if (totalCount === 0) {
        showEmptyState();
    } else {
        observeReveal();
    }
}

function observeReveal() {
    const rows = document.querySelectorAll(".menu-row:not(.reveal)");
    if (!("IntersectionObserver" in window)) {
        rows.forEach(c => c.classList.add("reveal"));
        return;
    }
    const io = new IntersectionObserver(function (entries, obs) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) {
                entry.target.classList.add("reveal");
                obs.unobserve(entry.target);
            }
        });
    }, { threshold: 0.12 });
    rows.forEach(c => io.observe(c));
}


// ==========================
// Cart
// ==========================

function saveCart() {
    sessionStorage.setItem("cart", JSON.stringify(cart));
}

function addFood(id, name, price, sourceBtn) {
    let item = cart.find(food => food.id === id);
    if (item) {
        item.qty++;
    } else {
        cart.push({ id: id, name: name, price: Number(price), qty: 1 });
    }

    saveCart();
    renderCart();

    if (sourceBtn) {
        const fly = document.createElement("span");
        fly.className = "fly-plus";
        fly.textContent = "+1";
        sourceBtn.appendChild(fly);
        setTimeout(function () { fly.remove(); }, 700);
    }
}

function increase(index) {
    cart[index].qty++;
    saveCart();
    renderCart();
}

function decrease(index) {
    cart[index].qty--;
    if (cart[index].qty <= 0) cart.splice(index, 1);
    saveCart();
    renderCart();
}

function removeItem(index) {
    cart.splice(index, 1);
    saveCart();
    renderCart();
}

function renderCart() {
    const cartItems = document.getElementById("cartItems");
    const totalPrice = document.getElementById("totalPrice");
    const orderBtn = document.getElementById("submitOrderBtn");
    const seatMsg = document.getElementById("seatRequiredMsg");
    if (!cartItems || !totalPrice) return;

    cartItems.innerHTML = "";

    if (cart.length === 0) {
        cartItems.innerHTML = `<div class="empty-cart"><i class="bi bi-basket3" style="font-size:1.8rem;display:block;margin-bottom:10px;"></i>سبد خرید خالی است</div>`;
        totalPrice.innerHTML = "0";
        if (orderBtn) orderBtn.disabled = true;
        if (seatMsg) seatMsg.classList.remove("show");
        updateHeaderCart(0);
        updateContinueBar(0, 0);
        return;
    }

    let total = 0;
    let count = 0;

    cart.forEach(function (item, index) {
        total += item.price * item.qty;
        count += item.qty;

        cartItems.innerHTML += `
        <div class="cart-line">
            <div class="info">
                <h5>${item.name}</h5>
                <span class="mono-num">${item.price.toLocaleString()} تومان</span>
            </div>
            <div class="qty-stepper">
                <button onclick="decrease(${index})">-</button>
                <span>${item.qty}</span>
                <button onclick="increase(${index})">+</button>
            </div>
            <button class="remove" onclick="removeItem(${index})"><i class="bi bi-trash"></i></button>
        </div>
        `;
    });

    totalPrice.innerHTML = total.toLocaleString();

    if (seatMsg) seatMsg.classList.toggle("show", !selectedSeat);
    if (orderBtn) orderBtn.disabled = !selectedSeat;

    updateHeaderCart(count);
    updateContinueBar(count, total);
}

function updateHeaderCart(count) {
    const badge = document.getElementById("headerCartCount");
    if (!badge) return;
    badge.textContent = count;
    badge.classList.add("bump");
    setTimeout(function () { badge.classList.remove("bump"); }, 250);
}


// ==========================================================
// BOTTOM "CONTINUE TO CART" BAR
// ==========================================================

function initContinueBar() {
    const btn = document.getElementById("continueBtn");
    if (btn) btn.addEventListener("click", openCartDrawer);
}

function updateContinueBar(count, total) {
    const bar = document.getElementById("continueBar");
    if (!bar) return;
    if (count > 0) {
        bar.classList.add("show");
        const countEl = document.getElementById("continueCount");
        const totalEl = document.getElementById("continueTotal");
        if (countEl) countEl.textContent = count + " آیتم";
        if (totalEl) totalEl.textContent = total.toLocaleString() + " تومان";
    } else {
        bar.classList.remove("show");
    }
}


// ==========================================================
// CART DRAWER
// ==========================================================

function openCartDrawer() {
    const drawer = document.getElementById("cartDrawer");
    const overlay = document.getElementById("drawerOverlay");
    if (drawer) drawer.classList.add("open");
    if (overlay) overlay.classList.add("open");
}

function closeCartDrawer() {
    const drawer = document.getElementById("cartDrawer");
    const overlay = document.getElementById("drawerOverlay");
    if (drawer) drawer.classList.remove("open");
    if (overlay) overlay.classList.remove("open");
}

function setupCartDrawer() {
    const overlay = document.getElementById("drawerOverlay");
    const openBtns = document.querySelectorAll("[data-open-cart]");
    const closeBtn = document.getElementById("drawerClose");
    const submitBtn = document.getElementById("submitOrderBtn");

    openBtns.forEach(btn => btn.addEventListener("click", openCartDrawer));
    if (closeBtn) closeBtn.addEventListener("click", closeCartDrawer);
    if (overlay) overlay.addEventListener("click", closeCartDrawer);
    if (submitBtn) submitBtn.addEventListener("click", submitOrder);
}

// ==========================================================
// IDEMPOTENCY — یک کلید برای هر عملیات منطقی ثبت سفارش
// ==========================================================

const ORDER_SUBMIT_STATE = { idle: "idle", submitting: "submitting", success: "success", error: "error" };
let orderSubmitState = ORDER_SUBMIT_STATE.idle;
let pendingOrderSubmission = null;   // { key, fingerprint } — برای Retry همان کلید می‌ماند

function generateIdempotencyKey() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
    }
    const bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
        window.crypto.getRandomValues(bytes);
    } else {
        for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
    return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}

// اثر انگشت سمت کلاینت فقط برای تشخیص «همان عملیات» در Retry است؛
// دادهٔ امنیتی نیست و Backend اثر انگشت معتبر خودش را می‌سازد.
function orderRequestFingerprint(payload) {
    const items = (payload.foods || [])
        .map(f => [Number(f.id), Number(f.qty)])
        .sort((a, b) => a[0] - b[0]);
    return JSON.stringify({
        table: String(payload.table),
        seats: (payload.seats || []).slice().sort(),
        items: items
    });
}

function setOrderSubmitState(state) {
    orderSubmitState = state;
    isSubmittingOrder = (state === ORDER_SUBMIT_STATE.submitting);
    const btn = document.getElementById("submitOrderBtn");
    if (btn) btn.dataset.state = state;
}

async function submitOrder() {
    // هر لحظه فقط یک درخواست؛ کلیک دوم تا پایان پاسخ نادیده گرفته می‌شود.
    if (orderSubmitState === ORDER_SUBMIT_STATE.submitting) return;

    if (!selectedSeat) {
        const seatMsg = document.getElementById("seatRequiredMsg");
        if (seatMsg) seatMsg.classList.add("show");
        showToast("ابتدا میز خود را انتخاب کنید.", true);
        openSeatModal();
        return;
    }
    if (cart.length === 0) {
        showToast("سبد خرید خالی است.", true);
        return;
    }

    // 🔐 اعتبارسنجی امنیتی میز در سمت Frontend (اعتبارسنجی نهایی در Backend است)
    // سفارش فقط برای میزی که با QR Code معتبر قفل شده است امکان‌پذیر است.
    if (!isTableLocked || initialTableNumber === null) {
        showToast("برای ثبت سفارش باید QR Code روی میز خود را اسکن کنید.", true);
        return;
    }
    if (selectedSeat.tableIndex !== initialTableNumber) {
        showToast("⚠️ میز انتخاب‌شده با میز QR Code مطابقت ندارد. لطفاً دوباره تلاش کنید.", true);
        return;
    }
    if (!tableSignature) {
        showToast("امضای امنیتی میز موجود نیست. لطفاً QR Code را دوباره اسکن کنید.", true);
        return;
    }

    const currentUser = getCurrentUser();
    const order = {
        table: selectedSeat.tableIndex,
        table_signature: tableSignature,
        seats: selectedSeat.chairIds,
        name: currentUser ? currentUser.name : undefined,
        phone: currentUser ? currentUser.phone : undefined,
        foods: cart
    };

    // کلید Idempotency: تا وقتی خودِ عملیات (میز/صندلی/اقلام) عوض نشده،
    // برای Retry همان کلید حفظ می‌شود تا سفارش تکراری ساخته نشود.
    const fingerprint = orderRequestFingerprint(order);
    if (!pendingOrderSubmission || pendingOrderSubmission.fingerprint !== fingerprint) {
        pendingOrderSubmission = { key: generateIdempotencyKey(), fingerprint: fingerprint };
    }
    order.idempotency_key = pendingOrderSubmission.key;

    setOrderSubmitState(ORDER_SUBMIT_STATE.submitting);
    const submitBtn = document.getElementById("submitOrderBtn");
    if (submitBtn) submitBtn.disabled = true;

    try {
        const response = await fetch("/api/create_order/", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrftoken
            },
            body: JSON.stringify(order)
        });

        const data = await response.json();

        if (!response.ok) {
            setOrderSubmitState(ORDER_SUBMIT_STATE.error);
            showToast(data.error || "خطا در ثبت سفارش", true);
            return;
        }

        if (data.success) {
            const orderId = data.order_id;

            // اگر سرور موفقیت اعلام کرد اما شناسهٔ سفارش برنگرداند، پیام
            // موفقیت نمایش نمی‌دهیم تا مشتری شمارهٔ نامعتبر نبیند.
            if (orderId === undefined || orderId === null || orderId === "") {
                setOrderSubmitState(ORDER_SUBMIT_STATE.error);
                showToast("سفارش ثبت شد اما شمارهٔ سفارش دریافت نشد. لطفاً با پشتیبانی تماس بگیرید.", true);
                return;
            }

            // 🔐 اعتبارنامهٔ پیگیری: فقط در پاسخ ثبت سفارش برمی‌گردد و
            // همراه همین سفارش در sessionStorage نگهداری می‌شود.
            const trackingToken = (typeof data.tracking_token === "string")
                ? data.tracking_token
                : null;

            if (!trackingToken) {
                showToast("سفارش ثبت شد اما کلید پیگیری دریافت نشد. لطفاً صفحه را رفرش نکنید و با پشتیبانی تماس بگیرید.", true);
            }

            // عملیات با موفقیت ثبت شد → کلید مصرف شد و عملیات بعدی کلید نو می‌گیرد
            pendingOrderSubmission = null;
            setOrderSubmitState(ORDER_SUBMIT_STATE.success);

            cart = [];
            saveCart();
            renderCart();

            const tableLabel = selectedSeat.tableLabel;
            const newOrder = {
                id: orderId,
                tableLabel: tableLabel,
                createdAt: Date.now(),
                token: trackingToken
            };
            addOrderToHistory(newOrder);

            closeCartDrawer();

            showToast(`سفارش شما با موفقیت ثبت شد — شمارهٔ سفارش: ${orderId}`, false);

            const fab = document.getElementById("trackFab");
            const ping = document.getElementById("trackPing");
            if (fab) fab.classList.add("show");
            if (ping) ping.style.display = "block";

            openTrackDetail(newOrder);
        } else {
            setOrderSubmitState(ORDER_SUBMIT_STATE.error);
            showToast(data.error || "خطا در ثبت سفارش", true);
        }

    } catch (error) {
        console.error("Submit Order Error:", error);
        setOrderSubmitState(ORDER_SUBMIT_STATE.error);
        showToast("خطا در ارتباط با سرور — با همین شمارهٔ سفارش دوباره تلاش کنید.", true);
    } finally {
        // خطا/شبکه: کلید برای تلاش مجدد حفظ ماند؛ وضعیت دکمه آزاد می‌شود
        if (orderSubmitState === ORDER_SUBMIT_STATE.submitting) {
            setOrderSubmitState(ORDER_SUBMIT_STATE.idle);
        }
        // بازگرداندن وضعیت دکمهٔ ثبت بر اساس سبد/میز
        renderCart();
    }
}


// ==========================================================
// SEAT / TABLE PICKER — دو مرحله: انتخاب میز (۲×۲) → انتخاب صندلی (چند رقمรอบ میز)
// ==========================================================

const SEATS_STATUS_URL = "/api/seats/status/";

// ۴ میز — هر میز ۴ صندلی دارد: بالا، راست، پایین، چپ
const TABLES = [
    { number: 1 },
    { number: 2 },
    { number: 3 },
    { number: 4 }
];

// ۴ جهت برای صندلی هر میز
const CHAIR_DIRECTIONS = ["top", "right", "bottom", "left"];

// ---- وضعیت انتخاب (در حین باز نشدن منو) ----
let pendingTableNumber = null;   // شماره میز انتخاب‌شده در مرحله ۱
let pendingChairIds = [];         // کلیدهای صندلی انتخاب‌شده، مثلاً ["1-top","1-right"]
let occupiedChairs = new Set();   // از بک‌اند پر می‌شود (مثلاً ["1-top","1-right"])

// کش عناصر DOM
let tableCardElements = {};
let chairElements = {};
let chairStageBuilt = false;

// دریافت وضعیت اشغال صندلی‌ها از سرور
async function fetchSeatOccupancy() {
    try {
        const res = await fetch(SEATS_STATUS_URL);
        if (!res.ok) throw new Error("bad response");
        const data = await res.json();
        occupiedChairs = new Set(data.occupied || []);
    } catch (e) {
        console.error("Seat status fetch failed:", e);
        occupiedChairs = new Set(); // fail open
    }
}

// مرحله ۱: ساخت کارت‌های میز (شبکه ۲×۲)
function buildTableCards() {
    const grid = document.getElementById("seatTableGrid");
    if (!grid) return;
    grid.innerHTML = "";
    tableCardElements = {};

    TABLES.forEach(function (table) {
        const card = document.createElement("div");
        card.className = "seat-table-card";
        card.dataset.tableNumber = table.number;
        card.dataset.tableIndex = table.number;

        card.innerHTML = `
            <div class="seat-table-icon"><i class="bi bi-table"></i></div>
            <div class="seat-table-number">${table.number}</div>
            <div class="seat-table-label-small">میز ${table.number} — ۴ صندلی</div>
        `;

        card.addEventListener("click", function () {
            handleTableClick(table.number);
        });

        tableCardElements[table.number] = card;
        grid.appendChild(card);
    });
}

// کاربر میز را انتخاب کرد — به مرحله ۲ برو
function handleTableClick(tableNumber) {
    // اگر میز از QR قفل شده، فقط همان میز قابل انتخاب است
    if (isTableLocked && initialTableNumber !== null && tableNumber !== initialTableNumber) {
        showToast("⚠️ میز شما از QR قفل شده است. فقط می‌توانید میز " + initialTableNumber + " را انتخاب کنید.", "warning");
        return;
    }

    pendingTableNumber = tableNumber;
    pendingChairIds = [];

    // کارت انتخاب‌شده را مشخص کن
    Object.keys(tableCardElements).forEach(function (n) {
        tableCardElements[n].classList.toggle("selected", Number(n) === tableNumber);
    });

    // مرحله ۲ را نشان بده (میز بزرگ + صندلی‌ها)
    document.getElementById("seatPhaseTables").style.display = "none";
    document.getElementById("seatPhaseChairs").style.display = "block";

    // شماره میز را در هدینگ fase ۲ بنویس
    document.getElementById("chairPhaseTableNum").textContent = tableNumber;
    document.getElementById("chairTableLabel").textContent = "میز " + tableNumber;

    // صندلی‌ها را دور میز بزرگ بساز (یک‌بار)
    buildChairStage(tableNumber);
    updateChairState(tableNumber);

    // خلاصه انتخاب را، به‌روز کن
    resetSeatSummary();
    updateSeatSummary();

    // اگر میز قفل شده، غیرفعالࠗ کارت‌های دیگر
    updateTableCardStates();
}

// کارت‌های میز دیگر را غیرفعال کن
function updateTableCardStates() {
    TABLES.forEach(function (table) {
        const card = tableCardElements[table.number];
        if (!card) return;

        // اگر QR قفل شده، فقط میز قفل‌شده نمایش داده می‌شود و بقیه-locked هستندـ
        if (isTableLocked && initialTableNumber !== null && table.number !== initialTableNumber) {
            card.classList.add("locked");
            card.classList.remove("selected");
            return;
        }

        // اگر میز دیگری التأیید نشده ولی هم‌ภาษا انتخاب نشده، غیرفعال نیست
        if (pendingTableNumber !== null && table.number !== pendingTableNumber) {
            card.classList.add("locked");
            card.classList.remove("selected");
        } else {
            card.classList.remove("locked");
            if (table.number === pendingTableNumber && pendingChairIds.length > 0) {
                // اگر میز انتخاب شده و صندلی هم انتخاب شده
                card.classList.add("selected");
            }
        }
    });
}

// مرحله ۲: ساخت میز بزرگ + ۴ صندلی دورش
function buildChairStage(tableNumber) {
    if (chairStageBuilt) return;
    chairStageBuilt = true;

    const top = document.getElementById("chairTop");
    const right = document.getElementById("chairRight");
    const bottom = document.getElementById("chairBottom");
    const left = document.getElementById("chairLeft");

    [top, right, bottom, left].forEach(function (el) {
        if (!el) return;

        el.innerHTML = "";
        const dir = el.dataset.chair;
        const chairKey = tableNumber + "-" + dir;
        el.dataset.chairKey = chairKey;
        el.dataset.tableNumber = tableNumber;

        // برچسب جهت صندلی را درون آیکون بنویس
        el.innerHTML = `
            <span class="chair-label">${dir === "top" ? "بالا" : dir === "right" ? "راست" : dir === "bottom" ? "پایین" : "چپ"}</span>
        `;

        el.addEventListener("click", function () {
            handleChairClick(chairKey);
        });

        chairElements[chairKey] = el;
    });

    // اگر QR قفل شده، کارت‌های دیگر غیرفعال کن
    updateTableCardStates();
}

// وضعیت صندلی‌ها را آپدیت کن
function updateChairState(tableNumber) {
    CHAIR_DIRECTIONS.forEach(function (dir) {
        const chairKey = tableNumber + "-" + dir;
        const el = chairElements[chairKey];
        if (!el) return;

        const isOccupied = occupiedChairs.has(chairKey);
        const isSelected = pendingChairIds.indexOf(chairKey) !== -1;

        el.classList.toggle("occupied", isOccupied);
        el.classList.toggle("free", !isOccupied);
        el.classList.toggle("selected", isSelected);
    });
}

// کاربر روی صندلی کلیک کرد
function handleChairClick(chairKey) {
    // صندلی مشغول است — انتخاب نمی‌شود
    if (occupiedChairs.has(chairKey)) {
        showToast("این صندلی مشغول است.", "warning");
        return;
    }

    // میز قفل است و این صندلی متعلق به میز دیگر است
    const parts = chairKey.split("-");
    const chairTable = Number(parts[0]);
    if (isTableLocked && initialTableNumber !== null && chairTable !== initialTableNumber) {
        showToast("⚠️ میز شما قفل شده است. فقط صندلی‌های میز " + initialTableNumber + " را انتخاب کنید.", "warning");
        return;
    }

    toggleChair(chairKey);
}

// صندلی را اضافه یا حذف از انتخاب
function toggleChair(chairKey) {
    const idx = pendingChairIds.indexOf(chairKey);

    if (idx !== -1) {
        pendingChairIds.splice(idx, 1);
        chairElements[chairKey].classList.remove("selected");
    } else {
        // اولین صندلی انتخاب شد: فقط صندلی‌های همین میز قابل انتخاب می‌باشند
        pendingChairIds.push(chairKey);
        chairElements[chairKey].classList.add("selected");
    }

    updateSeatSummary();
}

// خلاصه انتخاب را نمایش بده
function resetSeatSummary() {
    const title = document.getElementById("seatSummaryTitle");
    const sub = document.getElementById("seatSummarySub");
    const dots = document.getElementById("seatSummaryDots");
    const confirmBtn = document.getElementById("seatConfirmBtn");

    if (title) title.textContent = "هنوز میزی انتخاب نشده";
    if (sub) {
        if (pendingTableNumber !== null) {
            sub.textContent = "می‌تونی صندلی‌های میز " + pendingTableNumber + " را انتخاب کنی یا برگردی";
        } else {
            sub.textContent = "برای شروع، یکی از میزهای بالا را انتخاب کن";
        }
    }
    if (dots) dots.innerHTML = "";
    if (confirmBtn) confirmBtn.disabled = true;
}

function updateSeatSummary() {
    const title = document.getElementById("seatSummaryTitle");
    const sub = document.getElementById("seatSummarySub");
    const dots = document.getElementById("seatSummaryDots");
    const confirmBtn = document.getElementById("seatConfirmBtn");

    if (pendingChairIds.length === 0) {
        if (pendingTableNumber !== null) {
            if (title) title.textContent = "میز " + pendingTableNumber + " — صندلی انتخاب نشده";
            if (sub) sub.textContent = "روی صندلی‌ها بزن تا انتخاب کنی";
            if (confirmBtn) confirmBtn.disabled = true;
        } else {
            resetSeatSummary();
            return;
        }
    } else {
        const count = pendingChairIds.length;
        if (title) title.textContent = "میز " + pendingTableNumber + " — " + count + " صندلی انتخاب شد";
        if (sub) sub.textContent = "صندلی بیشتر هم اضافه کنی یا تأیید کن";
        if (dots) {
            dots.innerHTML = "";
            for (let i = 0; i < 4; i++) {
                const d = document.createElement("span");
                if (i < count) d.classList.add("filled");
                dots.appendChild(d);
            }
        }
        if (confirmBtn) confirmBtn.disabled = false;
    }
}

// ===================== دکمه‌های کنترل =====================
function initSeatPicker() {
    const closeBtn = document.getElementById("seatClose");
    const pillBtn = document.getElementById("seatPillBtn");
    const overlay = document.getElementById("seatOverlay");
    const backBtn = document.getElementById("seatBackBtn");
    const confirmBtn = document.getElementById("seatConfirmBtn");

    if (pillBtn) pillBtn.addEventListener("click", openSeatModal);
    if (closeBtn) closeBtn.addEventListener("click", closeSeatModal);
    if (overlay) overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeSeatModal();
    });

    if (backBtn) backBtn.addEventListener("click", function () {
        // برگشت از مرحله ۲ به مرحله ۱ (پاک کردن انتخاب صندلی)
        pendingChairIds = [];
        if (pendingTableNumber !== null) {
            document.getElementById("seatPhaseTables").style.display = "block";
            document.getElementById("seatPhaseChairs").style.display = "none";
            Object.keys(tableCardElements).forEach(function (n) {
                tableCardElements[n].classList.remove("selected");
            });
            pendingTableNumber = null;
        }
        resetSeatSummary();
        updateTableCardStates();
    });

    if (confirmBtn) confirmBtn.addEventListener("click", confirmSeatSelection);

    // ساخت کارت‌های میز ۴ تایی (مرحله ۱)
    buildTableCards();
}

async function openSeatModal() {
    const overlay = document.getElementById("seatOverlay");
    if (overlay) { overlay.classList.add("open"); lockBodyScroll(); }

    // انتخابات قبلی را پاک کن (اگر میز قبلی انتخاب شده بود)
    pendingTableNumber = null;
    pendingChairIds = [];

    await fetchSeatOccupancy();

    // نمایش مرحله ۱ (میزها)
    document.getElementById("seatPhaseTables").style.display = "block";
    document.getElementById("seatPhaseChairs").style.display = "none";

    Object.keys(tableCardElements).forEach(function (n) {
        tableCardElements[n].classList.remove("selected");
    });

    resetSeatSummary();
    updateTableCardStates();

    // اگر میز از QR Code قفل شده، به‌طور خودکار کارت میز قفل‌شده را انتخاب می‌کنیم
    if (isTableLocked && initialTableNumber !== null) {
        showToast("✅ میز " + initialTableNumber + " از QR Code شما انتخاب شده است", "success");

        pendingTableNumber = initialTableNumber;
        // انتخاب کارت میز
        tableCardElements[initialTableNumber].classList.add("selected");

        // غیرفعال کردن کارت‌های دیگر
        updateTableCardStates();

        // حرکت به مرحله ۲
        document.getElementById("seatPhaseTables").style.display = "none";
        document.getElementById("seatPhaseChairs").style.display = "block";

        document.getElementById("chairPhaseTableNum").textContent = initialTableNumber;
        document.getElementById("chairTableLabel").textContent = "میز " + initialTableNumber;

        buildChairStage(initialTableNumber);
        updateChairState(initialTableNumber);
        resetSeatSummary();
        updateSeatSummary();
    }
}

function closeSeatModal() {
    const overlay = document.getElementById("seatOverlay");
    if (overlay && overlay.classList.contains("open")) {
        overlay.classList.remove("open");
        unlockBodyScroll();
    }
}

function confirmSeatSelection() {
    if (pendingChairIds.length === 0 || pendingTableNumber === null) return;

    selectedSeat = {
        tableIndex: pendingTableNumber,
        chairIds: pendingChairIds.slice(),
        tableLabel: "میز " + pendingTableNumber + " (" + pendingChairIds.length + " صندلی)"
    };

    sessionStorage.setItem("selectedSeat", JSON.stringify(selectedSeat));
    applySeatSelectionUI();
    closeSeatModal();

    showToast(selectedSeat.tableLabel + " با موفقیت رزرو شد. حالا می‌تونی سفارش بدی!", false);

    // Now that seating is settled, land them on the menu.
    const menu = document.getElementById("menu");
    if (menu) menu.scrollIntoView({ behavior: "smooth", block: "start" });
}

function applySeatSelectionUI() {
    const pill = document.getElementById("seatPillBtn");
    const label = document.getElementById("seatPillLabel");
    if (!selectedSeat) return;
    if (label) label.textContent = selectedSeat.tableLabel;
    if (pill) pill.classList.add("chosen");

    const seatMsg = document.getElementById("seatRequiredMsg");
    if (seatMsg) seatMsg.classList.remove("show");

    renderCart(); // refresh the order button / seat-required message state
}


// ==========================================================
// TOAST
// ==========================================================

function showToast(message, type) {
    const toast = document.getElementById("toast");
    const msg = document.getElementById("toastMsg");
    const icon = document.getElementById("toastIcon");
    if (!toast || !msg) { alert(message); return; }

    msg.textContent = message;

    // پاک کردن کلاس‌های قبلی
    toast.classList.remove("error", "success", "warning");

    // تشخیص نوع پیام (backward compatible با boolean)
    let messageType = "success"; // پیش‌فرض
    let iconClass = "bi bi-check-circle-fill";

    if (typeof type === "boolean") {
        // پشتیبانی از کد قدیمی که boolean می‌فرستاد
        messageType = type ? "error" : "success";
    } else if (typeof type === "string") {
        // پشتیبانی از انواع جدید: "success", "error", "warning"
        messageType = type;
    }

    // تنظیم کلاس و آیکون بر اساس نوع
    if (messageType === "error") {
        toast.classList.add("error");
        iconClass = "bi bi-exclamation-circle-fill";
    } else if (messageType === "warning") {
        toast.classList.add("warning");
        iconClass = "bi bi-exclamation-triangle-fill";
    } else {
        toast.classList.add("success");
        iconClass = "bi bi-check-circle-fill";
    }

    if (icon) icon.className = iconClass;
    toast.classList.add("show");
    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(function () { toast.classList.remove("show"); }, 2600);
}


// ==========================================================
// ORDER TRACKING — full history (list) + per-order detail
// ==========================================================

// مراحل پیگیری از این‌جا تعریف نمی‌شوند: سرور در هر پاسخ، لیست کامل
// `steps` (key / label / description / state) را می‌فرستد تا با وضعیت
// واقعی Backend هرگز ناهماهنگ نباشد.

let trackPollTimer = null;

function getOrderHistory() {
    try {
        return JSON.parse(sessionStorage.getItem("orderHistory") || "[]");
    } catch (e) {
        return [];
    }
}
function saveOrderHistory(list) {
    sessionStorage.setItem("orderHistory", JSON.stringify(list));
}

// توکن پیگیری هر سفارش (فقط در حافظهٔ همین تب؛ پس از Refresh باقی می‌ماند)
function getOrderToken(orderId) {
    const entry = getOrderHistory().find(function (o) {
        return String(o.id) === String(orderId);
    });
    return entry && entry.token ? entry.token : null;
}

function trackingHeaders(orderId) {
    const token = getOrderToken(orderId);
    return token ? { "X-Order-Token": token } : {};
}
function addOrderToHistory(order) {
    const list = getOrderHistory();
    list.unshift(order);
    if (list.length > 10) list.length = 10;
    saveOrderHistory(list);
}

function initTrackDrawer() {
    const fab = document.getElementById("trackFab");
    const closeBtn = document.getElementById("trackClose");
    const overlay = document.getElementById("trackOverlay");
    const backBtn = document.getElementById("trackBackBtn");

    if (fab) fab.addEventListener("click", openTrackList);
    if (closeBtn) closeBtn.addEventListener("click", closeTrackDrawer);
    if (overlay) overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeTrackDrawer();
    });
    if (backBtn) backBtn.addEventListener("click", openTrackList);
}

function openTrackList() {
    const drawer = document.getElementById("trackDrawer");
    const overlay = document.getElementById("trackOverlay");
    const listView = document.getElementById("trackOrderList");
    const detailView = document.getElementById("trackDetailView");
    const title = document.getElementById("trackDrawerTitle");
    if (!drawer || !overlay || !listView || !detailView) return;

    stopTrackPolling();
    drawer.classList.add("open");
    overlay.classList.add("open");

    const history = getOrderHistory();

    if (history.length === 0) {
        listView.style.display = "block";
        detailView.style.display = "none";
        listView.innerHTML = `<div class="empty-cart">هنوز سفارشی ثبت نکردی</div>`;
        if (title) title.textContent = "پیگیری سفارش‌ها";
        return;
    }

    if (history.length === 1) {
        openTrackDetail(history[0]);
        return;
    }

    if (title) title.textContent = "سفارش‌های تو";
    listView.style.display = "flex";
    detailView.style.display = "none";
    listView.innerHTML = "";

    history.forEach(function (order) {
        const item = document.createElement("div");
        item.className = "track-order-item";
        item.innerHTML = `
            <div>
                <h5>سفارش #${order.id}</h5>
                <p>${order.tableLabel || "—"}</p>
            </div>
            <i class="bi bi-chevron-left"></i>
        `;
        item.addEventListener("click", function () { openTrackDetail(order); });
        listView.appendChild(item);
    });
}

function openTrackDetail(order) {
    const drawer = document.getElementById("trackDrawer");
    const overlay = document.getElementById("trackOverlay");
    const listView = document.getElementById("trackOrderList");
    const detailView = document.getElementById("trackDetailView");
    const backBtn = document.getElementById("trackBackBtn");
    const title = document.getElementById("trackDrawerTitle");

    if (drawer) drawer.classList.add("open");
    if (overlay) overlay.classList.add("open");

    const history = getOrderHistory();

    if (listView) listView.style.display = "none";
    if (detailView) detailView.style.display = "block";
    if (backBtn) backBtn.style.display = history.length > 1 ? "flex" : "none";
    if (title) title.textContent = "پیگیری سفارش #" + order.id;

    // نمایش واضح شمارهٔ سفارش ثبت‌شده به مشتری
    const successCard = document.getElementById("orderSuccessCard");
    const successId = document.getElementById("successOrderId");
    const successTable = document.getElementById("successTableLabel");
    if (successCard) successCard.style.display = "block";
    if (successId) successId.textContent = order.id;
    if (successTable) successTable.textContent = order.tableLabel || ("میز " + (order.table || "—"));

    loadOrderTracking(order.id, order.tableLabel);
    startTrackPolling(order.id, order.tableLabel);
}

function closeTrackDrawer() {
    const drawer = document.getElementById("trackDrawer");
    const overlay = document.getElementById("trackOverlay");
    if (drawer) drawer.classList.remove("open");
    if (overlay) overlay.classList.remove("open");
    stopTrackPolling();
}

function startTrackPolling(orderId, tableLabel) {
    stopTrackPolling();
    trackPollTimer = setInterval(function () {
        loadOrderTracking(orderId, tableLabel);
    }, 3000);
}

function stopTrackPolling() {
    if (trackPollTimer) {
        clearInterval(trackPollTimer);
        trackPollTimer = null;
    }
}

function checkForActiveOrder() {
    const history = getOrderHistory();
    const fab = document.getElementById("trackFab");
    const ping = document.getElementById("trackPing");
    if (history.length === 0 || !fab) return;
    fab.classList.add("show");
    if (ping) ping.style.display = "block";
}

function ensureTrackInfoBox() {
    let box = document.getElementById("trackInfoBox");
    if (box) return box;

    const stepsBox = document.getElementById("trackSteps");
    if (!stepsBox || !stepsBox.parentNode) return null;

    box = document.createElement("div");
    box.id = "trackInfoBox";
    box.className = "seat-summary";
    box.style.marginTop = "18px";
    box.innerHTML = `
        <div class="ss-info">
            <h5 id="trackStatusText">در انتظار تایید</h5>
            <p id="trackTotalText">—</p>
        </div>
    `;
    stepsBox.parentNode.insertBefore(box, stepsBox);
    return box;
}

async function loadOrderTracking(orderId, tableLabel) {
    const idEl = document.getElementById("trackOrderId");
    const seatEl = document.getElementById("trackSeatLabel");
    const errBox = document.getElementById("trackError");
    const errMsg = document.getElementById("trackErrorMsg");
    const infoBox = ensureTrackInfoBox();

    if (idEl) idEl.textContent = orderId;
    if (seatEl) seatEl.textContent = tableLabel || "—";
    if (errBox) errBox.style.display = "none";

    try {
        const response = await fetch(`/api/orders/${orderId}/`, {
            headers: trackingHeaders(orderId)
        });
        const data = await response.json();

        if (!response.ok) {
            if (response.status === 404) {
                stopTrackPolling();
                if (errBox && errMsg) {
                    errMsg.textContent = "دسترسی به این سفارش مجاز نیست یا کلید پیگیری معتبر نیست. لطفاً از صفحهٔ ثبت سفارش خود پیگیری کنید.";
                    errBox.style.display = "flex";
                }
                return;
            }
            throw new Error(data.error || "خطا در دریافت وضعیت سفارش");
        }

        renderTrackSteps(data.steps, data.status);
        renderConfirmPrompt(orderId, data.status, data.customer_confirmed);

        if (infoBox) {
            const statusText = infoBox.querySelector("#trackStatusText");
            const totalText = infoBox.querySelector("#trackTotalText");
            if (statusText) {
                statusText.textContent = data.status_label || "وضعیت سفارش در حال بررسی است";
            }
            if (totalText && typeof data.table !== "undefined") {
                const totalPart = data.total ? Number(data.total).toLocaleString() + " تومان" : "";
                totalText.textContent = `میز ${data.table}${totalPart ? " • " + totalPart : ""}`;
            }
        }

        if (data.cancelled || data.status === "rejected") {
            stopTrackPolling();
            if (errBox && errMsg) {
                errMsg.textContent = "متأسفانه سفارش شما رد شد.";
                errBox.style.display = "flex";
            }
        } else if (data.status === "done") {
            stopTrackPolling();
        }
    } catch (error) {
        console.error("Order Tracking Error:", error);
        if (errBox && errMsg) {
            errMsg.textContent = "امکان دریافت وضعیت سفارش نیست.";
            errBox.style.display = "flex";
        }
    }
}

// Shows a "did you receive it?" prompt once the kitchen's timer expires,
// and a "we're on it" note if the customer already said no.
function renderConfirmPrompt(orderId, status, customerConfirmed) {
    let box = document.getElementById("trackConfirmBox");

    if (status !== "awaiting_confirmation") {
        if (box) box.remove();
        return;
    }

    if (!box) {
        const stepsBox = document.getElementById("trackSteps");
        if (!stepsBox || !stepsBox.parentNode) return;
        box = document.createElement("div");
        box.id = "trackConfirmBox";
        box.className = "seat-summary";
        box.style.marginTop = "14px";
        stepsBox.parentNode.insertBefore(box, stepsBox.nextSibling);
    }

    if (customerConfirmed === false) {
        box.innerHTML = `
            <div class="ss-info">
                <h5 style="color:var(--rose);">پیام شما ثبت شد</h5>
                <p>آشپزخانه در حال پیگیریه — تا لحظاتی دیگه بهت می‌رسن.</p>
            </div>
        `;
        return;
    }

    box.innerHTML = `
        <div class="ss-info">
            <h5>سفارشت رو گرفتی؟</h5>
            <p>لطفاً تأیید کن تا سفارش تکمیل بشه.</p>
        </div>
    `;
    const actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.gap = "10px";
    actions.innerHTML = `
        <button class="btn-book-confirm" style="background:var(--green);" onclick="confirmOrderReceived(${orderId}, true)">بله، رسید</button>
        <button class="btn-book-confirm" style="background:var(--rose);" onclick="confirmOrderReceived(${orderId}, false)">نه، نرسید</button>
    `;
    box.appendChild(actions);
}

async function confirmOrderReceived(orderId, received) {
    try {
        const response = await fetch(`/api/orders/${orderId}/confirm/`, {
            method: "POST",
            headers: Object.assign(
                { "Content-Type": "application/json", "X-CSRFToken": csrftoken },
                trackingHeaders(orderId)
            ),
            body: JSON.stringify({ received: received })
        });
        const data = await response.json();
        if (!data.success) {
            showToast(data.error || "خطا در ثبت پاسخ", true);
            return;
        }
        showToast(received ? "ممنون! نوش جان 🎉" : "پیامت به آشپزخانه رسید.", false);
        loadOrderTracking(orderId);
    } catch (error) {
        console.error("Confirm Order Error:", error);
        showToast("خطا در ارتباط با سرور", true);
    }
}

// رندر مراحل بر اساس پاسخ سرور؛ وضعیت ناشناخته/لغوشده صفحه را خراب نمی‌کند.
function renderTrackSteps(steps, status) {
    const box = document.getElementById("trackSteps");
    if (!box) return;
    box.innerHTML = "";

    if (!Array.isArray(steps) || steps.length === 0) return;

    const cancelled = status === "rejected";

    if (cancelled) {
        steps.forEach(function (step, i) {
            const el = document.createElement("div");
            el.className = "track-step";
            const dotStyle = i === 0 ? '' : 'background:#C2495B;border-color:#C2495B;color:#fff;';
            el.innerHTML = `
                <div class="track-dot" style="${dotStyle}"><i class="bi ${i === 0 ? 'bi-check-lg' : 'bi-x-lg'}"></i></div>
                <div class="track-info">
                    <h5 style="${i === 0 ? '' : 'color:#C2495B;'}">${i === 0 ? step.label : 'لغو شد'}</h5>
                    <p>${i === 0 ? (step.description || '') : ''}</p>
                </div>
            `;
            if (i === 0) el.classList.add("done");
            box.appendChild(el);
        });
        return;
    }

    steps.forEach(function (step) {
        const el = document.createElement("div");
        el.className = "track-step";

        if (step.state === "done") el.classList.add("done");
        else if (step.state === "current") el.classList.add("active");

        const iconClass = step.state === "done"
            ? "bi-check-lg"
            : (step.state === "current" ? "bi-record-circle" : "bi-circle");

        el.innerHTML = `
            <div class="track-dot"><i class="bi ${iconClass}"></i></div>
            <div class="track-info">
                <h5>${step.label || ''}</h5>
                <p>${step.description || ''}</p>
            </div>
        `;
        box.appendChild(el);
    });
}