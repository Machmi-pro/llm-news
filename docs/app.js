const MODELS_URL = "data/models.json";
const CHANGELOG_URL = "data/changelog.json";

function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("pl-PL", {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

function renderModelCard(model) {
  const card = el("div", "model-card");
  card.appendChild(el("div", "model-id", model.id || model.name || "?"));

  const metaBits = [];
  if (model.name && model.name !== model.id) metaBits.push(model.name);
  if (model.release_date_guess) metaBits.push(`od ${model.release_date_guess}`);
  if (model.tier) metaBits.push(model.tier);
  if (metaBits.length) {
    card.appendChild(el("div", "model-meta", metaBits.join(" · ")));
  }

  if (model.notes) {
    card.appendChild(el("div", "model-notes", model.notes));
  }

  return card;
}

function renderModels(data) {
  const claudeWrap = document.getElementById("claude-models");
  const codexWrap = document.getElementById("codex-models");

  const claude = data.claude || [];
  const codex = data.codex || [];

  if (claude.length === 0) {
    claudeWrap.appendChild(el("p", "empty-note", "Brak danych — pierwsze uruchomienie workflow jeszcze się nie odbyło."));
  } else {
    claude.forEach(m => claudeWrap.appendChild(renderModelCard(m)));
  }

  if (codex.length === 0) {
    codexWrap.appendChild(el("p", "empty-note", "Brak danych — pierwsze uruchomienie workflow jeszcze się nie odbyło."));
  } else {
    codex.forEach(m => codexWrap.appendChild(renderModelCard(m)));
  }

  document.getElementById("meta-models").textContent =
    `modele: aktualizacja ${fmtDateTime(data.last_updated)}`;

  const warnBox = document.getElementById("models-warnings");
  if (data.fetch_warnings && data.fetch_warnings.length) {
    warnBox.textContent = data.fetch_warnings.join("\n");
  }
}

let allEntries = [];

/**
 * Próbuje sparsować date_guess (dowolny format tekstowy znaleziony na stronie
 * producenta, np. "2026-07-09", "October, 2023", "Jan 15, 2026") na obiekt Date.
 * Zwraca null, jeśli się nie uda -- wtedy używamy fetched_at jako zapasowego
 * klucza sortowania, żeby wpis i tak wylądował w rozsądnym miejscu.
 */
function parseEntryDate(dateGuess) {
  if (!dateGuess) return null;
  const cleaned = dateGuess.replace(",", "").trim();
  const d = new Date(cleaned);
  return isNaN(d.getTime()) ? null : d;
}

function sortKey(entry) {
  const parsed = parseEntryDate(entry.date_guess);
  if (parsed) return parsed.getTime();
  const fetched = entry.fetched_at ? new Date(entry.fetched_at) : null;
  return fetched && !isNaN(fetched.getTime()) ? fetched.getTime() : 0;
}

function renderLedger(filter) {
  const ledger = document.getElementById("changelog-ledger");
  ledger.innerHTML = "";

  const entries = filter === "all"
    ? allEntries
    : allEntries.filter(e => e.vendor === filter);

  if (entries.length === 0) {
    ledger.appendChild(el("p", "empty-note", "Brak wpisów do pokazania."));
    return;
  }

  entries.forEach(entry => {
    const item = el("div", "entry");
    item.dataset.vendor = entry.vendor || "";
    item.appendChild(el("div", "entry-date", `${entry.vendor || "?"} · ${entry.date_guess || "data nieznana"}`));
    item.appendChild(el("div", "entry-title", entry.title || "(bez tytułu)"));
    if (entry.summary) {
      item.appendChild(el("div", "entry-summary", entry.summary));
    }
    if (entry.source) {
      const src = el("div", "entry-source");
      const a = el("a", "", "źródło ↗");
      a.href = entry.source;
      a.target = "_blank";
      a.rel = "noopener";
      src.appendChild(a);
      item.appendChild(src);
    }
    ledger.appendChild(item);
  });
}

function renderChangelog(data) {
  // Nie zakładamy żadnej konkretnej kolejności w pliku źródłowym (różne strony
  // producentów porządkują wpisy różnie) -- sortujemy jawnie po realnej dacie
  // wpisu, malejąco (najnowsze pierwsze). Wpisy bez rozpoznanej daty lądują
  // wg daty pobrania (fetched_at) jako przybliżenie.
  allEntries = (data.entries || []).slice().sort((a, b) => sortKey(b) - sortKey(a));

  renderLedger("all");

  document.querySelectorAll(".filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderLedger(btn.dataset.filter);
    });
  });

  document.getElementById("meta-changelog").textContent =
    `changelog: sprawdzone ${fmtDateTime(data.last_checked)}`;

  const warnBox = document.getElementById("changelog-warnings");
  if (data.fetch_warnings && data.fetch_warnings.length) {
    warnBox.textContent = data.fetch_warnings.join("\n");
  }
}

async function loadJSON(url) {
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(`${url}: HTTP ${resp.status}`);
  return resp.json();
}

async function init() {
  try {
    const models = await loadJSON(MODELS_URL);
    renderModels(models);
  } catch (err) {
    document.getElementById("models-warnings").textContent = `Błąd wczytywania modeli: ${err.message}`;
  }

  try {
    const changelog = await loadJSON(CHANGELOG_URL);
    renderChangelog(changelog);
  } catch (err) {
    document.getElementById("changelog-warnings").textContent = `Błąd wczytywania changelogu: ${err.message}`;
  }
}

init();
