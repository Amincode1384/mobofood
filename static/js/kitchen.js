// ===============================
// Midnight Kitchen — real-time dashboard
// ===============================

// ---- CSRF ----
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        for (let cookie of document.cookie.split(";")) {
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

// ---- state ----
const orders = new Map(); // id -> order object (server shape from serialize_order)
let ws = null;
let wsAttempts = 0;
let pollingFallback = null;
const MAX_WS_ATTEMPTS_BEFORE_FALLBACK = 5;

// ===============================
// Toasts
// ===============================
function showToast(message, type) {
    const stack = document.getElementById("toastStack");
    if (!stack) return;
    const el = document.createElement("div");
    el.className = "kh-toast" + (type ? " " + type : "");
    el.innerHTML = `<i class="bi bi-${type === "error" ? "exclamation-triangle-fill" : "check-circle-fill"}"></i> ${message}`;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 3400);
}

// ===============================
// Header clock
// ===============================
function updateClock() {
    const el = document.getElementById("headerClock");
    if (!el) return;
    el.textContent = new Date().toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ===============================
// Connection status pill
// ===============================
function setConnStatus(state) {
    const pill = document.getElementById("connStatus");
    const text = document.getElementById("connStatusText");
    if (!pill || !text) return;
    pill.classList.remove("warn", "down");
    if (state === "live") {
        text.textContent = "زنده و متصل";
    } else if (state === "connecting") {
        pill.classList.add("warn");
        text.textContent = "در حال اتصال...";
    } else {
        pill.classList.add("down");
        text.textContent = "قطع — تلاش مجدد...";
    }
}

// ===============================
// WebSocket connection (with reconnect + polling fallback)
// ===============================
function connectWS() {
    setConnStatus(wsAttempts === 0 ? "connecting" : "down");

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws/kitchen/`);

    ws.onopen = function () {
        wsAttempts = 0;
        setConnStatus("live");
        stopPollingFallback();
        ws.send(JSON.stringify({ action: "request_snapshot" }));
    };

    ws.onmessage = function (event) {
        let data;
        try { data = JSON.parse(event.data); } catch (e) { return; }
        handleServerEvent(data);
    };

    ws.onclose = function () {
        setConnStatus("down");
        scheduleReconnect();
    };

    ws.onerror = function () {
        try { ws.close(); } catch (e) { /* noop */ }
    };
}

function scheduleReconnect() {
    wsAttempts++;
    if (wsAttempts > MAX_WS_ATTEMPTS_BEFORE_FALLBACK) {
        startPollingFallback();
        // keep trying WS quietly in the background so we can drop the
        // fallback the moment it's available again
        setTimeout(connectWS, 15000);
        return;
    }
    const delay = Math.min(1000 * Math.pow(2, wsAttempts), 15000);
    setTimeout(connectWS, delay);
}

function startPollingFallback() {
    if (pollingFallback) return;
    showToast("اتصال زنده برقرار نشد — حالت بروزرسانی دوره‌ای فعال شد.", "error");
    pollingFallback = setInterval(loadOrdersViaRest, 4000);
    loadOrdersViaRest();
}
function stopPollingFallback() {
    if (!pollingFallback) return;
    clearInterval(pollingFallback);
    pollingFallback = null;
}

async function loadOrdersViaRest() {
    try {
        const res = await fetch("/api/orders/");
        if (!res.ok) return;
        const list = await res.json();
        handleServerEvent({ event: "snapshot", orders: list });
    } catch (e) {
        console.error("REST fallback fetch failed:", e);
    }
}

// ===============================
// Server event handling
// ===============================
function handleServerEvent(data) {
    if (!data || !data.event) return;

    if (data.event === "snapshot") {
        const seen = new Set();
        (data.orders || []).forEach(order => {
            if (isBoardStatus(order.status)) {
                seen.add(order.id);
                upsertOrder(order);
            }
        });
        // remove any cards we're showing that the snapshot no longer includes
        Array.from(orders.keys()).forEach(id => {
            if (!seen.has(id)) removeOrder(id);
        });
        return;
    }

    if (data.event === "new_order") {
        upsertOrder(data.order);
        beep();
        showToast(`سفارش جدید — میز ${data.order.table}`, "success");
        return;
    }

    if (data.event === "order_updated") {
        const order = data.order;
        if (isBoardStatus(order.status)) {
            upsertOrder(order);
        } else {
            removeOrder(order.id);
        }
        return;
    }

    if (data.event === "order_removed") {
        removeOrder(data.order_id);
    }
}

// A short synthesized beep — no audio file dependency, and gracefully
// no-ops if the browser blocks autoplay before any user interaction.
function beep() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
        osc.connect(gain).connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.35);
    } catch (e) { /* ignore — non-critical */ }
}

// ===============================
// Rendering — only the affected card is touched, never a full re-render
// ===============================
// وضعیت‌هایی که روی برد آشپزخانه دیده می‌شوند.
// new → cooking → ready → sent → awaiting_confirmation
// هر وضعیت ستون خودش را دارد: «آمادهٔ ارسال» و «ارسال‌شده» جدا هستند.
const BOARD_STATUSES = ["new", "cooking", "ready", "sent", "awaiting_confirmation"];

function isBoardStatus(status) {
    return BOARD_STATUSES.includes(status);
}

function cardSkin(status) {
    if (status === "new") return "new";
    if (status === "cooking") return "cooking";
    if (status === "ready") return "ready";
    if (status === "sent") return "sent";
    return "waiting";
}

function columnForStatus(status) {
    if (status === "new") return document.getElementById("colNew");
    if (status === "cooking") return document.getElementById("colCooking");
    if (status === "ready") return document.getElementById("colReady");
    if (status === "sent") return document.getElementById("colSent");
    if (status === "awaiting_confirmation") return document.getElementById("colWaiting");
    return null;
}

function upsertOrder(order) {
    const prev = orders.get(order.id);
    orders.set(order.id, order);

    const targetCol = columnForStatus(order.status);
    if (!targetCol) { removeOrder(order.id); return; }

    let card = document.getElementById("order-" + order.id);
    const statusChanged = !prev || prev.status !== order.status;

    if (card && statusChanged) {
        // moved between columns — rebuild in the right place
        card.remove();
        card = null;
    }

    if (!card) {
        card = buildOrderCard(order);
        targetCol.prepend(card);
        clearEmptyState(targetCol);
    } else {
        updateOrderCard(card, order);
    }

    refreshCounts();
}

function removeOrder(id) {
    if (!orders.has(id)) return;
    orders.delete(id);
    const card = document.getElementById("order-" + id);
    if (card) card.remove();
    refreshCounts();
}

function refreshCounts() {
    ["new", "cooking", "ready", "sent", "waiting"].forEach(key => {
        const col = document.getElementById("col" + key.charAt(0).toUpperCase() + key.slice(1));
        const countEl = document.getElementById("count" + key.charAt(0).toUpperCase() + key.slice(1));
        if (!col || !countEl) return;
        const count = col.querySelectorAll(".order-card").length;
        countEl.textContent = count;
        if (count === 0 && !col.querySelector(".kh-empty")) {
            const emptyText = {
                new: "سفارش جدیدی نیست",
                cooking: "چیزی در حال پخت نیست",
                ready: "سفارشی آمادهٔ ارسال نیست",
                sent: "سفارشی در مسیر ارسال نیست",
                waiting: "سفارشی منتظر تأیید نیست"
            }[key];
            col.innerHTML = `<div class="kh-empty">${emptyText}</div>`;
        }
    });
}

function clearEmptyState(col) {
    const empty = col.querySelector(".kh-empty");
    if (empty) empty.remove();
}

function itemsHtml(order) {
    return (order.items || []).map(i => `🍽 ${i.name} <span>× ${i.quantity}</span>`).join("<br>");
}

function formatMMSS(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

function buildOrderCard(order) {
    const card = document.createElement("div");
    card.id = "order-" + order.id;
    card.className = "order-card " + cardSkin(order.status);
    card.dataset.orderId = order.id;
    fillCardContent(card, order);
    return card;
}

function updateOrderCard(card, order) {
    card.className = "order-card " + cardSkin(order.status)
        + (order.status === "awaiting_confirmation" && order.customer_confirmed === false ? " alert" : "");
    fillCardContent(card, order);
}

function fillCardContent(card, order) {
    const nameLine = order.customer_name ? `<div class="oc-name"><i class="bi bi-person"></i> ${order.customer_name}</div>` : "";

    if (order.status === "new") {
        card.innerHTML = `
            <div class="oc-head"><span class="oc-id">سفارش #${order.id}</span><span class="oc-badge new">جدید</span></div>
            <div class="oc-table"><i class="bi bi-grid-3x3-gap-fill"></i>میز ${order.table}</div>
            ${nameLine}
            <div class="oc-items">${itemsHtml(order)}</div>
            <div class="oc-total">${Number(order.total).toLocaleString()} تومان</div>
            <div class="oc-actions">
                <button class="btn-huge btn-accept" onclick="acceptOrder(${order.id})"><i class="bi bi-check-lg"></i> تأیید</button>
                <button class="btn-huge btn-reject" onclick="rejectOrder(${order.id})"><i class="bi bi-x-lg"></i> رد</button>
            </div>
        `;
        return;
    }

    if (order.status === "cooking") {
        const remaining = order.remaining_seconds != null ? order.remaining_seconds : 0;
        card.innerHTML = `
            <div class="oc-head"><span class="oc-id">سفارش #${order.id}</span><span class="oc-badge cooking">در حال آماده‌سازی</span></div>
            <div class="oc-table"><i class="bi bi-grid-3x3-gap-fill"></i>میز ${order.table}</div>
            ${nameLine}
            <div class="oc-items">${itemsHtml(order)}</div>
            <div class="timer-wrap">
                <button class="timer-btn" onclick="adjustTimer(${order.id}, -5)">−5</button>
                <span class="timer-digits mono" id="timer-${order.id}" data-eta="${order.eta_at || ""}">${formatMMSS(remaining)}</span>
                <button class="timer-btn" onclick="adjustTimer(${order.id}, 5)">+5</button>
            </div>
            <div class="oc-actions">
                <button class="btn-huge btn-dispatch" onclick="markOrderReady(${order.id})"><i class="bi bi-check2-circle"></i> آماده شد</button>
            </div>
        `;
        return;
    }

    if (order.status === "ready") {
        card.innerHTML = `
            <div class="oc-head"><span class="oc-id">سفارش #${order.id}</span><span class="oc-badge ready">آماده تحویل</span></div>
            <div class="oc-table"><i class="bi bi-grid-3x3-gap-fill"></i>میز ${order.table}</div>
            ${nameLine}
            <div class="oc-items">${itemsHtml(order)}</div>
            <div class="oc-total">${Number(order.total).toLocaleString()} تومان</div>
            <div class="dispatch-msg"><i class="bi bi-check2-circle"></i> غذا آماده است — هنوز تحویل نشده</div>
            <div class="oc-actions">
                <button class="btn-huge btn-dispatch" onclick="sendToRobot(${order.id})"><i class="bi bi-robot"></i> ارسال با ربات</button>
            </div>
        `;
        return;
    }

    if (order.status === "sent") {
        card.innerHTML = `
            <div class="oc-head"><span class="oc-id">سفارش #${order.id}</span><span class="oc-badge sent">ارسال‌شده</span></div>
            <div class="oc-table"><i class="bi bi-grid-3x3-gap-fill"></i>میز ${order.table}</div>
            ${nameLine}
            <div class="oc-items">${itemsHtml(order)}</div>
            <div class="dispatch-msg"><i class="bi bi-robot"></i> ربات در مسیر میز ${order.table} — منتظر تأیید تحویل</div>
        `;
        return;
    }

    if (order.status === "awaiting_confirmation") {
        const alertBox = order.customer_confirmed === false ? `
            <div class="waiting-alert">
                <div class="wa-title"><i class="bi bi-exclamation-triangle-fill"></i> مشتری گفته سفارش نرسیده!</div>
                <div class="wa-actions">
                    <button class="btn-huge btn-retry" onclick="resolveIssue(${order.id}, 'retry')"><i class="bi bi-arrow-repeat"></i> دوباره ارسال کن</button>
                    <button class="btn-huge btn-mark-done" onclick="resolveIssue(${order.id}, 'done')"><i class="bi bi-check2"></i> تحویل‌شده علامت بزن</button>
                </div>
            </div>
        ` : `
            <div class="waiting-msg"><i class="bi bi-hourglass-split"></i> در انتظار پاسخ مشتری...</div>
        `;

        card.innerHTML = `
            <div class="oc-head"><span class="oc-id">سفارش #${order.id}</span><span class="oc-badge waiting">منتظر تأیید</span></div>
            <div class="oc-table"><i class="bi bi-grid-3x3-gap-fill"></i>میز ${order.table}</div>
            ${nameLine}
            <div class="oc-items">${itemsHtml(order)}</div>
            ${alertBox}
        `;
    }
}

// ===============================
// Local per-second countdown (purely visual — server is authoritative
// and will broadcast order_updated the moment a timer actually expires)
// ===============================
function tickTimers() {
    document.querySelectorAll(".timer-digits[data-eta]").forEach(el => {
        const eta = el.dataset.eta;
        if (!eta) return;
        const remaining = Math.max(0, Math.floor((new Date(eta).getTime() - Date.now()) / 1000));
        el.textContent = formatMMSS(remaining);
        el.classList.toggle("urgent", remaining <= 30);
    });
}
setInterval(tickTimers, 1000);

// ===============================
// Actions
// ===============================
async function postAction(url, body) {
    try {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrftoken },
            body: JSON.stringify(body || {})
        });
        const data = await res.json();
        if (!data.success) {
            showToast(data.error || "عملیات انجام نشد.", "error");
        } else {
            // بازخورد موفقیت فقط پس از تأیید سرور (نه روی خوش‌بینی)
            showToast(data.message || "وضعیت سفارش به‌روزرسانی شد.", "success");
        }
        return data;
    } catch (e) {
        console.error(e);
        showToast("خطا در ارتباط با سرور.", "error");
        return { success: false };
    }
}

function acceptOrder(id) { postAction(`/api/orders/${id}/accept/`); }
function rejectOrder(id) {
    if (!confirm("رد کردن این سفارش قطعیه؟")) return;
    postAction(`/api/orders/${id}/reject/`);
}
function adjustTimer(id, deltaMinutes) { postAction(`/api/orders/${id}/timer/`, { delta_minutes: deltaMinutes }); }

// غذا آماده شد (تحویل نشده) → ستون «آماده / در حال ارسال»
function markOrderReady(id) { postAction(`/api/orders/${id}/ready/`); }

// ارسال با ربات → وضعیت sent (هنوز تحویل‌شده محسوب نمی‌شود)
function sendToRobot(id) { postAction(`/api/orders/${id}/send/`); }
function resolveIssue(id, action) { postAction(`/api/orders/${id}/resolve/`, { action: action }); }

// ===============================
// Boot
// ===============================
document.addEventListener("DOMContentLoaded", function () {
    updateClock();
    setInterval(updateClock, 1000);
    connectWS();
});