const $ = (id) => document.getElementById(id);

const AGENT_PROMPT_STORAGE_KEY = "nav.agent.systemPrompt";
const AGENT_PROMPT_COLLAPSED_KEY = "nav.agent.promptCollapsed";

let lastSources = [];
let chatHistory = [];
let sending = false;
let defaultAgentPrompt = "";

async function apiFetch(url, options = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...options });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("unauthorized");
  }
  return res;
}

async function loadCurrentUser() {
  const res = await apiFetch("/api/auth/me");
  const data = await res.json();
  const user = data.user || {};
  $("userName").textContent = user.full_name || user.login || "—";
}

async function loadConfig() {
  const res = await apiFetch("/api/health");
  const data = await res.json();
  $("apiBadge").textContent = data.openrouter_configured ? "OpenRouter OK" : "Нет OPENROUTER_API_KEY";
  $("apiBadge").className = data.openrouter_configured ? "badge ok" : "badge err";
  $("modelHint").textContent = `Модель: ${data.default_model || "—"}`;
  const pg = $("pgHint");
  pg.textContent = data.pgvector_configured
    ? "PostgreSQL + pgvector: подключено"
    : "PostgreSQL: не настроен (DATABASE_URL)";
  pg.className = data.pgvector_configured ? "hint ok" : "hint warn";
}

function setPromptStatus(text, kind = "") {
  const el = $("agentPromptStatus");
  if (!el) return;
  el.textContent = text;
  el.className = `help agent-prompt-status ${kind}`.trim();
}

function persistAgentPrompt() {
  const value = $("agentSystemPrompt")?.value ?? "";
  const trimmed = value.trim();
  if (!trimmed) {
    localStorage.setItem(AGENT_PROMPT_STORAGE_KEY, "");
    setPromptStatus("Промпт очищен — обычный чат без поиска по базе", "ok");
    return;
  }
  if (trimmed === defaultAgentPrompt) {
    localStorage.removeItem(AGENT_PROMPT_STORAGE_KEY);
    setPromptStatus("Дефолтный промпт с сервера");
    return;
  }
  localStorage.setItem(AGENT_PROMPT_STORAGE_KEY, value);
  setPromptStatus("Сохранено в этом браузере (отличается от дефолта)", "ok");
}

function getAgentSystemPromptForRequest() {
  const value = $("agentSystemPrompt")?.value ?? "";
  return value.trim() ? value : "";
}

async function loadAgentPrompt() {
  const res = await apiFetch("/api/agent/default-prompt");
  const data = await res.json();
  defaultAgentPrompt = (data.prompt || "").trim();
  const saved = localStorage.getItem(AGENT_PROMPT_STORAGE_KEY);
  const textarea = $("agentSystemPrompt");
  if (!textarea) return;
  if (saved && saved.trim()) {
    textarea.value = saved;
    setPromptStatus("Загружен сохранённый промпт из браузера", "ok");
  } else {
    textarea.value = defaultAgentPrompt;
    setPromptStatus("Дефолтный промпт с сервера");
  }
}

function resetAgentPrompt() {
  const textarea = $("agentSystemPrompt");
  if (!textarea) return;
  textarea.value = defaultAgentPrompt;
  localStorage.removeItem(AGENT_PROMPT_STORAGE_KEY);
  setPromptStatus("Сброшено к дефолту с сервера");
}

