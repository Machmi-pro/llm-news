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

function fmtPrice(pricing) {
  if (!pricing || (pricing.input_price_per_m == null && pricing.output_price_per_m == null)) {
    return null;
  }
  const inp = pricing.input_price_per_m != null ? `$${pricing.input_price_per_m}` : "?";
  const out = pricing.output_price_per_m != null ? `$${pricing.output_price_per_m}` : "?";
  return `${inp} / ${out} za MTok (in/out)`;
}

function fmtTopScores(topScores) {
  if (!topScores || Object.keys(topScores).length === 0) return null;
  return Object.entries(topScores)
    .slice(0, 4)
    .map(([k, v]) => `${k} ${v}`)
    .join(" · ");
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

  const price = fmtPrice(model.pricing);
  if (price) {
    card.appendChild(el("div", "model-notes", price));
  }

  // top_scores celowo ukryte -- kategorie agregują niepowiązane benchmarki
  // bez wspólnej skali (np. "math: 17" obok "math: 0.7"), więc pokazywanie
  // ich sugerowałoby porównywalność, której nie ma.

  if (model.notes) {
    card.appendChild(el("div", "model-notes", model.notes));
  }

  return card;
}

function parseReleaseDate(str) {
  if (!str) return null;
  const d = new Date(str);
  return isNaN(d.getTime()) ? null : d;
}

function renderModelColumn(containerEl, models) {
  const currentYear = new Date().getFullYear();

  const withDates = models.map(m => ({ ...m, _date: parseReleaseDate(m.release_date_guess) }));
  // sortowanie malejąco (najnowsze pierwsze); modele bez rozpoznanej daty lądują na końcu
  withDates.sort((a, b) => {
    const at = a._date ? a._date.getTime() : -Infinity;
    const bt = b._date ? b._date.getTime() : -Infinity;
    return bt - at;
  });

  const recent = withDates.filter(m => m._date && m._date.getFullYear() === currentYear);
  const older = withDates.filter(m => !(m._date && m._date.getFullYear() === currentYear));

  if (recent.length === 0 && older.length === 0) {
    containerEl.appendChild(el("p", "empty-note", "Brak danych — pierwsze uruchomienie workflow jeszcze się nie odbyło."));
    return;
  }

  if (recent.length === 0) {
    containerEl.appendChild(el("p", "empty-note", `Brak modeli z ${currentYear} roku — wszystkie poniżej.`));
  }

  recent.forEach(m => containerEl.appendChild(renderModelCard(m)));

  if (older.length > 0) {
    const olderWrap = el("div", "model-list model-older-wrap");
    olderWrap.style.display = "none";
    older.forEach(m => olderWrap.appendChild(renderModelCard(m)));
    containerEl.appendChild(olderWrap);

    const toggleBtn = el("button", "show-more-btn", `Pokaż więcej (${older.length}) ↓`);
    toggleBtn.addEventListener("click", () => {
      const isHidden = olderWrap.style.display === "none";
      olderWrap.style.display = isHidden ? "flex" : "none";
      toggleBtn.textContent = isHidden ? "Pokaż mniej ↑" : `Pokaż więcej (${older.length}) ↓`;
    });
    containerEl.appendChild(toggleBtn);
  }
}

function renderModels(data) {
  const claudeWrap = document.getElementById("claude-models");
  const codexWrap = document.getElementById("codex-models");

  renderModelColumn(claudeWrap, data.claude || []);
  renderModelColumn(codexWrap, data.codex || []);

  document.getElementById("meta-models").textContent =
    `modele: aktualizacja ${fmtDateTime(data.last_updated)}`;

  const warnBox = document.getElementById("models-warnings");
  if (data.fetch_warnings && data.fetch_warnings.length) {
    warnBox.textContent = data.fetch_warnings.join("\n");
  }
}

let allEntries = [];
let visibleCount = 10;
const PAGE_SIZE = 10;

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

  const shown = entries.slice(0, visibleCount);

  shown.forEach(entry => {
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

  const remaining = entries.length - shown.length;
  if (remaining > 0 || visibleCount > PAGE_SIZE) {
    const controls = el("div", "ledger-controls");

    if (remaining > 0) {
      const moreBtn = el("button", "show-more-btn", `Rozwiń więcej (${Math.min(PAGE_SIZE, remaining)}) ↓`);
      moreBtn.addEventListener("click", () => {
        visibleCount += PAGE_SIZE;
        renderLedger(getActiveFilter());
      });
      controls.appendChild(moreBtn);
    }

    if (visibleCount > PAGE_SIZE) {
      const collapseBtn = el("button", "show-more-btn", "Zwiń ↑");
      collapseBtn.addEventListener("click", () => {
        visibleCount = PAGE_SIZE;
        renderLedger(getActiveFilter());
        ledger.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      controls.appendChild(collapseBtn);
    }

    ledger.appendChild(controls);
  }
}

function getActiveFilter() {
  const active = document.querySelector(".filter-btn.active");
  return active ? active.dataset.filter : "all";
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
      visibleCount = PAGE_SIZE;
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
