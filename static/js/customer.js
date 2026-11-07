/*
==========================
LUMEN — Smart Menu
customer.js
==========================
API endpoints used:
  GET  /api/foods/
  POST /api/create_order/
  GET  /api/orders/<id>/
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
// Start
// ==========================

window.onload = function () {
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
            const imgSrc = food.image || "/static/images/no-food.png";

            const row = document.createElement("div");
            row.className = "menu-row glass" + (soldOut ? " sold-out" : "");
            row.dataset.foodId = food.id;

            row.innerHTML = `
                <div class="thumb">
                    <img src="${imgSrc}" onerror="this.src='/static/images/no-food.png'" alt="${food.name}">
                    ${soldOut ? '<span class="badge out">ناموجود</span>' : ''}
                </div>
                <div class="body">
                    <h4>${food.name}</h4>
                    <div class="stat-line"><i class="bi bi-tag-fill"></i> ${food.category || ""}</div>
                    <p class="desc">${food.description || ""}</p>
                    <div class="price-chip"><span class="mono-num">${Number(food.price).toLocaleString()}</span><span class="unit">تومان</span></div>
                </div>
                <div class="side">
                    <button class="add-btn" ${soldOut ? "disabled" : ""} aria-label="افزودن ${food.name}"><i class="bi bi-plus-lg"></i></button>
                </div>
            `;

            if (!soldOut) {
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

async function submitOrder() {
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

    const currentUser = getCurrentUser();
    const order = {
        table: selectedSeat.tableIndex,
        seats: selectedSeat.chairIds,
        name: currentUser ? currentUser.name : undefined,
        phone: currentUser ? currentUser.phone : undefined,
        foods: cart
    };

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
            showToast(data.error || "خطا در ثبت سفارش", true);
            return;
        }

        if (data.success) {
            cart = [];
            saveCart();
            renderCart();

            const orderId = data.order_id;
            const tableLabel = selectedSeat.tableLabel;
            const newOrder = { id: orderId, tableLabel: tableLabel, createdAt: Date.now() };
            addOrderToHistory(newOrder);

            closeCartDrawer();

            showToast("سفارش با موفقیت ثبت شد!", false);

            const fab = document.getElementById("trackFab");
            const ping = document.getElementById("trackPing");
            if (fab) fab.classList.add("show");
            if (ping) ping.style.display = "block";

            openTrackDetail(newOrder);
        } else {
            showToast(data.error || "خطا در ثبت سفارش", true);
        }

    } catch (error) {
        console.error("Submit Order Error:", error);
        showToast("خطا در ارتباط با سرور", true);
    }
}


// ==========================================================
// SEAT / TABLE PICKER — 64 tables, dynamic layout, real occupancy
// ==========================================================

// ---- Floor configuration (تغییر تعداد میزها فقط همینجا) ----
const FLOOR_TOTAL_TABLES     = 4;    // تعداد میزها کاهش یافت به 4 میز
const FLOOR_TWO_SEATER_START = 999;  // همه میزها 4 نفره (هیچ میز دو نفره‌ای نداریم)
const FLOOR_COLS             = 2;    // چیدمان 2 ستونی (2x2)

// فاصله‌ی صندلی از مرکز میز (px)
const CHAIR_OFFSETS_4 = [ // میز ۴ نفره
    { key: "top",    dx: 0,   dy: -72 },
    { key: "right",  dx: 72,  dy: 0   },
    { key: "bottom", dx: 0,   dy: 72  },
    { key: "left",   dx: -72, dy: 0   }
];
const CHAIR_OFFSETS_2 = [ // میز ۲ نفره — فقط دو طرف مقابل هم
    { key: "top",    dx: 0, dy: -78 },
    { key: "bottom", dx: 0, dy: 78  }
];

// جیتر ثابت (deterministic) — تصادفی به‌نظر می‌رسه ولی هر بار همون مقدار رو می‌ده
function seededJitter(seed) {
    const x = Math.sin(seed * 12.9898) * 43758.5453;
    return (x - Math.floor(x)) - 0.5; // -0.5..0.5
}

// تولید layout به‌جای آرایه‌ی دستی — فقط یک‌بار در زمان لود اسکریپت اجرا می‌شود
function generateSeatLayout(totalTables, cols) {
    const rows = Math.ceil(totalTables / cols);

    const marginX = 7, marginY = 9;      // درصد فاصله از لبه‌ی سالن
    const usableW = 100 - marginX * 2;
    const usableH = 100 - marginY * 2;

    const colGroups = Math.ceil(cols / 4);   // هر ۴ ستون یک راهرو عمودی
    const rowGroups = Math.ceil(rows / 4);   // هر ۴ ردیف یک راهرو افقی
    const aisleColGap = 4.2;                 // % فاصله‌ی راهرو عمودی
    const aisleRowGap = 5.5;                 // % فاصله‌ی راهرو افقی

    const cellW = (usableW - aisleColGap * (colGroups - 1)) / cols;
    const cellH = (usableH - aisleRowGap * (rowGroups - 1)) / rows;

    const layout = [];
    let tableIndex = 0;

    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            tableIndex++;
            if (tableIndex > totalTables) break;

            const colGroupIdx = Math.floor(c / 4);
            const rowGroupIdx = Math.floor(r / 4);

            let left = marginX + c * cellW + colGroupIdx * aisleColGap + cellW / 2;
            let top  = marginY + r * cellH + rowGroupIdx * aisleRowGap + cellH / 2;

            // آفست طبیعی و ثابت، تا شبیه جدول خشک نباشه ولی هر بار همون شکل بمونه
            left += seededJitter(tableIndex * 3.1) * (cellW * 0.22);
            top  += seededJitter(tableIndex * 7.7) * (cellH * 0.22);

            const isTwoSeater = tableIndex >= FLOOR_TWO_SEATER_START;

            layout.push({
                id: "T" + String(tableIndex).padStart(2, "0"), // T01 .. T64
                tableIndex: tableIndex,
                leftPct: left,
                topPct: top,
                seatCount: isTwoSeater ? 2 : 4
            });
        }
    }
    return layout;
}

// این‌جا یک‌بار (در زمان لود اسکریپت) تولید می‌شه، نه هر بار رندر
const SEAT_LAYOUT = generateSeatLayout(FLOOR_TOTAL_TABLES, FLOOR_COLS);

const SEATS_STATUS_URL = "/api/seats/status/";
let occupiedChairs = new Set();   // filled from the backend, not random
let pendingTableIndex = null;     // the table currently being picked on (locks selection to one table)
let pendingChairIds = [];         // chair keys selected so far, e.g. ["T03-top","T03-right"]

// --- کش DOM: میزها فقط یک‌بار ساخته می‌شوند، بعد از آن فقط کلاس‌ها تغییر می‌کنند ---
let seatFloorBuilt = false;
let chairElementsByKey = {};
let tableGroupElements = {};

async function fetchSeatOccupancy() {
    try {
        const res = await fetch(SEATS_STATUS_URL);
        if (!res.ok) throw new Error("bad response");
        const data = await res.json();
        occupiedChairs = new Set(data.occupied || []);
    } catch (e) {
        console.error("Seat status fetch failed:", e);
        occupiedChairs = new Set(); // fail open: show everything as free rather than block ordering
    }
}

// ---------- Zoom / Pan / Rotate state ----------
const floorState = { scale: 1, tx: 0, ty: 0, rotateZ: 0 };
const FLOOR_MIN_SCALE = 0.55;
const FLOOR_MAX_SCALE = 2.6;
const FLOOR_BASE_TILT = 52; // درجه، خم شدن ثابت کف برای حس 3D

function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
function pointerDist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

function applyFloorTransform() {
    const floor = document.getElementById("seatFloor");
    if (!floor) return;
    floor.style.transform =
        `translate(${floorState.tx}px, ${floorState.ty}px) ` +
        `scale(${floorState.scale}) ` +
        `rotateX(${FLOOR_BASE_TILT}deg) rotateZ(${floorState.rotateZ}deg)`;
}

function resetFloorTransform() {
    floorState.scale = 1;
    floorState.tx = 0;
    floorState.ty = 0;
    floorState.rotateZ = 0;
    applyFloorTransform();
}

function rotateFloor(deltaDeg) {
    floorState.rotateZ += deltaDeg;
    applyFloorTransform();
}

function zoomFloorBy(delta) {
    floorState.scale = clamp(floorState.scale + delta, FLOOR_MIN_SCALE, FLOOR_MAX_SCALE);
    applyFloorTransform();
}

function initFloorGestures() {
    const viewport = document.getElementById("seatFloorViewport");
    if (!viewport) return;

    const activePointers = new Map();
    let dragState = null;
    let pinchState = null;

    viewport.addEventListener("pointerdown", function (e) {
        viewport.setPointerCapture(e.pointerId);
        activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

        if (activePointers.size === 1) {
            dragState = { startX: e.clientX, startY: e.clientY, originTx: floorState.tx, originTy: floorState.ty };
        } else if (activePointers.size === 2) {
            const pts = Array.from(activePointers.values());
            pinchState = { startDist: pointerDist(pts[0], pts[1]), startScale: floorState.scale };
            dragState = null;
        }
    });

    viewport.addEventListener("pointermove", function (e) {
        if (!activePointers.has(e.pointerId)) return;
        activePointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

        if (activePointers.size === 2 && pinchState) {
            const pts = Array.from(activePointers.values());
            const ratio = pointerDist(pts[0], pts[1]) / pinchState.startDist;
            floorState.scale = clamp(pinchState.startScale * ratio, FLOOR_MIN_SCALE, FLOOR_MAX_SCALE);
            applyFloorTransform();
        } else if (dragState) {
            floorState.tx = dragState.originTx + (e.clientX - dragState.startX);
            floorState.ty = dragState.originTy + (e.clientY - dragState.startY);
            applyFloorTransform();
        }
    });

    function endPointer(e) {
        activePointers.delete(e.pointerId);
        if (activePointers.size < 2) pinchState = null;
        if (activePointers.size === 0) dragState = null;
    }
    viewport.addEventListener("pointerup", endPointer);
    viewport.addEventListener("pointercancel", endPointer);
    viewport.addEventListener("pointerleave", endPointer);

    viewport.addEventListener("wheel", function (e) {
        e.preventDefault();
        zoomFloorBy(e.deltaY > 0 ? -0.12 : 0.12);
    }, { passive: false });
}

function initSeatPicker() {
    const closeBtn = document.getElementById("seatClose");
    const pillBtn = document.getElementById("seatPillBtn");
    const overlay = document.getElementById("seatOverlay");
    const rotateLeft = document.getElementById("rotateLeft");
    const rotateRight = document.getElementById("rotateRight");
    const confirmBtn = document.getElementById("seatConfirmBtn");
    const zoomInBtn = document.getElementById("zoomInBtn");
    const zoomOutBtn = document.getElementById("zoomOutBtn");
    const zoomResetBtn = document.getElementById("zoomResetBtn");

    // Re-opening from the navbar pill lets them change seats later —
    // closing is allowed there. During first-time onboarding the modal
    // is opened programmatically by maybeOpenSeatGate(); the close
    // button still works so no one gets stuck if they close by mistake,
    // they'll just be prompted again next time they try to order.
    if (pillBtn) pillBtn.addEventListener("click", openSeatModal);
    if (closeBtn) closeBtn.addEventListener("click", closeSeatModal);
    if (overlay) overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeSeatModal();
    });

    if (rotateLeft) rotateLeft.addEventListener("click", function () { rotateFloor(-45); });
    if (rotateRight) rotateRight.addEventListener("click", function () { rotateFloor(45); });
    if (zoomInBtn) zoomInBtn.addEventListener("click", function () { zoomFloorBy(0.2); });
    if (zoomOutBtn) zoomOutBtn.addEventListener("click", function () { zoomFloorBy(-0.2); });
    if (zoomResetBtn) zoomResetBtn.addEventListener("click", resetFloorTransform);

    if (confirmBtn) confirmBtn.addEventListener("click", confirmSeatSelection);

    // DOM ۶۴ میز رو یک‌بار می‌سازه، بار اول با occupancy خالی
    renderSeatFloor();
    resetFloorTransform();
    initFloorGestures();
}

async function openSeatModal() {
    const overlay = document.getElementById("seatOverlay");
    if (overlay) { overlay.classList.add("open"); lockBodyScroll(); }

    // Reset the in-progress picker state (previous confirmed selection,
    // if any, is untouched until they hit "تأیید" again).
    pendingTableIndex = null;
    pendingChairIds = [];

    await fetchSeatOccupancy();
    renderSeatFloor();
    resetSeatSummary();
    resetFloorTransform();
}

function closeSeatModal() {
    const overlay = document.getElementById("seatOverlay");
    if (overlay && overlay.classList.contains("open")) {
        overlay.classList.remove("open");
        unlockBodyScroll();
    }
}

// ---------- ساخت DOM (فقط یک‌بار، نه در هر رندر) ----------
function buildSeatFloorDOM() {
    const floor = document.getElementById("seatFloor");
    if (!floor) return;
    floor.innerHTML = "";
    chairElementsByKey = {};
    tableGroupElements = {};

    SEAT_LAYOUT.forEach(function (table) {
        const group = document.createElement("div");
        group.className = "table-group " + (table.seatCount === 2 ? "table-two" : "table-four");
        group.style.left = table.leftPct + "%";
        group.style.top = table.topPct + "%";
        group.dataset.tableId = table.id;
        group.dataset.tableIndex = table.tableIndex;

        group.innerHTML = `
            <div class="table-core">
                <div class="table-shadow"></div>
                <div class="table-edge"></div>
                <div class="table-top"><span class="table-label">${table.tableIndex}</span></div>
            </div>
        `;

        const chairDefs = table.seatCount === 2 ? CHAIR_OFFSETS_2 : CHAIR_OFFSETS_4;
        chairDefs.forEach(function (chairDef) {
            const chairKey = table.id + "-" + chairDef.key;

            const chair = document.createElement("div");
            chair.className = "chair";
            chair.style.left = "calc(50% + " + chairDef.dx + "px)";
            chair.style.top  = "calc(50% + " + chairDef.dy + "px)";
            chair.dataset.chairKey = chairKey;
            chair.dataset.tableIndex = table.tableIndex;

            chair.innerHTML = `
                <div class="chair-back"></div>
                <div class="chair-seat"></div>
                <div class="chair-reticle"></div>
            `;

            chair.addEventListener("click", function () {
                handleChairClick(table.tableIndex, chairKey);
            });

            chairElementsByKey[chairKey] = chair;
            group.appendChild(chair);
        });

        tableGroupElements[table.tableIndex] = group;
        floor.appendChild(group);
    });

    seatFloorBuilt = true;
}

// کلیک روی صندلی — همون قانون قبلی: occupied یا قفل‌شده روی میز دیگه قابل‌کلیک نیست
function handleChairClick(tableIndex, chairKey) {
    if (occupiedChairs.has(chairKey)) return;
    if (pendingTableIndex !== null && pendingTableIndex !== tableIndex) return;
    toggleChair(tableIndex, chairKey);
}

// ---------- فقط آپدیت کلاس‌های DOM موجود، بدون بازسازی هر بار ----------
function updateSeatFloorState() {
    SEAT_LAYOUT.forEach(function (table) {
        const group = tableGroupElements[table.tableIndex];
        if (!group) return;

        let anySelected = false;
        let allOccupied = true;

        const chairDefs = table.seatCount === 2 ? CHAIR_OFFSETS_2 : CHAIR_OFFSETS_4;
        chairDefs.forEach(function (chairDef) {
            const chairKey = table.id + "-" + chairDef.key;
            const chair = chairElementsByKey[chairKey];
            if (!chair) return;

            const isOccupied = occupiedChairs.has(chairKey);
            const isSelected = pendingChairIds.indexOf(chairKey) !== -1;
            // Once a table is locked in, chairs at every other table are disabled
            // so a single order can't span multiple tables.
            const isLockedElsewhere = pendingTableIndex !== null && pendingTableIndex !== table.tableIndex;

            if (isSelected) anySelected = true;
            if (!isOccupied) allOccupied = false;

            chair.classList.toggle("occupied", isOccupied || isLockedElsewhere);
            chair.classList.toggle("free", !isOccupied && !isLockedElsewhere);
            chair.classList.toggle("selected", isSelected);
        });

        group.classList.toggle("selected", anySelected);
        group.classList.toggle("table-full", allOccupied);
    });
}

// این تابع همون اسم قبلی رو نگه داشته تا بقیه‌ی کد صدا زدنش عوض نشه؛
// اما دیگر innerHTML را از صفر نمی‌سازد — فقط اولین بار DOM را می‌سازد
// و از آن به بعد فقط کلاس‌ها را به‌روزرسانی می‌کند.
function renderSeatFloor() {
    if (!seatFloorBuilt) buildSeatFloorDOM();
    updateSeatFloorState();
}

function toggleChair(tableIndex, chairKey) {
    const idx = pendingChairIds.indexOf(chairKey);

    if (idx !== -1) {
        // deselecting
        pendingChairIds.splice(idx, 1);
        if (pendingChairIds.length === 0) pendingTableIndex = null;
    } else {
        // first chair picked locks the table for this order
        if (pendingTableIndex === null) pendingTableIndex = tableIndex;
        pendingChairIds.push(chairKey);
    }

    renderSeatFloor();
    updateSeatSummary();
}

function resetSeatSummary() {
    const title = document.getElementById("seatSummaryTitle");
    const sub = document.getElementById("seatSummarySub");
    const dots = document.getElementById("seatSummaryDots");
    const confirmBtn = document.getElementById("seatConfirmBtn");

    if (title) title.textContent = "هنوز میزی انتخاب نشده";
    if (sub) sub.textContent = "روی یکی از صندلی‌ها بزن — می‌تونی چند صندلی هم انتخاب کنی";
    if (dots) dots.innerHTML = "";
    if (confirmBtn) confirmBtn.disabled = true;
}

function updateSeatSummary() {
    const title = document.getElementById("seatSummaryTitle");
    const sub = document.getElementById("seatSummarySub");
    const dots = document.getElementById("seatSummaryDots");
    const confirmBtn = document.getElementById("seatConfirmBtn");

    if (pendingChairIds.length === 0) {
        resetSeatSummary();
        return;
    }

    const count = pendingChairIds.length;
    if (title) title.textContent = "میز " + pendingTableIndex + " — " + count + " صندلی انتخاب شد";
    if (sub) sub.textContent = "می‌تونی صندلی بیشتری هم اضافه کنی یا تأیید کن";
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

function confirmSeatSelection() {
    if (pendingChairIds.length === 0 || pendingTableIndex === null) return;

    selectedSeat = {
        tableIndex: pendingTableIndex,
        chairIds: pendingChairIds.slice(),
        tableLabel: "میز " + pendingTableIndex + " (" + pendingChairIds.length + " صندلی)"
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

function showToast(message, isError) {
    const toast = document.getElementById("toast");
    const msg = document.getElementById("toastMsg");
    const icon = document.getElementById("toastIcon");
    if (!toast || !msg) { alert(message); return; }
    msg.textContent = message;
    toast.classList.toggle("error", !!isError);
    if (icon) icon.className = isError ? "bi bi-exclamation-circle-fill" : "bi bi-check-circle-fill";
    toast.classList.add("show");
    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(function () { toast.classList.remove("show"); }, 2600);
}


// ==========================================================
// ORDER TRACKING — full history (list) + per-order detail
// ==========================================================

const TRACK_STEPS = [
    { key: "new",                    label: "سفارش ثبت شد",        desc: "سفارش شما با موفقیت دریافت شد" },
    { key: "cooking",                label: "آماده‌سازی و ارسال",   desc: "آشپزخانه در حال آماده کردن و ارسال سفارشته" },
    { key: "awaiting_confirmation",  label: "در انتظار تأیید شما", desc: "لطفاً تأیید کن که سفارش رو گرفتی" },
    { key: "done",                   label: "تحویل شد",            desc: "نوش جان!" }
];

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
        const response = await fetch(`/api/orders/${orderId}/`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "خطا در دریافت وضعیت سفارش");
        }

        renderTrackSteps(data.status);
        renderConfirmPrompt(orderId, data.status, data.customer_confirmed);

        if (infoBox) {
            const statusText = infoBox.querySelector("#trackStatusText");
            const totalText = infoBox.querySelector("#trackTotalText");
            const labels = {
                new: "در انتظار تایید", cooking: "در حال آماده‌سازی و ارسال",
                awaiting_confirmation: "رسیدت رو تأیید کن", done: "نوش جان!", rejected: "سفارش رد شد"
            };
            if (statusText) statusText.textContent = labels[data.status] || labels.new;
            if (totalText && typeof data.table !== "undefined") {
                const totalPart = data.total ? Number(data.total).toLocaleString() + " تومان" : "";
                totalText.textContent = `میز ${data.table}${totalPart ? " • " + totalPart : ""}`;
            }
        }

        if (data.status === "rejected") {
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
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrftoken },
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

function renderTrackSteps(status) {
    const box = document.getElementById("trackSteps");
    if (!box) return;
    box.innerHTML = "";

    if (status === "rejected") {
        TRACK_STEPS.forEach(function (step, i) {
            const el = document.createElement("div");
            el.className = "track-step";
            const dotStyle = i === 0 ? '' : 'background:#C2495B;border-color:#C2495B;color:#fff;';
            el.innerHTML = `
                <div class="track-dot" style="${dotStyle}"><i class="bi ${i === 0 ? 'bi-check-lg' : 'bi-x-lg'}"></i></div>
                <div class="track-info">
                    <h5 style="${i === 0 ? '' : 'color:#C2495B;'}">${i === 0 ? step.label : 'لغو شد'}</h5>
                    <p>${i === 0 ? step.desc : ''}</p>
                </div>
            `;
            if (i === 0) el.classList.add("done");
            box.appendChild(el);
        });
        return;
    }

    const currentIndex = TRACK_STEPS.findIndex(s => s.key === status);

    TRACK_STEPS.forEach(function (step, i) {
        const el = document.createElement("div");
        el.className = "track-step";
        if (currentIndex >= 0) {
            if (i < currentIndex) el.classList.add("done");
            else if (i === currentIndex) el.classList.add("active");
        }

        el.innerHTML = `
            <div class="track-dot"><i class="bi ${i <= currentIndex ? 'bi-check-lg' : 'bi-circle'}"></i></div>
            <div class="track-info">
                <h5>${step.label}</h5>
                <p>${step.desc}</p>
            </div>
        `;
        box.appendChild(el);
    });
}