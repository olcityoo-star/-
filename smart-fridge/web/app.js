const REFRESH_MS = 10000;

const el = (id) => document.getElementById(id);
const state = { snapshotId: null, busy: false };

async function api(path, options) {
    const response = await fetch(path, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok && !body.error) {
        throw new Error(body.detail || `HTTP ${response.status}`);
    }
    return body;
}

function plural(n, one, few, many) {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
}

function timeAgo(ts) {
    if (!ts) return 'никогда';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (seconds < 60) return 'только что';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} ${plural(minutes, 'минуту', 'минуты', 'минут')} назад`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} ${plural(hours, 'час', 'часа', 'часов')} назад`;
    const days = Math.floor(hours / 24);
    return `${days} ${plural(days, 'день', 'дня', 'дней')} назад`;
}

function freshnessText(item) {
    if (item.expires_in_days === null) return '';
    const days = item.expires_in_days;
    if (days <= 0) {
        const overdue = Math.max(1, Math.round(-days));
        return `просрочен на ${overdue} ${plural(overdue, 'день', 'дня', 'дней')}`;
    }
    if (days < 1) return 'испортится сегодня';
    const left = Math.round(days);
    return `свежесть ещё ${left} ${plural(left, 'день', 'дня', 'дней')}`;
}

function setAlert(message) {
    const node = el('alert');
    if (!message) {
        node.classList.add('hidden');
        return;
    }
    node.textContent = message;
    node.classList.remove('hidden');
}

// --- отрисовка ---------------------------------------------------------

function renderStatus(status) {
    const ok = !status.last_error;
    const detector = { demo: 'демо-режим', yolo: 'YOLO', vlm: status.detector.model }[status.detector.name]
        || status.detector.name;
    el('status-line').innerHTML =
        `<span class="dot ${ok ? 'ok' : 'bad'}"></span>` +
        `${detector} · камера ${status.camera.source} · проверено ${timeAgo(status.last_scan_ts)}`;
    setAlert(status.last_error ? `Не удалось получить данные: ${status.last_error}` : '');

    const interval = status.scan_interval > 0
        ? `автопроверка каждые ${Math.round(status.scan_interval / 60) || 1} мин`
        : 'автопроверка выключена';
    el('footer-info').textContent = `${interval} · всего сканирований: ${status.scan_count}`;

    renderSnapshot(status.snapshot);
}

function renderSnapshot(snapshot) {
    const frame = el('frame');
    if (!snapshot) {
        frame.classList.remove('has-image');
        el('snapshot-time').textContent = '—';
        el('boxes').innerHTML = '';
        el('legend').innerHTML = '';
        return;
    }
    if (snapshot.id !== state.snapshotId) {
        state.snapshotId = snapshot.id;
        el('snapshot').src = `/api/snapshot.jpg?v=${snapshot.id}`;
    }
    frame.classList.add('has-image');
    el('snapshot-time').textContent = timeAgo(snapshot.ts);

    const detections = snapshot.detections || [];
    el('boxes').innerHTML = detections
        .filter((d) => d.box && (d.box[2] - d.box[0]) > 0.001)
        .map((d) => {
            const [x1, y1, x2, y2] = d.box;
            const style = `left:${x1 * 100}%;top:${y1 * 100}%;width:${(x2 - x1) * 100}%;height:${(y2 - y1) * 100}%`;
            return `<div class="box" style="${style}"><span>${d.label} ${Math.round(d.confidence * 100)}%</span></div>`;
        })
        .join('');

    el('legend').innerHTML = detections.length
        ? detections
            .map((d) => `<span class="legend-item">${d.label} · ${Math.round(d.confidence * 100)}%</span>`)
            .join('')
        : '<span class="legend-item">на кадре ничего не распознано</span>';
}

