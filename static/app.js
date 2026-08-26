const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  items: [],
  shop: [],
  scan: null,
  filter: "all",
  settings: {},
};

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 4200);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || data.message || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function daysUntil(iso) {
  if (!iso) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const date = new Date(`${iso}T00:00:00`);
  return Math.round((date - today) / 86400000);
}

function expiryLabel(iso) {
  const days = daysUntil(iso);
  if (days === null) return { text: "без срока", cls: "" };
  if (days < 0) return { text: `просрочено ${-days} дн.`, cls: "bad" };
  if (days === 0) return { text: "истекает сегодня", cls: "warn" };
  if (days <= 3) return { text: `ещё ${days} дн.`, cls: "warn" };
  return { text: `до ${iso}`, cls: "" };
}

function renderStats() {
  const soon = state.items.filter((item) => {
    const d = daysUntil(item.expires_on);
    return d !== null && d >= 0 && d <= 3;
  }).length;
  $("#statCount").textContent = state.items.length;
  $("#statSoon").textContent = soon;
  $("#statShop").textContent = state.shop.length;
}

function renderItems() {
  const list = $("#itemList");
  const empty = $("#emptyState");
  const filtered = state.items.filter((item) => {
    if (state.filter === "soon") {
      const d = daysUntil(item.expires_on);
      return d !== null && d <= 3;
    }
    if (state.filter === "shop") {
      return state.shop.some((row) => row.id === item.id);
    }
    if (state.filter === "scan") return item.source === "scan";
    return true;
  });
  list.innerHTML = filtered.map((item) => {
    const exp = expiryLabel(item.expires_on);
    const cls = daysUntil(item.expires_on) < 0 ? "expired" : daysUntil(item.expires_on) <= 3 && item.expires_on ? "soon" : "";
    return `
      <article class="card ${cls}" data-id="${item.id}">
        <div>
          <div class="tag">${item.category} · ${item.source === "scan" ? "скан" : "вручную"}</div>
          <h4>${escapeHtml(item.name)}</h4>
          <p>${item.quantity} ${escapeHtml(item.unit)}${item.notes ? " · " + escapeHtml(item.notes) : ""}</p>
        </div>
        <div class="card-actions">
          <span class="when ${exp.cls}">${exp.text}</span>
          <button class="icon-btn" data-edit="${item.id}" type="button">изменить</button>
          <button class="icon-btn" data-del="${item.id}" type="button">убрать</button>
        </div>
      </article>`;
  }).join("");
  empty.hidden = filtered.length > 0;
  renderStats();
}

