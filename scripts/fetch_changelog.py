#!/usr/bin/env python3
"""
Pobiera "co nowego" z kanałów RSS zamiast scrapować strony release notes jako
HTML. Powód zmiany: HTML producentów zmienia strukturę bez ostrzeżenia (patrz
historia tego pliku -- kilka rund poprawek selektorów i wzorców dat), a RSS to
ustandaryzowany format, więc parsowanie jest znacznie stabilniejsze.

Źródła:
  - OpenAI: oficjalny kanał RSS (https://openai.com/news/rss.xml) -- prowadzony
    bezpośrednio przez OpenAI, najbardziej wiarygodne źródło jakie mamy.
  - Anthropic: Anthropic NIE publikuje oficjalnego RSS. Używamy community'owego
    mirrora (github.com/taobojlen/anthropic-rss-feed), który sam scrapuje
    anthropic.com/news i publikuje wynik jako gotowy plik XML, odświeżany
    automatycznie. To świadomy kompromis: prostszy parser (już RSS, nie HTML
    do zgadywania), ale zależność od utrzymania cudzego projektu. Jeśli ten
    mirror zniknie lub przestanie się aktualizować, ten skrypt zacznie zwracać
    zero nowych wpisów dla Anthropic -- fetch_warnings to wtedy pokaże, ale
    warto od czasu do czasu ręcznie sprawdzić, czy projekt nadal żyje:
    https://github.com/tim-hilde/anthropic-rss
    (zweryfikowane 21.08.2026: aktywny, codzienny cron, świeże wpisy)

Dodatkowo: llm-stats.com /v1/updates jako strukturalne (JSON) źródło informacji
o nowo dodanych modelach -- to jedyna część "co nowego", która nie polega na
RSS/HTML w ogóle.

Deduplikacja: hash(vendor + tytuł + data) -- pozwala uruchamiać skrypt
codziennie bez duplikowania wpisów.
"""

import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "changelog.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; claude-codex-tracker/1.0; +https://github.com/)"
}

MAX_ENTRIES_PER_VENDOR = 300

RSS_SOURCES = [
    {
        "vendor": "OpenAI",
        "url": "https://openai.com/news/rss.xml",
        "official": True,
    },
    {
        "vendor": "Anthropic",
        # Zweryfikowane 21.08.2026: lastBuildDate = dziś, aktywny codzienny cron
        # (60 commitów w historii repo). Dla porównania, inny community mirror
        # (taobojlen/anthropic-rss-feed) okazał się nieaktualny -- ostatni wpis
        # sprzed ponad 2,5 miesiąca -- więc jeśli kiedyś ten adres przestanie
        # działać, warto sprawdzić świeżość zamiennika, nie tylko czy URL żyje.
        "url": "https://tim-hilde.github.io/anthropic-rss/rss.xml",
        "official": False,
    },
]

LLM_STATS_UPDATES_URL = "https://api.llm-stats.com/stats/v1/updates"
ORG_VENDOR_MAP = {"anthropic": "Anthropic", "openai": "OpenAI"}


def log(msg: str) -> None:
    print(f"[fetch_changelog] {msg}", file=sys.stderr)


TAG_PATTERN = re.compile(r"<[^>]+>")