function renderInventory(data) {
    el('total-count').textContent = `${data.total} ${plural(data.total, 'позиция', 'позиции', 'позиций')}`;
    const container = el('inventory');
    if (!data.groups.length) {
        container.innerHTML = '<div class="empty">Холодильник пуст — или камера ещё ничего не увидела.</div>';
        return;
    }
    container.innerHTML = data.groups
        .map((group) => `
            <div>
                <p class="group-title">${group.label}</p>
                <div class="items">${group.items.map(renderItem).join('')}</div>
            </div>`)
        .join('');

    container.querySelectorAll('button[data-key]').forEach((button) => {
        button.addEventListener('click', () => adjust(button.dataset.key, Number(button.dataset.count)));
    });
}

function renderItem(item) {
    const fresh = freshnessText(item);
    const cls = item.freshness === 'expired' ? 'expired' : item.freshness === 'soon' ? 'soon' : '';
    const meta = fresh || `замечено ${timeAgo(item.last_seen)}`;
    return `
        <div class="item ${cls}">
            <span class="item-emoji">${item.emoji}</span>
            <div class="item-body">
                <div class="item-name" title="${item.label}">${item.label}</div>
                <div class="item-meta ${cls}">${meta}</div>
            </div>
            <div class="counter">
                <button data-key="${encodeURIComponent(item.key)}" data-count="${item.count - 1}" title="Убрать">−</button>
                <span class="count">${item.count}</span>
                <button data-key="${encodeURIComponent(item.key)}" data-count="${item.count + 1}" title="Добавить">+</button>
            </div>
        </div>`;
}

function renderShopping(list) {
    const node = el('shopping');
    if (!list.length) {
        node.innerHTML = '<li class="empty">Всё на месте, покупать нечего.</li>';
        return;
    }
    node.innerHTML = list
        .map((row) => `<li><span>${row.emoji}</span><span>${row.label}</span><span class="reason">${row.reason}</span></li>`)
        .join('');
}

const EVENT_TEXT = {
    added: 'появился',
    removed: 'закончился',
    increased: 'стало больше',
    decreased: 'стало меньше',
};

function renderEvents(events) {
    const node = el('events');
    if (!events.length) {
        node.innerHTML = '<li class="empty">Пока ничего не происходило.</li>';
        return;
    }
    node.innerHTML = events
        .slice(0, 25)
        .map((event) => {
            const text = EVENT_TEXT[event.kind] || event.kind;
            const hand = event.source === 'manual' ? ' (вручную)' : '';
            return `<li>
                <span>${event.emoji}</span>
                <span><b>${event.label}</b> <span class="kind-${event.kind}">${text}${hand}</span></span>
                <span class="when">${timeAgo(event.ts)}</span>
            </li>`;
        })
        .join('');
}

// --- действия ----------------------------------------------------------

async function refresh() {
    try {
        const [status, inventory, events] = await Promise.all([
            api('/api/status'),
            api('/api/inventory'),
            api('/api/events?limit=25'),
        ]);
        renderStatus(status);
        renderInventory(inventory);
        renderShopping(inventory.shopping_list);
        renderEvents(events.events);
    } catch (error) {
        setAlert(`Сервер недоступен: ${error.message}`);
    }
}

async function scanNow() {
    if (state.busy) return;
    state.busy = true;
    const button = el('scan-btn');
    button.disabled = true;
    button.querySelector('.btn-label').textContent = 'Смотрю…';
    try {
        const result = await api('/api/scan', { method: 'POST' });
        if (result.error) setAlert(`Не удалось получить данные: ${result.error}`);
        await refresh();
    } catch (error) {
        setAlert(`Ошибка сканирования: ${error.message}`);
    } finally {
        button.disabled = false;
        button.querySelector('.btn-label').textContent = 'Сканировать';
        state.busy = false;
    }
}

async function adjust(encodedKey, count) {
    try {
        await api(`/api/items/${encodedKey}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ count: Math.max(0, count) }),
        });
        await refresh();
    } catch (error) {
        setAlert(`Не удалось изменить количество: ${error.message}`);
    }
}

el('scan-btn').addEventListener('click', scanNow);
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
});
refresh();
setInterval(refresh, REFRESH_MS);