function setAgentPromptCollapsed(collapsed) {
  const body = $("agentPromptBody");
  const btn = $("btnToggleAgentPrompt");
  if (!body || !btn) return;
  body.hidden = collapsed;
  btn.textContent = collapsed ? "Развернуть" : "Свернуть";
  localStorage.setItem(AGENT_PROMPT_COLLAPSED_KEY, collapsed ? "1" : "0");
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

function renderAnswer(text) {
  return `<div class="md">${renderMarkdown(text || "")}</div>`;
}

function renderMarkdown(raw) {
  const fences = [];
  let source = String(raw || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  source = source.replace(/```[\w+-]*\n?([\s\S]*?)```/g, (_, code) => {
    const token = `%%FENCE${fences.length}%%`;
    fences.push(
      `<pre class="md-code"><code>${escapeHtml(String(code).replace(/\n$/, ""))}</code></pre>`
    );
    return token;
  });

  const lines = source.split("\n");
  const blocks = [];
  let index = 0;

  const isFence = (line) => /^%%FENCE\d+%%$/.test(line.trim());
  const isHeading = (line) => /^(#{1,4})\s+\S/.test(line);
  const isHr = (line) => /^\s*([-*_]){3,}\s*$/.test(line);
  const isQuote = (line) => /^\s*>\s?/.test(line);
  const isUl = (line) => /^\s*(?:[-*+]|•)\s+\S/.test(line);
  const isOl = (line) => /^\s*\d+[.)]\s+\S/.test(line);
  const isTableRow = (line) => /^\s*\|.+\|\s*$/.test(line);
  const isStandaloneBold = (line) => /^\s*\*\*[^*].*\*\*\s*$/.test(line);

  const isBlockStart = (line) =>
    !line.trim() ||
    isFence(line) ||
    isHeading(line) ||
    isHr(line) ||
    isQuote(line) ||
    isUl(line) ||
    isOl(line) ||
    isTableRow(line) ||
    isStandaloneBold(line);

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (isFence(line)) {
      blocks.push(line.trim());
      index += 1;
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 4);
      blocks.push(`<h${level} class="md-h md-h${level}">${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    if (isStandaloneBold(line)) {
      const title = line.trim().replace(/^\*\*/, "").replace(/\*\*\s*$/, "");
      blocks.push(`<h3 class="md-h md-h3">${renderInline(title)}</h3>`);
      index += 1;
      continue;
    }
    if (isHr(line)) {
      blocks.push('<hr class="md-hr" />');
      index += 1;
      continue;
    }
    if (isQuote(line)) {
      const quoted = [];
      while (index < lines.length && isQuote(lines[index])) {
        quoted.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote class="md-quote">${renderInline(quoted.join(" "))}</blockquote>`);
      continue;
    }
    if (isTableRow(line) && index + 1 < lines.length && isMdTableSep(lines[index + 1])) {
      const header = splitMdTableRow(line);
      index += 2;
      const rows = [];
      while (index < lines.length && isTableRow(lines[index])) {
        rows.push(splitMdTableRow(lines[index]));
        index += 1;
      }
      const thead = `<tr>${header.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr>`;
      const tbody = rows
        .map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`)
        .join("");
      blocks.push(
        `<div class="md-table-wrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`
      );
      continue;
    }
    if (isUl(line)) {
      const items = [];
      while (index < lines.length && isUl(lines[index])) {
        items.push(`<li>${renderInline(lines[index].replace(/^\s*(?:[-*+]|•)\s+/, ""))}</li>`);
        index += 1;
      }
      blocks.push(`<ul class="md-list">${items.join("")}</ul>`);
      continue;
    }
    if (isOl(line)) {
      const items = [];
      while (index < lines.length && isOl(lines[index])) {
        items.push(`<li>${renderInline(lines[index].replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
        index += 1;
      }
      blocks.push(`<ol class="md-list">${items.join("")}</ol>`);
      continue;
    }
    const para = [line];
    index += 1;
    while (index < lines.length && !isBlockStart(lines[index])) {
      para.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${renderInline(para.join("\n")).replace(/\n/g, "<br />")}</p>`);
  }

  let html = blocks.join("");
  fences.forEach((block, fenceIndex) => {
    html = html.replace(`%%FENCE${fenceIndex}%%`, block);
  });
  return html;
}

function splitMdTableRow(line) {
  let value = line.trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|")) value = value.slice(0, -1);
  return value.split("|").map((cell) => cell.trim());
}

function isMdTableSep(line) {
  if (!/^\s*\|?[\s:|-]+\|?\s*$/.test(line)) return false;
  const cells = splitMdTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, "")));
}

