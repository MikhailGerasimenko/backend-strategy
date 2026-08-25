const $ = (id) => document.getElementById(id);

let pollTimer = None;

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

function renderChannels(channels) {
  const status = $("customListStatus");
  const table = $("customTable");
  const body = $("customTableBody");
  if (!status || !table || !body) return;
  body.innerHTML = "";
  if (!channels.length) {
    table.hidden = true;
    status.hidden = false;
    status.textContent = "Пока никто не добавлял свои каналы.";
    return;
  }
  status.hidden = true;
  table.hidden = false;
  for (const row of channels) {
    const tr = document.createElement("tr");
    const url = row.url || `https://t.me/${row.channel}`;
    tr.innerHTML =
      `<td><a href="${escapeHtml(url)}" target="_blank" rel="noopener">@${escapeHtml(row.channel)}</a></td>` +
      `<td>${escapeHtml(row.topic_category || "—")}</td>` +
      `<td>${escapeHtml(row.added_by || "—")}</td>`;
    const actions = document.createElement("td");
    actions.className = "attachment-actions";
    const parseBtn = document.createElement("button");
    parseBtn.type = "button";
    parseBtn.className = "btn secondary btn-sm";
    parseBtn.textContent = "Парсить вчера";
    parseBtn.addEventListener("click", () => parseChannel(row.channel));
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn secondary btn-sm";
    delBtn.textContent = "Удалить";
    delBtn.addEventListener("click", () => deleteChannel(row.channel));
    actions.append(parseBtn, delBtn);
    tr.appendChild(actions);
    body.appendChild(tr);
  }
}

async function loadChannels() {
  const res = await apiFetch("/api/custom-sources");
  const data = await res.json();
  fillCategories(data.categories || []);
  renderChannels(data.channels || []);
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

async function deleteChannel(channel) {
  if (!confirm(`Удалить канал @${channel} из своих источников?`)) return;
  const res = await apiFetch(`/api/custom-sources/${encodeURIComponent(channel)}`, {
    method: "DELETE",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    setHint(data.detail || "Не удалось удалить.", "err");
    return;
  }
  setHint(`Канал @${channel} удалён.`);
  await loadChannels();
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
    await loadChannels();
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
    await loadChannels();
  } catch (e) {
    /* redirect handled in apiFetch */
  }
})();
