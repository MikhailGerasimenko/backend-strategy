const $ = (id) => document.getElementById(id);

let pollTimer = null;
let activeJobId = null;
let selectedBriefKind = "full";
const MONTHLY_BRIEF_KINDS = new Set([
  "monthly",
  "monthly_news",
  "monthly_market",
  "monthly_corporate",
]);

function isMonthlyBriefKind(kind = selectedBriefKind) {
  return MONTHLY_BRIEF_KINDS.has(kind);
}
let availableSources = [];
let selectedSourceNames = new Set();
let availableAttachmentIds = [];
let attachmentMetaById = {};
let briefKindMatch = {
  full: ["news", "market", "all"],
  monthly: ["news", "market", "all"],
  monthly_news: ["news", "all"],
  monthly_market: ["market", "all"],
  monthly_corporate: ["market", "all"],
  market: ["market", "all"],
  corporate: ["news", "all"],
};
let briefLabels = {
  news: "Новостной",
  market: "Рыночный",
  all: "Все типы",
};

const BRIEF_KIND_LABELS = {
  full: "полный",
  market: "рыночный",
  corporate: "новостной",
  monthly: "полный ежемесячный",
  monthly_news: "ежемесячный новостной",
  monthly_market: "ежемесячный рыночный",
  monthly_corporate: "ежемесячный рыночный",
};

/** Порядок категорий с листа health (колонка «Категория») в metallurgy_news Excel. */
const TOPIC_GROUP_ORDER = [
  "Металлургия РФ",
  "Металлургия мира",
  "Китай",
  "Макроэкономика РФ",
  "Макроэкономика мира",
  "Документы RAG",
];
const DOC_SOURCE_NAMES = new Set(["Kallanish", "PDF отчёт", "PMI"]);
const KALLANISH_SOURCE = "Kallanish";
const PDF_SOURCE = "PDF отчёт";
const PMI_SOURCE = "PMI";

async function apiFetch(url, options = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...options });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  return res;
}

function formatLocalIso(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Вчерашний день: С = По = yesterday (локальный календарь браузера). */
function yesterdayRange() {
  const day = new Date();
  day.setDate(day.getDate() - 1);
  const iso = formatLocalIso(day);
  return { start: iso, end: iso };
}

function setButtonsDisabled(disabled) {
  [
    "btnWeeklyBrief",
    "btnCheckCoverage",
    "btnLoadFull",
    "btnLoadMarket",
    "btnLoadCorporate",
    "btnLoadMonthlyNews",
    "btnLoadMonthlyMarket",
    "btnUploadAttachment",
    "btnSelectAllSources",
    "btnClearSources",
    "btnRefreshAttachments",
    "btnExportDocx",
    "btnExportJson",
  ].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = disabled;
  });
}

function setGenerationRunning(running) {
  setButtonsDisabled(running);
  const stop = $("btnStopWeeklyBrief");
  if (stop) {
    stop.hidden = !running;
    stop.disabled = !running;
  }
}

async function loadCurrentUser() {
  const res = await apiFetch("/api/auth/me");
  const data = await res.json();
  const user = data.user || {};
  $("userName").textContent = user.is_admin ? `${user.full_name || user.login} (админ)` : user.full_name || user.login;
}

async function loadConfig() {
  const res = await apiFetch("/api/health");
  const data = await res.json();
  $("apiBadge").textContent = data.openrouter_configured ? "OpenRouter OK" : "Нет OPENROUTER_API_KEY";
  $("apiBadge").className = data.openrouter_configured ? "badge ok" : "badge err";
  $("modelHint").textContent = `Модель: ${data.default_model || "—"}`;
  const modelEl = $("modelDisplay");
  if (modelEl) modelEl.textContent = data.default_model || "—";
  const pg = $("pgHint");
  if (pg) {
    pg.textContent = data.pgvector_configured
      ? "PostgreSQL + pgvector: подключено"
      : "PostgreSQL: не настроен (DATABASE_URL)";
    pg.className = data.pgvector_configured ? "hint ok" : "hint warn";
  }
}

function updatePromptKindHint() {
  const hint = $("promptKindHint");
  if (hint) hint.textContent = `Тип брифа: ${BRIEF_KIND_LABELS[selectedBriefKind] || selectedBriefKind}`;
}

async function loadPromptVariant(variant) {
  selectedBriefKind = variant || "full";

  const start = $("periodStart")?.value || "";
  const end = $("periodEnd")?.value || start;
  const params = new URLSearchParams({ variant: selectedBriefKind });
  if (start) params.set("period_start", start);
  if (end) params.set("period_end", end);

  const res = await apiFetch(`/api/weekly/default-prompt?${params.toString()}`);
  const data = await res.json();
  $("systemPrompt").value = data.prompt || "";
  selectedBriefKind = data.variant || selectedBriefKind;
  updatePromptKindHint();
  updateJsonExportVisibility();
  await loadPeriodSources({ applyBriefFilter: true });
  await checkCoverage();
}