function renderShop() {
  const list = $("#shopList");
  const empty = $("#shopEmpty");
  list.innerHTML = state.shop.map((item) => `
    <article class="card soon">
      <div>
        <div class="tag">${escapeHtml(item.reason || "купить")}</div>
        <h4>${escapeHtml(item.name)}</h4>
        <p>${item.quantity || 1} ${escapeHtml(item.unit || "шт")}</p>
      </div>
    </article>`).join("");
  empty.hidden = state.shop.length > 0;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setCameraChip(online, text) {
  const chip = $("#camChip");
  chip.dataset.state = online === true ? "online" : online === false ? "offline" : "unknown";
  $("#camChipText").textContent = text;
}

function syncDetectionsFromPlan() {
  const scan = state.scan;
  if (!scan) return;
  const plan = scan.sync || { kept: [], added: [], removed: [] };
  const merged = [...(plan.kept || []), ...(plan.added || [])];
  // Preserve user edits already on detections when re-rendering
  if (!scan.detections?.length || merged.length) {
    scan.detections = merged.map((det, index) => ({
      ...det,
      id: det.id || index + 1,
      accepted: det.accepted !== false,
    }));
  }
  scan.removed = (plan.removed || []).map((row) => ({
    ...row,
    remove: row.remove !== false,
  }));
}

function renderScan() {
  const scan = state.scan;
  const img = $("#scanImage");
  const svg = $("#boxes");
  const stage = $("#stage");
  const note = $("#detectNote");
  const confirmBtn = $("#confirmBtn");
  const summary = $("#syncSummary");
  if (!scan) {
    img.removeAttribute("src");
    stage.classList.remove("has-image");
    svg.innerHTML = "";
    $("#detectionList").innerHTML = "";
    $("#removedList").innerHTML = "";
    confirmBtn.hidden = true;
    note.hidden = true;
    summary.hidden = true;
    return;
  }
  syncDetectionsFromPlan();
  img.src = scan.image_url;
  stage.classList.add("has-image");
  $("#scanMeta").textContent = new Date(scan.created_at).toLocaleString("ru-RU");
  if (scan.detect_error) {
    note.hidden = false;
    note.textContent = `YOLO пока не сработал: ${scan.detect_error}. Снимок сохранён — можно подписать продукты вручную или скачать модель.`;
  } else {
    note.hidden = true;
  }
  const plan = scan.sync || { summary: { kept: 0, added: 0, removed: 0 } };
  summary.hidden = false;
  summary.innerHTML = `
    <span class="pill keep">осталось ${plan.summary?.kept || 0}</span>
    <span class="pill add">новое ${plan.summary?.added || 0}</span>
    <span class="pill rem">пропало ${plan.summary?.removed || 0}</span>`;
  img.onload = () => drawBoxes(scan, img, svg);
  renderDetections();
  renderRemoved();
  confirmBtn.hidden = !(scan.detections?.length || scan.removed?.length);
}

function drawBoxes(scan, img, svg) {
  const w = img.naturalWidth || 1;
  const h = img.naturalHeight || 1;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.innerHTML = (scan.detections || []).map((det) => {
    if (!det.accepted || !det.bbox) return "";
    const b = det.bbox;
    const bw = Math.max(1, b.x2 - b.x1);
    const bh = Math.max(1, b.y2 - b.y1);
    const color = det.sync_action === "add" ? "#8af0e0" : "#7de4d4";
    return `<g>
      <rect x="${b.x1}" y="${b.y1}" width="${bw}" height="${bh}" fill="none" stroke="${color}" stroke-width="${Math.max(2, w / 240)}"/>
      <text x="${b.x1 + 4}" y="${Math.max(14, b.y1 - 6)}" fill="${color}" font-size="${Math.max(12, w / 42)}" font-family="Manrope">${escapeHtml(det.name)} ${Math.round((det.confidence || 0) * 100)}%</text>
    </g>`;
  }).join("");
}

function renderDetections() {
  const scan = state.scan;
  const box = $("#detectionList");
  if (!scan?.detections?.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = scan.detections.map((det, i) => {
    const action = det.sync_action === "keep" ? "осталось" : "новое";
    const ocr = det.expires_on ? `срок ${det.expires_on}` : (det.ocr_text ? "есть OCR" : "без OCR");
    return `
    <label class="det">
      <input type="checkbox" data-acc="${i}" ${det.accepted === false ? "" : "checked"}>
      <div>
        <input type="text" data-name="${i}" value="${escapeHtml(det.name)}">
        <div class="mono subtle">${action} · ${Math.round((det.confidence || 0) * 100)}% · ${ocr}</div>
        <input type="date" data-exp="${i}" value="${det.expires_on || ""}">
      </div>
      <span class="mono">${det.sync_action === "keep" ? "✓" : "+"}</span>
    </label>`;
  }).join("");
}

function renderRemoved() {
  const scan = state.scan;
  const box = $("#removedList");
  if (!scan?.removed?.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `<p class="subhead">Похоже, пропало с полок</p>` + scan.removed.map((row, i) => `
    <label class="det rem-det">
      <input type="checkbox" data-rem="${i}" ${row.remove === false ? "" : "checked"}>
      <div>
        <strong>${escapeHtml(row.name)}</strong>
        <div class="mono subtle">убрать из инвентаря</div>
      </div>
      <span class="mono">−</span>
    </label>`).join("");
}

async function loadItems() {
  state.items = await api("/api/items");
  renderItems();
}

async function loadShop() {
  const data = await api("/api/shopping");
  state.shop = data.items || [];
  renderShop();
  renderStats();
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  const form = $("#settingsForm");
  for (const [key, value] of Object.entries(state.settings)) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === "checkbox") field.checked = value === "1" || value === true;
    else field.value = value;
  }
}

async function probeCamera() {
  setCameraChip(null, "проверка…");
  try {
    const data = await api("/api/camera/status");
    setCameraChip(data.online, data.online ? `${data.width}×${data.height}` : "нет связи");
    if (!data.online) toast(data.message);
    return data;
  } catch (err) {
    setCameraChip(false, "ошибка");
    toast(err.message);
  }
}

async function runScan(kind, file) {
  const scanBtn = $("#scanBtn");
  scanBtn.disabled = true;
  scanBtn.textContent = "Сканирую…";
  try {
    let scan;
    if (kind === "upload") {
      const body = new FormData();
      body.append("file", file);
      scan = await api("/api/scan/upload", { method: "POST", body });
    } else {
      scan = await api("/api/scan", { method: "POST" });
    }
    state.scan = scan;
    renderScan();
    const s = scan.sync?.summary || {};
    toast(`Скан: осталось ${s.kept || 0}, новое ${s.added || 0}, пропало ${s.removed || 0}`);
  } catch (err) {
    toast(err.message);
  } finally {
    scanBtn.disabled = false;
    scanBtn.textContent = "Сканировать камерой";
  }
}

function openItemDialog(item) {
  const dialog = $("#itemDialog");
  const form = $("#itemForm");
  $("#dialogTitle").textContent = item ? "Изменить продукт" : "Новый продукт";
  form.reset();
  form.elements.id.value = item?.id || "";
  if (item) {
    form.elements.name.value = item.name;
    form.elements.quantity.value = item.quantity;
    form.elements.unit.value = item.unit;
    form.elements.category.value = item.category;
    form.elements.expires_on.value = item.expires_on || "";
    form.elements.notes.value = item.notes || "";
  }
  dialog.showModal();
}

function bind() {
  $("#scanBtn").addEventListener("click", () => runScan("camera"));
  $("#uploadInput").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file) runScan("upload", file);
    event.target.value = "";
  });
  $("#addBtn").addEventListener("click", () => openItemDialog(null));
  $("#probeBtn").addEventListener("click", probeCamera);
  $("#confirmBtn").addEventListener("click", async () => {
    if (!state.scan) return;
    try {
      const removeIds = (state.scan.removed || [])
        .filter((row) => row.remove !== false)
        .map((row) => row.id);
      const result = await api(`/api/scans/${state.scan.id}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          detections: state.scan.detections,
          remove_item_ids: removeIds,
          mode: "sync",
        }),
      });
      state.scan = result.scan;
      await Promise.all([loadItems(), loadShop()]);
      toast(`Готово: +${result.created.length}, обновлено ${result.updated.length}, убрано ${result.removed.length}`);
    } catch (err) {
      toast(err.message);
    }
  });

  $("#itemList").addEventListener("click", async (event) => {
    const edit = event.target.dataset.edit;
    const del = event.target.dataset.del;
    if (edit) {
      const item = state.items.find((row) => String(row.id) === String(edit));
      openItemDialog(item);
    }
    if (del) {
      if (!confirm("Убрать продукт с полок?")) return;
      await api(`/api/items/${del}`, { method: "DELETE" });
      await Promise.all([loadItems(), loadShop()]);
    }
  });

  $("#detectionList").addEventListener("input", (event) => {
    const acc = event.target.dataset.acc;
    const name = event.target.dataset.name;
    const exp = event.target.dataset.exp;
    if (acc != null) state.scan.detections[acc].accepted = event.target.checked;
    if (name != null) state.scan.detections[name].name = event.target.value;
    if (exp != null) state.scan.detections[exp].expires_on = event.target.value || null;
    drawBoxes(state.scan, $("#scanImage"), $("#boxes"));
  });

  $("#removedList").addEventListener("input", (event) => {
    const rem = event.target.dataset.rem;
    if (rem != null) state.scan.removed[rem].remove = event.target.checked;
  });

  $$(".filters .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $$(".filters .chip").forEach((c) => c.classList.remove("on"));
      chip.classList.add("on");
      state.filter = chip.dataset.filter;
      renderItems();
    });
  });

  $("#itemForm").addEventListener("submit", async (event) => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      name: form.elements.name.value.trim(),
      quantity: Number(form.elements.quantity.value || 1),
      unit: form.elements.unit.value.trim() || "шт",
      category: form.elements.category.value,
      expires_on: form.elements.expires_on.value || null,
      notes: form.elements.notes.value.trim(),
    };
    const id = form.elements.id.value;
    try {
      if (id) await api(`/api/items/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      else await api("/api/items", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      $("#itemDialog").close();
      await Promise.all([loadItems(), loadShop()]);
    } catch (err) {
      toast(err.message);
    }
  });

  $("#settingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      camera_name: form.elements.camera_name.value,
      camera_host: form.elements.camera_host.value,
      stream_url: form.elements.stream_url.value,
      snapshot_url: form.elements.snapshot_url.value,
      confidence: form.elements.confidence.value,
      food_only: form.elements.food_only.checked ? "1" : "0",
    };
    try {
      state.settings = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Настройки камеры сохранены");
    } catch (err) {
      toast(err.message);
    }
  });

  $("#modelBtn").addEventListener("click", async () => {
    $("#modelBtn").disabled = true;
    try {
      const info = await api("/api/model/download", { method: "POST" });
      toast(`Модель готова (${Math.round(info.bytes / 1024 / 1024)} МБ)`);
    } catch (err) {
      toast(err.message);
    } finally {
      $("#modelBtn").disabled = false;
    }
  });
}

async function boot() {
  bind();
  await Promise.all([loadItems(), loadSettings(), loadShop()]);
  try {
    state.scan = await api("/api/scans/latest");
    renderScan();
  } catch {
    renderScan();
  }
  probeCamera();
}

boot();
