const $ = (id) => document.getElementById(id);

let pollTimer = null;
let activeJobId = null;
let isAdmin = false;
let selectedBriefKind = "full";

const BRIEF_KIND_LABELS = {
  full: "полный",
  market: "рыночный",
  corporate: "корпоративный",
};

const fetchOpts = { credentials: "same-origin" };

async function apiFetch(url, options = {}) {
  const res = await fetch(url, { ...fetchOpts, ...options });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  return res;
}

async function loadCurrentUser() {
  try {
    const res = await apiFetch("/api/auth/me");
    const data = await res.json();
    const user = data.user || {};
    const name = user.full_name || user.login || "—";
    $("userName").textContent = user.is_admin ? `${name} (админ)` : name;
    isAdmin = Boolean(user.is_admin);
    $("adminPanel").hidden = !isAdmin;
  } catch (e) {
    if (e.message !== "unauthorized") {
      $("userName").textContent = "—";
    }
  }
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  window.location.href = "/login";
}

function yesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

function setButtonsDisabled(disabled) {
  ["btnBrief", "btnCheckDay", "btnLoadFull", "btnLoadMarket", "btnLoadCorporate"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = disabled;
  });
  if (isAdmin) {
    ["btnUploadData", "btnDeleteData"].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = disabled;
    });
  }
}

function formatKallanishStatus(data) {
  const el = $("kallanishStatus");
  if (!el) return;
  if (!data || !data.has_file) {
    el.textContent = "Kallanish не загружен. Администратор может загрузить .docx выше.";
    el.className = "kallanish-status missing";
    return;
  }
  const modified = data.modified_at
    ? new Date(data.modified_at).toLocaleString("ru-RU")
    : "—";
  el.textContent =
    `Загружен: ${data.filename} · ${data.size_kb} КБ · ~${data.text_chars.toLocaleString("ru-RU")} символов · ${modified}`;
  el.className = "kallanish-status ready";
}

async function loadKallanishStatus() {
  if (!isAdmin) return;
  try {
    const res = await apiFetch("/api/kallanish");
    const data = await res.json();
    formatKallanishStatus(data);
  } catch {
    const el = $("kallanishStatus");
    if (el) {
      el.textContent = "Не удалось проверить файл Kallanish.";
      el.className = "kallanish-status missing";
    }
  }
}