function showCoverage(message, state) {
  const banner = $("coverageBanner");
  const info = $("coverageInfo");
  const icon = $("coverageIcon");
  if (!banner || !info) return;
  info.textContent = message;
  banner.hidden = false;
  banner.className = `coverage-banner ${state || ""}`.trim();
  if (icon) {
    icon.textContent = state === "ready" ? "✓" : state === "warn" ? "!" : state === "missing" ? "✕" : "ℹ";
  }
}

function sourceMatchesBriefKind(source, kind = selectedBriefKind) {
  const allowed = briefKindMatch[kind] || briefKindMatch.full;
  return allowed.includes(source.brief);
}

function selectSourcesForBriefKind(kind = selectedBriefKind) {
  selectedSourceNames = new Set(
    availableSources.filter((s) => sourceMatchesBriefKind(s, kind)).map((s) => s.name),
  );
  renderSourceChips();
  updateAttachmentSelectionHint();
  checkCoverage();
}

function updateSourceSelectionHint() {
  const hint = $("sourceSelectionHint");
  if (!hint) return;
  const total = availableSources.length;
  const selected = selectedSourceNames.size;
  const withNews = availableSources.filter((s) => s.count > 0).length;
  const matched = availableSources.filter((s) => sourceMatchesBriefKind(s)).length;
  hint.textContent = total
    ? `Выбрано ${selected} из ${total} · для типа брифа: ${matched} (с новостями: ${withNews})`
    : "Нет источников в конфигурации";
}

function renderSourceChips() {
  const root = $("sourceBriefGroups");
  if (!root) return;
  root.innerHTML = "";

  if (!availableSources.length) {
    root.innerHTML = '<p class="source-empty">Нет источников</p>';
    updateSourceSelectionHint();
    return;
  }

  const byTopic = new Map();
  for (const item of availableSources) {
    const topic = String(item.topic_category || "").trim() || "Без категории";
    if (!byTopic.has(topic)) byTopic.set(topic, []);
    byTopic.get(topic).push(item);
  }

  const topics = [
    ...TOPIC_GROUP_ORDER.filter((t) => byTopic.has(t)),
    ...[...byTopic.keys()]
      .filter((t) => !TOPIC_GROUP_ORDER.includes(t) && t !== "Без категории")
      .sort((a, b) => a.localeCompare(b, "ru")),
  ];
  if (byTopic.has("Без категории")) topics.push("Без категории");

  for (const topic of topics) {
    const items = byTopic.get(topic) || [];
    if (!items.length) continue;
    items.sort((a, b) => {
      const kindRank = (k) => (k === "document" ? 2 : k === "telegram" ? 1 : 0);
      const d = kindRank(a.kind) - kindRank(b.kind);
      if (d) return d;
      return String(a.name).localeCompare(String(b.name), "ru");
    });

    const group = document.createElement("div");
    group.className = "source-group";

    const heading = document.createElement("div");
    heading.className = "source-group-heading";

    const title = document.createElement("h3");
    title.className = "source-group-title";
    const matching = items.filter((s) => sourceMatchesBriefKind(s)).length;
    const selectedInTopic = items.filter((s) => selectedSourceNames.has(s.name)).length;
    title.innerHTML = `${topic}<span class="source-group-meta">${items.length}${
      matching ? ` · ${matching} подходят к брифу` : ""
    }</span>`;

    const allBtn = document.createElement("button");
    allBtn.type = "button";
    allBtn.className = "btn-source-group-all";
    const allSelected = selectedInTopic === items.length && items.length > 0;
    allBtn.textContent = allSelected ? "Снять" : "Все";
    allBtn.title = allSelected
      ? `Снять выбор со всех источников в «${topic}»`
      : `Выбрать все источники в «${topic}»`;
    if (allSelected) allBtn.classList.add("active");
    allBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleTopicSources(topic, items);
    });

    heading.appendChild(title);
    heading.appendChild(allBtn);
    group.appendChild(heading);

    const chips = document.createElement("div");
    chips.className = "source-chips";

    for (const item of items) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "source-chip";
      if (selectedSourceNames.has(item.name)) btn.classList.add("selected");
      if (!item.count) btn.classList.add("empty");
      if (item.kind === "telegram") btn.classList.add("tg");
      if (item.kind === "document") btn.classList.add("doc");
      if (!sourceMatchesBriefKind(item)) btn.classList.add("off-brief");
      btn.dataset.sourceName = item.name;
      const labelText = item.name.startsWith("TG ") ? item.name.slice(3) : item.name;
      const kindBadge =
        item.kind === "telegram" ? "TG" : item.kind === "document" ? "doc" : "web";
      const briefBadge = item.brief_label || briefLabels[item.brief] || "";
      btn.innerHTML =
        `<span class="source-chip-check" aria-hidden="true">✓</span>` +
        `<span class="source-chip-kind">${kindBadge}</span>` +
        `<span class="source-chip-label">${labelText}</span>` +
        (item.custom ? `<span class="source-chip-custom">свой</span>` : "") +
        (briefBadge
          ? `<span class="source-chip-brief">${briefBadge}</span>`
          : "") +
        `<span class="source-chip-count">${item.count}</span>`;
      btn.title = `${item.name}${briefBadge ? ` · ${briefBadge}` : ""} · ${topic}${
        item.custom ? " · добавлен коллегами" : ""
      }`;
      btn.addEventListener("click", () => toggleSource(item.name));
      chips.appendChild(btn);
    }

    group.appendChild(chips);
    root.appendChild(group);
  }

  updateSourceSelectionHint();
}

