const $ = (id) => document.getElementById(id);

let pollTimer = null;

async function apiFetch(url, options = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...options });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  return res;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

function setHint(text, kind = "") {
  const el = $("addSourceHint");
  if (!el) return;
  el.textContent = text || "";
  el.className = `hint ${kind}`.trim();
}

function fillCategories(categories) {
  const list = $("knownCategories");
  if (!list) return;
  list.innerHTML = "";
  for (const name of categories || []) {
    const opt = document.createElement("option");
    opt.value = name;
    list.appendChild(opt);
  }
}

function sourceLabel(row) {
  if (row.channel) return `@${row.channel}`;
  return row.name || "—";
}

function sourceHref(row) {
  if (row.url) return row.url;
  if (row.channel) return `https://t.me/${row.channel}`;
  return "";
}

function kindLabel(row) {
  if (row.kind === "telegram" || row.channel) return "Telegram";
  return "Web";
}

function renderActiveSources(sources) {
  const status = $("activeListStatus");
  const table = $("activeTable");
  const body = $("activeTableBody");
  if (!status || !table || !body) return;
  body.innerHTML = "";
  if (!sources.length) {
    table.hidden = true;
    status.hidden = false;
    status.textContent = "Нет активных источников.";
    return;
  }
  status.hidden = true;
  table.hidden = false;
  for (const row of sources) {
    const tr = document.createElement("tr");
    const href = sourceHref(row);
    const label = sourceLabel(row);
    const nameCell = href
      ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`
      : escapeHtml(label);
    const badges = [];
    if (row.custom) badges.push('<span class="source-chip-custom">свой</span>');
    tr.innerHTML =
      `<td>${nameCell}${badges.length ? " " + badges.join(" ") : ""}` +
      `<div class="muted-source-name">${escapeHtml(row.name || "")}</div></td>` +
      `<td>${escapeHtml(kindLabel(row))}</td>` +
      `<td>${escapeHtml(row.topic_category || "—")}</td>`;
    const actions = document.createElement("td");
    actions.className = "attachment-actions";
    if (row.custom && row.channel) {
      const parseBtn = document.createElement("button");
      parseBtn.type = "button";
      parseBtn.className = "btn secondary btn-sm";
      parseBtn.textContent = "Парсить вчера";
      parseBtn.addEventListener("click", () => parseChannel(row.channel));
      actions.appendChild(parseBtn);
    }
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn secondary btn-sm";
    delBtn.textContent = "Удалить";
    delBtn.addEventListener("click", () => disableSource(row.name));
    actions.appendChild(delBtn);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

function renderDisabledSources(sources) {
  const status = $("disabledListStatus");
  const table = $("disabledTable");
  const body = $("disabledTableBody");
  if (!status || !table || !body) return;
  body.innerHTML = "";
  if (!sources.length) {
    table.hidden = true;
    status.hidden = false;
    status.textContent = "Отключённых источников нет.";
    return;
  }
  status.hidden = true;
  table.hidden = false;
  for (const row of sources) {
    const tr = document.createElement("tr");
    const href = sourceHref(row);
    const label = sourceLabel(row);
    const nameCell = href
      ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`
      : escapeHtml(label);
    tr.innerHTML =
      `<td>${nameCell}<div class="muted-source-name">${escapeHtml(row.name || "")}</div></td>` +
      `<td>${escapeHtml(kindLabel(row))}</td>` +
      `<td>${escapeHtml(row.topic_category || "—")}</td>`;
    const actions = document.createElement("td");
    actions.className = "attachment-actions";
    const restoreBtn = document.createElement("button");
    restoreBtn.type = "button";
    restoreBtn.className = "btn secondary btn-sm";
    restoreBtn.textContent = "Вернуть";
    restoreBtn.addEventListener("click", () => restoreSource(row.name));
    actions.appendChild(restoreBtn);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

async function loadSources() {
  const res = await apiFetch("/api/custom-sources");
  const data = await res.json();
  fillCategories(data.categories || []);
  renderActiveSources(data.sources || []);
  renderDisabledSources(data.disabled || []);
}

async function pollJob(jobId) {
  const res = await apiFetch(`/api/jobs/${jobId}`);
  const data = await res.json();
  const log = $("parseLog");
  if (log) {
    log.hidden = false;
    log.textContent = (data.logs || []).join("\n");
  }
  if (["completed", "failed", "cancelled"].includes(data.status)) {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (data.status === "completed") {
      setHint("Парсинг завершён. Канал появится в источниках брифа.", "ok");
    } else {
      setHint(data.error || "Парсинг не удался.", "err");
    }
  }
}

function watchJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  $("parseLog").hidden = false;
  $("parseLog").textContent = "Запуск парсинга…";
  pollTimer = setInterval(() => pollJob(jobId), 2000);
  pollJob(jobId);
}

async function parseChannel(channel) {
  setHint(`Парсинг @${channel} за вчера…`);
  const res = await apiFetch(
    `/api/custom-sources/${encodeURIComponent(channel)}/parse`,
    { method: "POST" },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setHint(data.detail || "Не удалось запустить парсинг.", "err");
    return;
  }
  watchJob(data.job_id);
}

async function disableSource(name) {
  const ok = confirm(
    `Отключить источник «${name}»?\n\n` +
      "Он перестанет парситься. Уже сохранённые новости останутся в векторной базе " +
      "и будут доступны в брифе и агенте.",
  );
  if (!ok) return;
  const res = await apiFetch("/api/managed-sources/disable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setHint(data.detail || "Не удалось отключить источник.", "err");
    return;
  }
  setHint(
    `Источник «${name}» отключён. Новости в RAG сохранены.`,
    "ok",
  );
  await loadSources();
}

async function restoreSource(name) {
  const res = await apiFetch("/api/managed-sources/restore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setHint(data.detail || "Не удалось вернуть источник.", "err");
    return;
  }
  setHint(`Источник «${name}» снова в парсинге.`, "ok");
  await loadSources();
}

$("addSourceForm")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("btnAddSource");
  const url = ($("tgUrl")?.value || "").trim();
  const topic = ($("tgCategory")?.value || "").trim();
  if (!url) {
    setHint("Вставьте ссылку на канал.", "err");
    return;
  }
  if (!topic) {
    setHint("Укажите категорию.", "err");
    return;
  }
  if (btn) btn.disabled = true;
  setHint("Добавляю…");
  try {
    const res = await apiFetch("/api/custom-sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        topic_category: topic,
        parse_yesterday: Boolean($("parseYesterday")?.checked),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setHint(data.detail || "Не удалось добавить канал.", "err");
      return;
    }
    $("tgUrl").value = "";
    fillCategories(data.categories || []);
    await loadSources();
    const name = data.channel?.channel || "";
    if (data.parse_job_id) {
      setHint(`Канал @${name} добавлен, парсим вчерашние посты…`, "ok");
      watchJob(data.parse_job_id);
    } else {
      setHint(
        `Канал @${name} добавлен. Новости подтянутся при ночном парсинге.`,
        "ok",
      );
    }
  } catch (err) {
    setHint("Сбой соединения.", "err");
  } finally {
    if (btn) btn.disabled = false;
  }
});

$("btnLogout")?.addEventListener("click", async () => {
  await apiFetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
});

(async function init() {
  try {
    const res = await apiFetch("/api/auth/me");
    const data = await res.json();
    const user = data.user || {};
    $("userName").textContent = user.full_name || user.login || "—";
    await loadSources();
  } catch (e) {
    /* redirect handled in apiFetch */
  }
})();