async function uploadKallanishFile(file, hintEl, reportDate) {
  if (!file.name.toLowerCase().endsWith(".docx")) {
    throw new Error("Kallanish: нужен файл .docx");
  }
  const form = new FormData();
  form.append("file", file);
  if (reportDate) {
    form.append("date_str", reportDate);
  }
  const res = await apiFetch("/api/kallanish/upload", { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || `Ошибка ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  formatKallanishStatus(data);
  return data;
}

async function uploadNewsJsonlFile(date, file) {
  const form = new FormData();
  form.append("date_str", date);
  form.append("file", file);
  const res = await apiFetch("/api/news/upload", { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || `Ошибка ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function uploadData() {
  const hint = $("dataUploadHint");
  const date = $("uploadNewsDate").value;
  const jsonlInput = $("newsJsonlFile");
  const kallanishInput = $("kallanishFile");

  if (!date) {
    hint.textContent = "Укажите дату новостей.";
    hint.className = "upload-hint err";
    return;
  }
  const hasJsonl = jsonlInput.files && jsonlInput.files.length;
  const hasKallanish = kallanishInput.files && kallanishInput.files.length;
  if (!hasJsonl && !hasKallanish) {
    hint.textContent = "Выберите JSONL после парсинга (и при необходимости Kallanish).";
    hint.className = "upload-hint err";
    return;
  }

  $("btnUploadData").disabled = true;
  hint.textContent = "Загрузка…";
  hint.className = "upload-hint";

  const messages = [];
  try {
    if (hasJsonl) {
      const data = await uploadNewsJsonlFile(date, jsonlInput.files[0]);
      messages.push(`JSONL: ${data.news_count} новостей → ${data.uploaded_as}`);
      jsonlInput.value = "";
      $("reportDate").value = date;
    }
    if (hasKallanish) {
      const data = await uploadKallanishFile(kallanishInput.files[0], hint, date);
      const ragNote = data.rag?.indexed
        ? `, RAG: ${data.rag.chunks} чанков`
        : data.rag?.skipped
          ? ", RAG: уже был"
          : "";
      messages.push(`Kallanish: ${data.uploaded_as || "сохранён"}${ragNote}`);
      kallanishInput.value = "";
    }
    hint.textContent = messages.join(" · ");
    hint.className = "upload-hint ok";
    await loadAvailableDates();
    await renderLoadedDataTable();
    checkDay();
  } catch (err) {
    hint.textContent = err.message || "Ошибка загрузки.";
    hint.className = "upload-hint err";
  } finally {
    $("btnUploadData").disabled = false;
  }
}

async function deleteDataForDate(date, includeBriefs = true) {
  const params = new URLSearchParams({
    date_str: date,
    include_briefs: includeBriefs ? "true" : "false",
  });
  const res = await apiFetch(`/api/news/data?${params}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || `Ошибка ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function renderLoadedDataTable() {
  if (!isAdmin) return;
  const tbody = $("loadedDataBody");
  if (!tbody) return;

  try {
    const res = await apiFetch("/api/news/dates");
    const data = await res.json();
    const dates = data.dates || [];
    tbody.innerHTML = "";

    if (!dates.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-row">Нет загруженных данных</td></tr>';
      return;
    }

    dates.forEach((item) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${item.date}</td>
        <td>${item.news_count}</td>
        <td><code>${item.filename || "—"}</code></td>
        <td class="actions-cell"></td>
      `;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn danger small";
      btn.textContent = "Удалить";
      btn.addEventListener("click", () => confirmDeleteDate(item.date));
      tr.querySelector(".actions-cell").appendChild(btn);
      tbody.appendChild(tr);
    });
  } catch {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-row">Не удалось загрузить список</td></tr>';
  }
}

async function confirmDeleteDate(date) {
  const includeBriefs = $("deleteIncludeBriefs")?.checked ?? true;
  const briefsNote = includeBriefs ? " и сгенерированные брифы" : "";
  if (!confirm(`Удалить данные за ${date}${briefsNote}?`)) return;

  const hint = $("deleteDataHint");
  hint.textContent = "Удаление…";
  hint.className = "upload-hint";

  try {
    const data = await deleteDataForDate(date, includeBriefs);
    hint.textContent = `Удалено файлов: ${data.deleted_count} (${data.deleted_files.join(", ")})`;
    hint.className = "upload-hint ok";
    await loadAvailableDates();
    await renderLoadedDataTable();
    checkDay();
  } catch (err) {
    hint.textContent = err.message || "Ошибка удаления.";
    hint.className = "upload-hint err";
  }
}

async function deleteDataByPicker() {
  const date = $("deleteDataDate").value;
  if (!date) {
    $("deleteDataHint").textContent = "Выберите дату для удаления.";
    $("deleteDataHint").className = "upload-hint err";
    return;
  }
  await confirmDeleteDate(date);
}

async function loadConfig() {
  try {
    const res = await apiFetch("/api/health");
    const data = await res.json();
    const badge = $("apiBadge");
    if (data.openrouter_configured) {
      badge.textContent = "OpenRouter: настроен";
      badge.className = "badge ok";
    } else {
      badge.textContent = "OpenRouter: нет ключа в .env";
      badge.className = "badge err";
    }
    $("modelHint").textContent = `OpenRouter: ${data.openrouter_configured ? "OK" : "нет ключа"}`;
    const modelEl = $("modelDisplay");
    if (modelEl) {
      modelEl.textContent = data.default_model || "—";
    }
    isAdmin = Boolean(data.is_admin);
    $("adminPanel").hidden = !isAdmin;
    if (isAdmin) {
      loadKallanishStatus();
      renderLoadedDataTable();
    }
  } catch {
    $("apiBadge").textContent = "Сервер недоступен";
    $("apiBadge").className = "badge err";
  }
}

function updatePromptKindHint() {
  const hint = $("promptKindHint");
  if (!hint) return;
  const label = BRIEF_KIND_LABELS[selectedBriefKind] || selectedBriefKind;
  hint.textContent = `Тип брифа: ${label}`;
}

async function loadPromptVariant(variant) {
  try {
    const res = await apiFetch(`/api/default-prompt?variant=${encodeURIComponent(variant)}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    $("systemPrompt").value = data.prompt || "";
    selectedBriefKind = data.variant || variant || "full";
    updatePromptKindHint();
  } catch (err) {
    $("apiBadge").textContent = "Не удалось загрузить промпт";
    $("apiBadge").className = "badge err";
    console.error("loadPromptVariant", err);
  }
}

async function loadAvailableDates() {
  try {
    const res = await apiFetch("/api/news/dates");
    const data = await res.json();
    const list = $("availableDates");
    list.innerHTML = "";
    const dates = data.dates || [];
    dates.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.date;
      opt.label = `${item.date} (${item.news_count} новостей)`;
      list.appendChild(opt);
    });
    if (dates.length && !$("reportDate").value) {
      $("reportDate").value = dates[0].date;
    }
  } catch {
    /* ignore */
  }
}

async function checkDay() {
  const date = $("reportDate").value;
  if (!date) return;
  const res = await apiFetch(`/api/day-status?date_str=${encodeURIComponent(date)}`);
  const data = await res.json();
  const info = $("dayInfo");
  if (data.ready_for_brief) {
    info.textContent =
      `Готово к генерации: ${data.news_count} новостей (${data.jsonl_filename || "JSONL"})` +
      (data.has_kallanish ? ` · Kallanish: ${data.kallanish_file}` : " · Kallanish: нет");
    info.className = "day-info ready";
  } else if (data.has_news) {
    info.textContent = `Файл есть, но новостей: ${data.news_count}. Обратитесь к администратору.`;
    info.className = "day-info warn";
  } else {
    info.textContent =
      `Новостей за ${data.date} нет. Администратор должен загрузить JSONL за этот день.`;
    info.className = "day-info missing";
  }
}

function getPayload() {
  return {
    date: $("reportDate").value,
    system_prompt: $("systemPrompt").value.trim(),
    model: null,
    relevant_only: $("relevantOnly").checked,
    include_kallanish: $("includeKallanish").checked,
    skip_parse: true,
    brief_kind: selectedBriefKind,
  };
}

function validatePrompt() {
  const p = $("systemPrompt").value.trim();
  if (p.length < 50) {
    alert("Системный промпт слишком короткий (минимум 50 символов).");
    return false;
  }
  return true;
}

function showProgress() {
  $("progressCard").hidden = false;
  $("btnDownload").hidden = true;
  $("jobLog").textContent = "";
  $("jobStatus").textContent = "Запуск…";
  $("progressBar").className = "progress-bar running";
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollJob(jobId) {
  const res = await apiFetch(`/api/jobs/${jobId}`);
  const job = await res.json();
  $("jobLog").textContent = (job.logs || []).join("\n");
  $("jobStatus").textContent = statusLabel(job.status);

  const bar = $("progressBar");
  if (job.status === "running" || job.status === "pending") {
    bar.className = "progress-bar running";
  } else if (job.status === "completed") {
    bar.className = "progress-bar done";
    stopPolling();
    setButtonsDisabled(false);
    if (job.docx_path || job.docx_filename) {
      $("btnDownload").href = `/api/jobs/${jobId}/download`;
      $("btnDownload").hidden = false;
    }
    checkDay();
  } else if (job.status === "failed") {
    bar.className = "progress-bar failed";
    stopPolling();
    setButtonsDisabled(false);
    if (job.error) {
      $("jobStatus").textContent = `Ошибка: ${job.error}`;
    }
  }
}

function statusLabel(s) {
  const map = {
    pending: "В очереди…",
    running: "Выполняется…",
    completed: "Готово",
    failed: "Ошибка",
  };
  return map[s] || s;
}

async function startBrief() {
  if (!validatePrompt()) return;
  const payload = getPayload();
  if (!payload.date) {
    alert("Выберите дату.");
    return;
  }

  setButtonsDisabled(true);
  showProgress();
  stopPolling();

  const res = await apiFetch("/api/jobs/brief", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    $("jobStatus").textContent = err.detail || `Ошибка ${res.status}`;
    $("progressBar").className = "progress-bar failed";
    setButtonsDisabled(false);
    return;
  }

  const data = await res.json();
  activeJobId = data.job_id;
  pollTimer = setInterval(() => pollJob(activeJobId), 2000);
  pollJob(activeJobId);
}

async function init() {
  $("reportDate").value = yesterdayISO();
  $("uploadNewsDate").value = yesterdayISO();
  $("deleteDataDate").value = yesterdayISO();
  try {
    await loadCurrentUser();
    await loadConfig();
    await loadPromptVariant("full");
    await loadAvailableDates();
    if (isAdmin) {
      await renderLoadedDataTable();
    }
    await checkDay();
  } catch (err) {
    console.error("init", err);
    $("apiBadge").textContent = "Ошибка загрузки страницы";
    $("apiBadge").className = "badge err";
  }
}

function bindClick(id, handler) {
  const el = $(id);
  if (el) el.addEventListener("click", handler);
}

bindClick("btnLoadFull", () => loadPromptVariant("full"));
bindClick("btnLoadMarket", () => loadPromptVariant("market"));
bindClick("btnLoadCorporate", () => loadPromptVariant("corporate"));
bindClick("btnCheckDay", checkDay);
bindClick("btnBrief", startBrief);
bindClick("btnLogout", logout);
bindClick("btnUploadData", uploadData);
bindClick("btnDeleteData", deleteDataByPicker);

init();
