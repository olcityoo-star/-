const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
const toast = document.getElementById("toast");
let cameraConfigured = false;

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((item) => item.classList.remove("is-active"));
    panels.forEach((panel) => panel.classList.remove("is-active"));
    tab.classList.add("is-active");
    document.getElementById(tab.dataset.tab).classList.add("is-active");
  });
});

function showToast(message) {
  toast.hidden = false;
  toast.textContent = message;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.hidden = true;
  }, 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Ошибка запроса");
  }
  return data;
}

function statusLabel(status) {
  if (status === "in") return "на полке";
  if (status === "maybe_gone") return "возможно съели";
  if (status === "gone") return "нет";
  return status;
}

function renderSummary(summary) {
  document.getElementById("summary").innerHTML = `
    <div class="stat"><b>${summary.inside}</b><span>сейчас внутри</span></div>
    <div class="stat"><b>${summary.maybe_gone}</b><span>под вопросом</span></div>
    <div class="stat"><b>${summary.shopping}</b><span>купить</span></div>
  `;
}

function renderInventory(items) {
  const groups = {};
  for (const item of items) {
    const key = item.category_label || "Другое";
    groups[key] = groups[key] || [];
    groups[key].push(item);
  }
  const root = document.getElementById("inventory");
  if (!items.length) {
    root.innerHTML = `<p class="hint">Пока пусто. Сделайте первый скан или добавьте продукт вручную.</p>`;
    return;
  }
  root.innerHTML = Object.entries(groups)
    .map(
      ([title, rows]) => `
      <section class="shelf-group">
        <h2>${title}</h2>
        <div class="cards">
          ${rows
            .map(
              (item) => `
            <article class="card ${item.status === "gone" ? "gone" : item.status === "maybe_gone" ? "maybe" : ""}">
              <div class="emoji">${item.emoji}</div>
              <h3>${item.name}</h3>
              <p>${item.count} шт · ${statusLabel(item.status)}</p>
            </article>`
            )
            .join("")}
        </div>
      </section>`
    )
    .join("");
}

function renderScan(lastScan, events) {
  const view = document.getElementById("viewfinder");
  if (!lastScan) {
    view.innerHTML = `<p class="hint">Ещё не было скана. Откройте дверь и нажмите «Скан» или загрузите фото.</p>`;
  } else {
    view.innerHTML = `
      <img id="scan-image" src="${lastScan.image_url}?t=${lastScan.id}" alt="Последний кадр холодильника">
      <div class="boxes" id="scan-boxes"></div>
    `;
    const image = document.getElementById("scan-image");
    const drawBoxes = () => {
      const width = image.naturalWidth || 560;
      const height = image.naturalHeight || 560;
      document.getElementById("scan-boxes").innerHTML = lastScan.detections
        .filter((item) => item.box && item.box.x2)
        .map((item) => {
          const boxWidth = Math.max(item.box.x2 - item.box.x1, 1);
          const boxHeight = Math.max(item.box.y2 - item.box.y1, 1);
          return `<div class="box" style="left:${(item.box.x1 / width) * 100}%;top:${(item.box.y1 / height) * 100}%;width:${(boxWidth / width) * 100}%;height:${(boxHeight / height) * 100}%"><span>${item.emoji} ${item.name}</span></div>`;
        })
        .join("");
    };
    if (image.complete) drawBoxes();
    else image.addEventListener("load", drawBoxes);
  }
  document.getElementById("events").innerHTML = events
    .map((event) => `<li>${event.message}</li>`)
    .join("") || "<li>История появится после сканов</li>";
}

function renderShopping(rows) {
  const root = document.getElementById("shopping");
  if (!rows.length) {
    root.innerHTML = `<p class="hint">Список покупок пуст — по сканам всё на месте.</p>`;
    return;
  }
  root.innerHTML = rows
    .map((row) => `<div class="row-item">${row.emoji} ${row.name} · ${row.reason}</div>`)
    .join("");
}

function fillSettings(settings) {
  const form = document.getElementById("settings-form");
  form.detector.value = settings.detector || "demo";
  form.camera_url.placeholder = settings.camera_url_masked || form.camera_url.placeholder;
  form.ollama_url.value = settings.ollama_url || "";
  form.ollama_model.value = settings.ollama_model || "";
  form.openai_model.value = settings.openai_model || "";
  document.getElementById("settings-status").textContent = settings.camera_configured
    ? `Камера задана: ${settings.camera_url_masked}`
    : "Камера ещё не подключена";
}

async function loadState() {
  const state = await api("/api/state");
  renderSummary(state.summary);
  renderInventory(state.inventory);
  renderScan(state.last_scan, state.events);
  renderShopping(state.shopping);
  fillSettings(state.settings);
  cameraConfigured = Boolean(state.settings.camera_configured);
  return state;
}

async function runScan(path, options) {
  const button = document.getElementById("scan-camera");
  button.classList.add("is-busy");
  try {
    await api(path, options);
    await loadState();
    document.querySelector('[data-tab="scan"]').click();
    showToast("Скан готов");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.classList.remove("is-busy");
  }
}

document.getElementById("scan-camera").addEventListener("click", () => {
  if (cameraConfigured) {
    runScan("/api/scans/camera", { method: "POST" });
    return;
  }
  showToast("Камера ещё не задана — показываю демо");
  runScan("/api/scans/demo", { method: "POST" });
});

document.getElementById("scan-demo").addEventListener("click", () => {
  runScan("/api/scans/demo", { method: "POST" });
});

document.getElementById("upload").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  await runScan("/api/scans/upload", { method: "POST", body });
  event.target.value = "";
});

document.getElementById("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const name = form.name.value.trim();
  try {
    await api("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, count: 1 }),
    });
    form.reset();
    await loadState();
  } catch (error) {
    showToast(error.message);
  }
});

document.getElementById("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const payload = Object.fromEntries(new FormData(form).entries());
  Object.keys(payload).forEach((key) => {
    if (payload[key] === "") delete payload[key];
  });
  try {
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadState();
    showToast("Настройки сохранены");
  } catch (error) {
    showToast(error.message);
  }
});

loadState().catch((error) => showToast(error.message));

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