function toggleSource(name) {
  if (selectedSourceNames.has(name)) selectedSourceNames.delete(name);
  else selectedSourceNames.add(name);
  renderSourceChips();
  updateAttachmentSelectionHint();
  checkCoverage();
}

function toggleTopicSources(topic, items) {
  const names = (items || []).map((s) => s.name);
  if (!names.length) return;
  const allSelected = names.every((name) => selectedSourceNames.has(name));
  if (allSelected) {
    names.forEach((name) => selectedSourceNames.delete(name));
  } else {
    names.forEach((name) => selectedSourceNames.add(name));
  }
  renderSourceChips();
  updateAttachmentSelectionHint();
  checkCoverage();
}

function selectAllSources() {
  selectedSourceNames = new Set(availableSources.map((s) => s.name));
  renderSourceChips();
  updateAttachmentSelectionHint();
  checkCoverage();
}

function clearAllSources() {
  selectedSourceNames.clear();
  renderSourceChips();
  updateAttachmentSelectionHint();
  checkCoverage();
}

function isDocSourceSelected(name) {
  return selectedSourceNames.has(name);
}

function anyDocSourceSelected() {
  return [...DOC_SOURCE_NAMES].some((name) => selectedSourceNames.has(name));
}

function classifyAttachmentDocSource(documentType) {
  const t = String(documentType || "").toLowerCase();
  if (t.includes("kallanish")) return KALLANISH_SOURCE;
  if (t.includes("pmi")) return PMI_SOURCE;
  // «PDF отчёт» и прочие отчёты без Kallanish/PMI
  return PDF_SOURCE;
}

function isAttachmentTypeEnabled(documentType) {
  return isDocSourceSelected(classifyAttachmentDocSource(documentType));
}

function getSelectedSourcesPayload() {
  const newsSources = availableSources.filter((s) => s.kind !== "document");
  if (!newsSources.length) return null;
  const selectedNews = [...selectedSourceNames].filter((name) => {
    const src = availableSources.find((s) => s.name === name);
    return src && src.kind !== "document";
  });
  // null = все новостные источники; [] = ни одного (только документы RAG)
  if (selectedNews.length === newsSources.length) return null;
  return selectedNews;
}

async function loadPeriodSources({ applyBriefFilter = false } = {}) {
  const start = $("periodStart").value;
  const end = $("periodEnd").value;
  if (!start || !end) return;
  const res = await apiFetch(
    `/api/rag/period-sources?period_start=${encodeURIComponent(start)}&period_end=${encodeURIComponent(end)}`
  );
  const data = await res.json();
  availableSources = data.sources || [];
  if (data.brief_kind_match) briefKindMatch = data.brief_kind_match;
  if (data.brief_labels) briefLabels = data.brief_labels;

  if (applyBriefFilter || !selectedSourceNames.size) {
    selectedSourceNames = new Set(
      availableSources.filter((s) => sourceMatchesBriefKind(s)).map((s) => s.name),
    );
  } else {
    const prev = new Set(selectedSourceNames);
    selectedSourceNames = new Set(
      availableSources.filter((s) => prev.has(s.name)).map((s) => s.name),
    );
    if (!selectedSourceNames.size) {
      selectedSourceNames = new Set(
        availableSources.filter((s) => sourceMatchesBriefKind(s)).map((s) => s.name),
      );
    }
  }
  renderSourceChips();
  updateAttachmentSelectionHint();
}