function renderInline(text) {
  let html = escapeHtml(text || "");
  html = html.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>'
  );
  html = html.replace(/\[(\d{1,3})\]/g, '<span class="cite">[$1]</span>');
  html = html.replace(/`([^`]+)`/g, '<code class="md-inline">$1</code>');
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  html = html.replace(
    /(^|[\s(«„"])\*([^*\n]+?)\*([\s).,;:!?»“"]|$)/g,
    "$1<em>$2</em>$3"
  );
  return html;
}

function addMessage(role, html) {
  const log = $("chatLog");
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  bubble.innerHTML = html;
  wrap.appendChild(bubble);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

function renderSources(sources) {
  if (!sources || !sources.length) return "";
  const items = sources
    .map((s) => {
      const titleHtml = s.url
        ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title)}</a>`
        : `<span>${escapeHtml(s.title)}</span>`;
      const openLabel = s.kind === "attachment" ? "показать фрагмент" : "показать целиком";
      return (
        `<li><span class="cite">[${s.ref}]</span> ` +
        `${titleHtml} ` +
        `<span class="src-meta">${escapeHtml(s.source)} · ${escapeHtml(s.news_date)}</span> ` +
        `<button type="button" class="btn-inline" data-full="${s.ref}">${openLabel}</button></li>`
      );
    })
    .join("");
  return `<div class="sources"><div class="sources-title">Источники</div><ul>${items}</ul></div>`;
}

function renderFullText(full) {
  const title = escapeHtml(full.title || "Документ");
  const titleHtml = full.url
    ? `<a href="${escapeHtml(full.url)}" target="_blank" rel="noopener">${title}</a>`
    : `<span>${title}</span>`;
  return (
    `<div class="full-text"><div class="full-text-title">` +
    `${titleHtml} ` +
    `<span class="src-meta">${escapeHtml(full.source || "")} · ${escapeHtml(full.news_date || "")}</span></div>` +
    `<pre class="full-text-body">${escapeHtml(full.text || "")}</pre></div>`
  );
}

async function ask(question) {
  if (sending || !question.trim()) return;
  sending = true;
  $("btnSend").disabled = true;
  addMessage("user", escapeHtml(question));
  const thinking = addMessage("assistant", '<span class="thinking">Думаю…</span>');

  const body = {
    question,
    period_start: $("agentStart").value || null,
    period_end: $("agentEnd").value || null,
    prior_sources: lastSources,
    history: chatHistory.slice(-6),
    system_prompt: getAgentSystemPromptForRequest(),
  };

  try {
    const res = await apiFetch("/api/agent/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      thinking.innerHTML = `<span class="err-text">${escapeHtml(data.detail || "Ошибка запроса")}</span>`;
      return;
    }
    let html = renderAnswer(data.answer || "");
    if (data.full_text) {
      html += renderFullText(data.full_text);
    }
    if (data.sources && data.sources.length) {
      lastSources = data.sources;
      html += renderSources(data.sources);
    }
    thinking.innerHTML = html;
    chatHistory.push({ role: "user", content: question });
    chatHistory.push({ role: "assistant", content: data.answer || "" });
    if (chatHistory.length > 12) {
      chatHistory = chatHistory.slice(-12);
    }
    $("chatLog").scrollTop = $("chatLog").scrollHeight;
  } catch (e) {
    thinking.innerHTML = `<span class="err-text">Сбой соединения</span>`;
  } finally {
    sending = false;
    $("btnSend").disabled = false;
  }
}

function showFull(ref) {
  const src = lastSources.find((s) => String(s.ref) === String(ref));
  if (!src) return;
  ask(`Покажи новость ${ref} целиком`);
}

$("btnSend").addEventListener("click", () => {
  const input = $("chatInput");
  const q = input.value;
  input.value = "";
  ask(q);
});

$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("btnSend").click();
  }
});

$("chatLog").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-full]");
  if (btn) showFull(btn.getAttribute("data-full"));
});

$("btnClearChat").addEventListener("click", () => {
  lastSources = [];
  chatHistory = [];
  $("chatLog").innerHTML =
    '<div class="chat-msg assistant"><div class="chat-bubble">Чат очищен. Задайте новый вопрос.</div></div>';
});

$("btnResetAgentPrompt")?.addEventListener("click", resetAgentPrompt);
$("btnToggleAgentPrompt")?.addEventListener("click", () => {
  const body = $("agentPromptBody");
  setAgentPromptCollapsed(!body.hidden);
});
$("agentSystemPrompt")?.addEventListener("change", persistAgentPrompt);
$("agentSystemPrompt")?.addEventListener("blur", persistAgentPrompt);

$("btnLogout").addEventListener("click", async () => {
  await apiFetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
});

(async function init() {
  try {
    await loadCurrentUser();
    await loadConfig();
    await loadAgentPrompt();
    setAgentPromptCollapsed(localStorage.getItem(AGENT_PROMPT_COLLAPSED_KEY) === "1");
  } catch (e) {
    /* redirect handled in apiFetch */
  }
})();