def strip_html(raw: str) -> str:
    """
    Niektóre kanały RSS (np. tim-hilde/anthropic-rss, który przenosi pełną
    treść posta z Webflow) trzymają w <description> surowy HTML ze stylami
    i znacznikami, nie czysty tekst. Bez tego strona pokazywałaby dosłowne
    tagi jako tekst zamiast czytelnego podsumowania.
    """
    if not raw:
        return ""
    text = TAG_PATTERN.sub(" ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def entry_hash(vendor: str, title: str, date: str | None) -> str:
    key = f"{vendor}|{title.strip().lower()}|{(date or '').strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_date_for_sort(entry: dict) -> str:
    """
    Zwraca porównywalny string (najlepiej YYYY-MM-DD) używany WYŁĄCZNIE do
    sortowania wpisów malejąco po dacie przed przycięciem listy. Woli
    date_guess (dla wpisów z RSS to zawsze czyste ISO, patrz fetch_rss_feed),
    z fallbackiem na fetched_at, żeby wpis bez rozpoznanej daty nie trafił
    przypadkowo na sam szczyt.
    """
    date_guess = entry.get("date_guess")
    if date_guess:
        match = re.match(r"^\d{4}-\d{2}-\d{2}", date_guess)
        if match:
            return match.group(0)
    return (entry.get("fetched_at") or "")[:10]


def fetch_rss_feed(vendor: str, url: str) -> list[dict]:
    """Pobiera i parsuje kanał RSS/Atom, zwraca listę wpisów w naszym formacie."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"{vendor}: błąd pobierania RSS {url}: {exc}")
        return []

    parsed_feed = feedparser.parse(resp.content)
    if parsed_feed.bozo:
        log(f"{vendor}: kanał {url} sparsowany z ostrzeżeniem "
            f"({parsed_feed.bozo_exception}) -- kontynuuję, wpisy mogą być niepełne.")

    entries = []
    for item in parsed_feed.entries:
        title = item.get("title", "").strip()
        if not title:
            continue

        date_str = None
        if item.get("published_parsed"):
            date_str = datetime(*item["published_parsed"][:6], tzinfo=timezone.utc).date().isoformat()
        elif item.get("published"):
            date_str = item.get("published")

        summary = strip_html(item.get("summary") or "")

        entries.append({
            "vendor": vendor,
            "title": title,
            "summary": summary[:500],
            "date_guess": date_str,
            "source": item.get("link") or url,
            "hash": entry_hash(vendor, title, date_str),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    log(f"{vendor}: RSS {url} -- sparsowano {len(entries)} wpisów.")
    return entries


def fetch_llm_stats_updates() -> list[dict]:
    """
    Dodatkowe, strukturalne źródło: llm-stats.com /v1/updates zwraca listę
    ostatnio dodanych modeli (JSON). Wymaga tego samego sekretu
    LLM_STATS_API_KEY co fetch_models.py.

    Niepewność schematu: nie miałam możliwości zweryfikować odpowiedzi na
    żywo. Log wypisuje klucze pierwszego elementu przy pierwszym uruchomieniu
    -- jeśli nazwy pól poniżej nie pasują, popraw je na podstawie tego logu.
    """
    api_key = os.environ.get("LLM_STATS_API_KEY")
    if not api_key:
        log("Brak LLM_STATS_API_KEY -- pomijam /v1/updates (nowe modele wg llm-stats.com).")
        return []

    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "claude-codex-tracker/1.0"}
    try:
        resp = requests.get(LLM_STATS_UPDATES_URL, headers=headers, params={"days": 30}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"Błąd zapytania do /v1/updates: {exc}")
        return []

    data = resp.json()
    items = data.get("updates") or data.get("models") or []
    if items:
        log(f"/v1/updates: klucze pierwszego elementu: {sorted(items[0].keys())} "
            f"-- sprawdź, czy pola date_str/organization/id poniżej pasują.")

    entries = []
    for item in items:
        org_field = item.get("organization")
        org_id = org_field.get("id") if isinstance(org_field, dict) else item.get("organization_id")
        vendor = ORG_VENDOR_MAP.get(str(org_id).lower()) if org_id else None
        if not vendor:
            continue

        model_id = item.get("id") or item.get("model_id") or "?"
        date_str = item.get("added_at") or item.get("release_date") or item.get("created_at")
        date_str = str(date_str) if date_str else None
        title = f"Nowy model w llm-stats.com: {model_id}"

        entries.append({
            "vendor": vendor,
            "title": title,
            "summary": "",
            "date_guess": date_str,
            "source": "https://llm-stats.com",
            "hash": entry_hash(vendor, title, date_str),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    log(f"/v1/updates: {len(entries)} wpisów Anthropic/OpenAI (z {len(items)} zwróconych łącznie).")
    return entries


def load_existing() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("Istniejący changelog.json jest uszkodzony -- zaczynam od pustej struktury.")
    return {"entries": [], "last_checked": None, "fetch_warnings": []}


def main() -> None:
    existing = load_existing()
    existing_hashes = {e["hash"] for e in existing.get("entries", [])}
    all_entries = list(existing.get("entries", []))
    warnings: list[str] = []
    new_count = 0

    for src in RSS_SOURCES:
        parsed = fetch_rss_feed(src["vendor"], src["url"])
        if not parsed:
            note = "" if src["official"] else " (community mirror -- sprawdź, czy nadal działa)"
            warnings.append(f"{src['vendor']}: RSS {src['url']} nie zwrócił wpisów{note}.")
            continue
        for entry in parsed:
            if entry["hash"] not in existing_hashes:
                all_entries.append(entry)
                existing_hashes.add(entry["hash"])
                new_count += 1

    llm_stats_entries = fetch_llm_stats_updates()
    for entry in llm_stats_entries:
        if entry["hash"] not in existing_hashes:
            all_entries.append(entry)
            existing_hashes.add(entry["hash"])
            new_count += 1

    by_vendor: dict[str, list[dict]] = {}
    for e in all_entries:
        by_vendor.setdefault(e["vendor"], []).append(e)
    trimmed: list[dict] = []
    for vendor, items in by_vendor.items():
        # Sortujemy jawnie po realnej dacie przed przycięciem -- NIE ufamy
        # kolejności w liście. Bug znaleziony 21.08.2026: niektóre kanały RSS
        # (np. pełna historia bloga OpenAI, 2015-2026) mają znacznie więcej
        # niż MAX_ENTRIES_PER_VENDOR wpisów, a `items[-N:]` przy liście
        # posortowanej "najnowsze pierwsze" zostawiało N NAJSTARSZYCH wpisów
        # zamiast najnowszych. Jawne sortowanie po dacie eliminuje tę klasę
        # błędu niezależnie od tego, w jakiej kolejności dany feed zwraca dane.
        items_sorted = sorted(items, key=parse_date_for_sort, reverse=True)
        trimmed.extend(items_sorted[:MAX_ENTRIES_PER_VENDOR])

    result = {
        "entries": trimmed,
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "fetch_warnings": warnings,
    }

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"Zapisano {DATA_PATH}: {new_count} nowych wpisów, {len(trimmed)} łącznie.")


if __name__ == "__main__":
    main()