async function checkCoverage() {
  const start = $("periodStart").value;
  const end = $("periodEnd").value;
  if (!start || !end) {
    showCoverage("Укажите начало и конец периода.", "warn");
    return;
  }
  showCoverage("Проверяем индекс…", "");
  const selected = getSelectedSourcesPayload();
  const sourcesQuery = selected?.length
    ? `&sources=${encodeURIComponent(selected.join(","))}`
    : "";
  const res = await apiFetch(
    `/api/rag/period-coverage?period_start=${encodeURIComponent(start)}&period_end=${encodeURIComponent(end)}&brief_kind=${encodeURIComponent(selectedBriefKind)}${sourcesQuery}`
  );
  const data = await res.json();
  if (!data.configured) {
    showCoverage("Векторная БД не настроена. Проверьте DATABASE_URL на сервере.", "missing");
    return;
  }
  const docs = data.documents || [];
  const attachments = data.attachments || docs.filter((d) =>
    d.source_type === "pdf_report" || d.source_type === "docx_report",
  );
  const days = data.period_days || 0;
  const news = data.news_documents || 0;
  const newsDays = data.news_days || 0;
  const fetched = data.news_full_text_fetched || 0;
  const attachmentSummary = attachments.length
    ? ` · документов: ${attachments.length} (${[...new Set(attachments.map((d) => d.document_type))].join(", ")})`
    : "";
  const sourceSummary = selected?.length
    ? ` · источников: ${selected.length}`
    : "";
  showCoverage(
    `Сырых новостей в базе: ${news} за ${newsDays} из ${days} дней` +
      ` (полный текст у ${fetched})` +
      sourceSummary +
      attachmentSummary,
    news > 0 || attachments.length > 0 ? "ready" : "missing",
  );
}

async function uploadAttachment() {
  const fileInput = $("attachmentFile");
  const files = fileInput?.files ? [...fileInput.files] : [];
  if (!files.length) {
    alert("Выберите один или несколько файлов (PDF, Word или TXT).");
    return;
  }

  const detected = [...new Set(files.map((f) => detectAttachmentDocumentType(f.name)).filter(Boolean))];
  const documentType = detected.length === 1 ? detected[0] : "";

  const hint = $("attachmentUploadHint");
  hint.className = "upload-hint";
  hint.textContent = `Загрузка и индексация ${files.length} файл(ов)…`;
  setButtonsDisabled(true);

  const form = new FormData();
  form.append("date_str", $("pdfDate").value);
  form.append("period_end", $("pdfEnd").value || $("pdfDate").value);
  form.append("document_type", documentType);
  for (const file of files) {
    form.append("files", file);
  }

  const endpoint =
    files.length === 1 ? "/api/rag/upload-document" : "/api/rag/upload-documents";
  if (files.length === 1) {
    form.delete("files");
    form.append("file", files[0]);
  }

  const res = await apiFetch(endpoint, { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  setButtonsDisabled(false);

  if (!res.ok) {
    hint.className = "upload-hint err";
    hint.textContent = data.detail || `Ошибка ${res.status}`;
    return;
  }

  if (files.length === 1) {
    hint.className = "upload-hint ok";
    hint.textContent =
      `Проиндексировано: ${data.document_type}, ${data.chunks} чанков, «${data.title}»`;
  } else {
    const parts = [
      `Готово: ${data.uploaded_count} из ${data.total}`,
      `${data.chunks} чанков`,
      data.document_type,
    ];
    if (data.skipped_count) parts.push(`пропущено (дубликаты): ${data.skipped_count}`);
    if (data.failed_count) parts.push(`ошибок: ${data.failed_count}`);
    hint.className = data.failed_count ? "upload-hint err" : "upload-hint ok";
    hint.textContent = parts.join(" · ");

    const details = [];
    for (const item of data.uploaded || []) {
      const kind = item.document_type ? `${item.document_type}, ` : "";
      details.push(`✓ ${item.filename || item.title} (${kind}${item.chunks} чанков)`);
    }
    for (const item of data.skipped || []) {
      details.push(`↷ ${item.filename}: уже в RAG`);
    }
    for (const item of data.failed || []) {
      details.push(`✕ ${item.filename}: ${item.reason}`);
    }
    if (details.length) {
      hint.textContent += `\n${details.join("\n")}`;
    }
  }

  fileInput.value = "";
  updateAttachmentFileCount();
  await loadAttachmentLibrary();
  await checkCoverage();
}

function formatIsoDateRu(iso) {
  if (!iso) return "—";
  const [year, month, day] = iso.split("-");
  if (!year || !month || !day) return iso;
  return `${day}.${month}.${year}`;
}

function formatAttachmentPeriod(doc) {
  const start = formatIsoDateRu(doc.brief_date);
  const end = formatIsoDateRu(doc.period_end);
  return start === end ? start : `${start} — ${end}`;
}

function formatCreatedAt(value) {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

function appendCell(row, text, className) {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  cell.textContent = text;
  row.appendChild(cell);
}

function setAttachmentLibraryCount(total, filtered) {
  const el = $("attachmentLibraryCount");
  if (!el) return;
  if (total == null) {
    el.textContent = "";
    return;
  }
  el.textContent = filtered ? `${total} · фильтр` : `${total}`;
}

function applyAttachmentLibraryCollapsed(collapsed) {
  const card = $("attachmentLibrary");
  const btn = $("btnToggleAttachmentLibrary");
  if (!card || !btn) return;
  card.classList.toggle("is-collapsed", collapsed);
  btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  try {
    localStorage.setItem("nav.attachmentLibrary.collapsed", collapsed ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
}

function initAttachmentLibraryCollapse() {
  const btn = $("btnToggleAttachmentLibrary");
  if (!btn) return;
  let collapsed = false;
  try {
    collapsed = localStorage.getItem("nav.attachmentLibrary.collapsed") === "1";
  } catch (_) {
    collapsed = false;
  }
  applyAttachmentLibraryCollapsed(collapsed);
  btn.addEventListener("click", () => {
    const card = $("attachmentLibrary");
    const next = !card?.classList.contains("is-collapsed");
    applyAttachmentLibraryCollapsed(next);
  });
}

const SIDEBAR_SECTION_IDS = [
  "attachmentPanel",
  "attachmentLibrary",
  "periodPanel",
  "sourcesPanel",
  "promptPanel",
];

function setSidebarActive(navId) {
  document.querySelectorAll(".sidebar-link[data-nav]").forEach((link) => {
    link.classList.toggle("active", link.dataset.nav === navId);
  });
}

function expandLibraryIfNeeded(sectionId) {
  if (sectionId !== "attachmentLibrary") return;
  applyAttachmentLibraryCollapsed(false);
}

function scrollToSection(sectionId) {
  const el = $(sectionId);
  if (!el) return;
  expandLibraryIfNeeded(sectionId);
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  setSidebarActive(sectionId);
  try {
    history.replaceState(null, "", `#${sectionId}`);
  } catch (_) {
    /* ignore */
  }
}

function initSidebarNav() {
  document.querySelectorAll(".sidebar-link[data-nav]").forEach((link) => {
    const href = link.getAttribute("href") || "";
    const navId = link.dataset.nav;
    if (!href.includes("#")) return;
    if (!navId || navId === "agent") return;
    link.addEventListener("click", (e) => {
      e.preventDefault();
      scrollToSection(navId);
    });
  });

  const hash = (location.hash || "").replace(/^#/, "");
  if (hash && SIDEBAR_SECTION_IDS.includes(hash)) {
    requestAnimationFrame(() => scrollToSection(hash));
  } else {
    setSidebarActive("periodPanel");
  }

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
      if (!visible.length) return;
      const id = visible[0].target.id;
      if (SIDEBAR_SECTION_IDS.includes(id)) setSidebarActive(id);
    },
    { rootMargin: "-20% 0px -55% 0px", threshold: [0.15, 0.4, 0.7] }
  );
  SIDEBAR_SECTION_IDS.forEach((id) => {
    const el = $(id);
    if (el) observer.observe(el);
  });
}

async function loadAttachmentLibrary() {
  const status = $("attachmentListStatus");
  const table = $("attachmentTable");
  const body = $("attachmentTableBody");
  if (!status || !table || !body) return;

  const useFilter = $("attachmentFilterByPeriod")?.checked;
  let url = "/api/rag/attachments";
  if (useFilter) {
    const start = $("pdfDate").value;
    const end = $("pdfEnd").value || start;
    if (!start) {
      status.textContent = "Укажите период загрузки выше для фильтра.";
      table.hidden = true;
      body.innerHTML = "";
      availableAttachmentIds = [];
      setAttachmentLibraryCount(null);
      updateAttachmentSelectionHint();
      return;
    }
    url += `?period_start=${encodeURIComponent(start)}&period_end=${encodeURIComponent(end)}`;
  }

  status.textContent = "Загрузка…";
  table.hidden = true;
  body.innerHTML = "";

  const res = await apiFetch(url);
  const data = await res.json();
  if (!data.configured) {
    status.textContent = "RAG не настроен (DATABASE_URL).";
    availableAttachmentIds = [];
    setAttachmentLibraryCount(null);
    updateAttachmentSelectionHint();
    return;
  }

  const docs = data.documents || [];
  availableAttachmentIds = docs.map((d) => Number(d.id)).filter((id) => id > 0);
  for (const doc of docs) {
    const id = Number(doc.id);
    if (id > 0) {
      attachmentMetaById[id] = {
        document_type: doc.document_type || "",
        title: doc.title || "",
        brief_date: doc.brief_date || "",
        period_end: doc.period_end || doc.brief_date || "",
      };
    }
  }

  setAttachmentLibraryCount(docs.length, useFilter);
  if (!docs.length) {
    status.textContent = useFilter
      ? "Нет документов за выбранный период."
      : "Документы не загружены.";
    updateAttachmentSelectionHint();
    return;
  }

  status.textContent = `Всего: ${docs.length}`;
  table.hidden = false;

  for (const doc of docs) {
    const row = document.createElement("tr");
    const nameHint = `${doc.title || ""} ${doc.file_path || ""}`.toLowerCase();
    const kind =
      doc.source_type === "pdf_report"
        ? "PDF"
        : nameHint.endsWith(".txt") || nameHint.includes(".txt")
          ? "TXT"
          : "Word";
    appendCell(row, doc.document_type || kind, "attachment-type");
    appendCell(row, doc.title || "—", "attachment-title");
    appendCell(row, formatAttachmentPeriod(doc), "attachment-period");
    appendCell(row, String(doc.chunks ?? 0), "attachment-chunks");
    appendCell(row, doc.indexed_by || "—", "attachment-user");
    appendCell(row, formatCreatedAt(doc.created_at), "attachment-date");

    const actions = document.createElement("td");
    actions.className = "attachment-actions";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn secondary btn-sm btn-danger";
    btn.textContent = "Удалить";
    btn.addEventListener("click", () => deleteAttachment(doc.id, doc.title || doc.document_type));
    actions.appendChild(btn);
    row.appendChild(actions);
    body.appendChild(row);
  }
  updateAttachmentSelectionHint();
}

function updateAttachmentSelectionHint() {
  const hint = $("attachmentSelectionHint");
  if (!hint) return;
  const total = availableAttachmentIds.length;
  if (!total) {
    hint.textContent = "Нет документов";
    return;
  }
  const forBrief = getSelectedAttachmentIds().length;
  const enabledTypes = [...DOC_SOURCE_NAMES]
    .filter((n) => isDocSourceSelected(n))
    .join(", ");
  hint.textContent = enabledTypes
    ? `В бриф за период: ${forBrief} (типы: ${enabledTypes})`
    : `В бриф: 0 — включите Kallanish / PDF / PMI в источниках`;
}

function attachmentOverlapsBriefPeriod(meta, start, end) {
  if (!start || !end) return true;
  if (!meta) return false;
  const docStart = meta.brief_date || "";
  const docEnd = meta.period_end || meta.brief_date || "";
  if (!docStart) return false;
  return docStart <= end && docEnd >= start;
}

function getSelectedAttachmentIds() {
  const start = $("periodStart")?.value || "";
  const end = $("periodEnd")?.value || "";
  // Все документы из библиотеки: галочек нет, фильтр только по типу источника и периоду брифа.
  return Object.keys(attachmentMetaById)
    .map((id) => Number(id))
    .filter((id) => {
      if (!(id > 0)) return false;
      const meta = attachmentMetaById[id];
      if (!meta) return false;
      if (!isAttachmentTypeEnabled(meta.document_type)) return false;
      return attachmentOverlapsBriefPeriod(meta, start, end);
    });
}

async function deleteAttachment(documentId, title) {
  const label = title || `документ #${documentId}`;
  if (!confirm(`Удалить «${label}» из RAG?`)) return;

  const res = await apiFetch(`/api/rag/attachments/${documentId}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    alert(data.detail || `Ошибка ${res.status}`);
    return;
  }
  delete attachmentMetaById[Number(documentId)];
  await loadAttachmentLibrary();
  await checkCoverage();
}

function detectAttachmentDocumentType(filename) {
  const lower = (filename || "").toLowerCase();
  const isPdf = lower.endsWith(".pdf");
  const isDocx = lower.endsWith(".docx");
  const isTxt = lower.endsWith(".txt");
  if (!isPdf && !isDocx && !isTxt) return "";
  if (lower.includes("pmi")) return "PMI";
  if (lower.includes("kallanish")) return "Kallanish";
  if (isPdf) return "PDF отчёт";
  if (isTxt) return "Kallanish";
  return "Kallanish";
}

function updateDocumentTypeFromFiles() {
  const input = $("attachmentFile");
  const hint = $("documentTypeHint");
  if (!input?.files?.length) {
    if (hint) hint.textContent = "Тип подставится сам: PMI / PDF отчёт / Kallanish.";
    return;
  }
  const files = [...input.files];
  const types = files.map((f) => detectAttachmentDocumentType(f.name));
  const unique = [...new Set(types.filter(Boolean))];
  if (!hint) return;
  if (unique.length === 1) {
    hint.textContent = files.length === 1
      ? `Определено автоматически: ${unique[0]}`
      : `Для всех ${files.length} файлов: ${unique[0]}`;
  } else {
    hint.textContent = `Смешанные типы: ${unique.join(", ")} — для каждого файла определится отдельно.`;
  }
}

function updateAttachmentFileCount() {
  const el = $("attachmentFileCount");
  const input = $("attachmentFile");
  if (!el || !input) return;
  const count = input.files?.length || 0;
  el.textContent = count ? `Выбрано файлов: ${count}` : "";
  updateDocumentTypeFromFiles();
}

function updateJsonExportVisibility() {
  const btn = $("btnExportJson");
  const help = $("briefEditorHelp");
  const monthly = isMonthlyBriefKind();
  if (btn) btn.hidden = !monthly;
  if (help) {
    help.textContent = monthly
      ? "Проверьте и поправьте текст. Word — для рассылки, JSON — структурированные слайды для презентации (заголовки, буллиты, комментарий докладчика)."
      : "Проверьте и поправьте текст. Затем нажмите «Скачать Word» — файл соберётся уже с вашими правками.";
  }
}

function showBriefEditor(content) {
  const card = $("briefEditorCard");
  const editor = $("briefEditor");
  const hint = $("briefExportHint");
  if (!card || !editor) return;
  editor.value = content || "";
  card.hidden = false;
  updateJsonExportVisibility();
  if (hint) {
    hint.className = "hint";
    hint.textContent = isMonthlyBriefKind()
      ? "Можно править текст перед экспортом в Word или JSON."
      : "Можно править текст перед экспортом в Word.";
  }
  card.scrollIntoView({ behavior: "smooth", block: "start" });
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
  $("jobStatus").textContent = job.status;
  const bar = $("progressBar");
  if (job.status === "running" || job.status === "pending") {
    bar.className = "progress-bar running";
    if (job.cancel_requested) {
      $("jobStatus").textContent = "stopping…";
      const stop = $("btnStopWeeklyBrief");
      if (stop) {
        stop.disabled = true;
        stop.textContent = "Останавливаем…";
      }
    }
  } else if (job.status === "completed") {
    bar.className = "progress-bar done";
    stopPolling();
    setGenerationRunning(false);
    resetStopButton();
    const content = job.result?.content || "";
    if (job.result?.brief_kind) {
      selectedBriefKind = job.result.brief_kind;
      updatePromptKindHint();
    }
    if (content) {
      $("jobStatus").textContent = isMonthlyBriefKind()
        ? "Готово — отредактируйте бриф, затем Word или JSON для презентации"
        : "Готово — отредактируйте бриф и скачайте Word";
      showBriefEditor(content);
    } else {
      $("jobStatus").textContent = "Готово, но текст брифа не получен";
    }
  } else if (job.status === "cancelled") {
    bar.className = "progress-bar failed";
    stopPolling();
    setGenerationRunning(false);
    resetStopButton();
    $("jobStatus").textContent = job.error || "Генерация остановлена";
  } else if (job.status === "failed") {
    bar.className = "progress-bar failed";
    stopPolling();
    setGenerationRunning(false);
    resetStopButton();
    if (job.error) $("jobStatus").textContent = `Ошибка: ${job.error}`;
  }
}

function resetStopButton() {
  const stop = $("btnStopWeeklyBrief");
  if (!stop) return;
  stop.textContent = "Остановить";
  stop.disabled = true;
  stop.hidden = true;
}

async function stopWeeklyBrief() {
  if (!activeJobId) return;
  const stop = $("btnStopWeeklyBrief");
  if (stop) {
    stop.disabled = true;
    stop.textContent = "Останавливаем…";
  }
  $("jobStatus").textContent = "stopping…";
  const res = await apiFetch(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    alert(data.detail || `Ошибка ${res.status}`);
    if (stop) {
      stop.disabled = false;
      stop.textContent = "Остановить";
    }
    return;
  }
  if (data.detail) {
    $("jobLog").textContent =
      ($("jobLog").textContent ? `${$("jobLog").textContent}\n` : "") +
      `[stop] ${data.detail}`;
  }
}

async function exportBriefDocx() {
  if (!activeJobId) {
    alert("Сначала сгенерируйте бриф.");
    return;
  }
  const content = ($("briefEditor")?.value || "").trim();
  if (content.length < 50) {
    alert("Текст брифа слишком короткий.");
    return;
  }
  const hint = $("briefExportHint");
  if (hint) {
    hint.className = "hint";
    hint.textContent = "Собираем Word…";
  }
  setButtonsDisabled(true);
  const res = await apiFetch(`/api/jobs/${activeJobId}/export-docx`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const data = await res.json().catch(() => ({}));
  setButtonsDisabled(false);
  if (!res.ok) {
    if (hint) {
      hint.className = "hint err";
      hint.textContent = data.detail || `Ошибка ${res.status}`;
    }
    alert(data.detail || `Ошибка ${res.status}`);
    return;
  }
  if (hint) {
    hint.className = "hint ok";
    hint.textContent = `Готово: ${data.docx_filename}`;
  }
  const url = data.download_url || `/api/jobs/${activeJobId}/download`;
  window.location.href = url;
}

async function exportBriefJson() {
  if (!activeJobId) {
    alert("Сначала сгенерируйте бриф.");
    return;
  }
  if (!isMonthlyBriefKind()) {
    alert("JSON доступен для ежемесячных брифов.");
    return;
  }
  const content = ($("briefEditor")?.value || "").trim();
  if (content.length < 50) {
    alert("Текст брифа слишком короткий.");
    return;
  }
  const hint = $("briefExportHint");
  if (hint) {
    hint.className = "hint";
    hint.textContent = "Собираем JSON…";
  }
  setButtonsDisabled(true);
  const res = await apiFetch(`/api/jobs/${activeJobId}/export-json`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  const data = await res.json().catch(() => ({}));
  setButtonsDisabled(false);
  if (!res.ok) {
    if (hint) {
      hint.className = "hint err";
      hint.textContent = data.detail || `Ошибка ${res.status}`;
    }
    alert(data.detail || `Ошибка ${res.status}`);
    return;
  }
  if (hint) {
    hint.className = "hint ok";
    hint.textContent = `Готово: ${data.json_filename} (${data.slides || 0} слайдов)`;
  }
  const url = data.download_url || `/api/jobs/${activeJobId}/download-json`;
  window.location.href = url;
}

async function startWeeklyBrief() {
  const prompt = $("systemPrompt").value.trim();
  if (prompt.length < 50) {
    alert("Промпт слишком короткий.");
    return;
  }
  const payload = {
    period_start: $("periodStart").value,
    period_end: $("periodEnd").value,
    system_prompt: prompt,
    model: null,
    brief_kind: selectedBriefKind,
  };
  if (!payload.period_start || !payload.period_end) {
    alert("Укажите период.");
    return;
  }
  const selectedSources = getSelectedSourcesPayload();
  const attachmentIds = getSelectedAttachmentIds();
  const noNews = Array.isArray(selectedSources) && selectedSources.length === 0;
  if (noNews && !anyDocSourceSelected()) {
    alert("Выберите хотя бы один источник (новости или документы: Kallanish / PDF / PMI).");
    return;
  }
  if (noNews && anyDocSourceSelected() && !attachmentIds.length) {
    const enabled = [...DOC_SOURCE_NAMES].filter((n) => isDocSourceSelected(n)).join(", ");
    alert(
      `Выбраны только документы (${enabled || "—"}), но в библиотеке нет файлов этих типов за период. ` +
        "Загрузите документы или добавьте новостные источники.",
    );
    return;
  }
  if (selectedSources !== null) payload.sources = selectedSources;
  payload.attachment_ids = attachmentIds;
  setGenerationRunning(true);
  const stopBtn = $("btnStopWeeklyBrief");
  if (stopBtn) stopBtn.textContent = "Остановить";
  $("progressCard").hidden = false;
  if ($("briefEditorCard")) $("briefEditorCard").hidden = true;
  if ($("briefEditor")) $("briefEditor").value = "";
  if ($("briefExportHint")) {
    $("briefExportHint").className = "hint";
    $("briefExportHint").textContent = "";
  }
  $("jobLog").textContent = "";
  const res = await apiFetch("/api/jobs/weekly-brief", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || `Ошибка ${res.status}`);
    setGenerationRunning(false);
    return;
  }
  const data = await res.json();
  activeJobId = data.job_id;
  pollTimer = setInterval(() => pollJob(activeJobId), 2000);
  pollJob(activeJobId);
}

async function init() {
  const range = yesterdayRange();
  $("periodStart").value = range.start;
  $("periodEnd").value = range.end;
  $("pdfDate").value = range.start;
  $("pdfEnd").value = range.end;
  initAttachmentLibraryCollapse();
  initSidebarNav();
  await loadCurrentUser();
  await loadConfig();
  await loadPeriodSources();
  await loadAttachmentLibrary();
  await loadPromptVariant("full");
}

$("btnLogout")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  window.location.href = "/login";
});
$("btnLoadFull")?.addEventListener("click", () => loadPromptVariant("full"));
$("btnLoadMarket")?.addEventListener("click", () => loadPromptVariant("market"));
$("btnLoadCorporate")?.addEventListener("click", () => loadPromptVariant("corporate"));
$("btnLoadMonthlyNews")?.addEventListener("click", () => loadPromptVariant("monthly_news"));
$("btnLoadMonthlyMarket")?.addEventListener("click", () => loadPromptVariant("monthly_market"));
$("btnCheckCoverage")?.addEventListener("click", async () => {
  await loadPeriodSources();
  await checkCoverage();
});
$("btnSelectBriefSources")?.addEventListener("click", () => selectSourcesForBriefKind());
$("btnSelectAllSources")?.addEventListener("click", selectAllSources);
$("btnClearSources")?.addEventListener("click", clearAllSources);
$("periodStart")?.addEventListener("change", async () => {
  await loadPeriodSources();
  if (selectedBriefKind) await loadPromptVariant(selectedBriefKind);
  updateAttachmentSelectionHint();
});
$("periodEnd")?.addEventListener("change", async () => {
  await loadPeriodSources();
  if (selectedBriefKind) await loadPromptVariant(selectedBriefKind);
  updateAttachmentSelectionHint();
});
$("btnUploadAttachment")?.addEventListener("click", uploadAttachment);
$("attachmentFile")?.addEventListener("change", updateAttachmentFileCount);
$("btnRefreshAttachments")?.addEventListener("click", loadAttachmentLibrary);
$("attachmentFilterByPeriod")?.addEventListener("change", loadAttachmentLibrary);
$("btnWeeklyBrief")?.addEventListener("click", startWeeklyBrief);
$("btnStopWeeklyBrief")?.addEventListener("click", stopWeeklyBrief);
$("btnExportDocx")?.addEventListener("click", exportBriefDocx);
$("btnExportJson")?.addEventListener("click", exportBriefJson);

init();
