#!/usr/bin/env python3
"""
Pobiera "co nowego" bezpośrednio ze stron producentów (nie z agregatorów)
i dopisuje wyłącznie nowe wpisy do docs/data/changelog.json.

Źródła:
  - Anthropic: release notes platformy/API oraz release notes Claude.ai
  - OpenAI: changelog platformy (URL do zweryfikowania przy pierwszym uruchomieniu --
    OpenAI rozbija informacje między kilka miejsc bardziej niż Anthropic)

Strategia deduplikacji: hash(vendor + tytuł + data) -- jeśli taki hash już
istnieje w archiwum, wpis jest pomijany. Dzięki temu można uruchamiać skrypt
codziennie bez duplikowania wpisów, nawet jeśli strona nie ma stabilnych ID.

Archiwum jest przycinane do ostatnich MAX_ENTRIES_PER_VENDOR wpisów na producenta,
żeby plik nie rósł bez końca.
"""

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "changelog.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; claude-codex-tracker/1.0; +https://github.com/)"
}

MAX_ENTRIES_PER_VENDOR = 300

SOURCES = {
    "anthropic_platform": {
        "vendor": "Anthropic",
        "url": "https://platform.claude.com/docs/en/release-notes/overview",
    },
    "anthropic_claude_ai": {
        "vendor": "Anthropic",
        "url": "https://support.claude.com/en/articles/12138966-release-notes",
    },
    "openai_api_changelog": {
        "vendor": "OpenAI",
        # Zweryfikowane 21.08.2026 -- OpenAI przeniosło changelog API na domenę
        # developers.openai.com; stary adres platform.openai.com/docs/changelog
        # zwracał nieaktualną/obciętą treść (kończącą się na listopadzie 2025).
        "url": "https://developers.openai.com/api/docs/changelog",
    },
    "openai_codex_changelog": {
        "vendor": "OpenAI",
        # Osobny changelog specyficzny dla Codex (CLI/rozszerzenia) --
        # zmiany modeli domyślnych w Codex bywają tu ogłaszane wcześniej
        # niż w ogólnym changelogu API.
        "url": "https://developers.openai.com/codex/changelog",
    },
}

DATE_PATTERN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+20\d{2}"
    r"|\b20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b"
)


def log(msg: str) -> None:
    print(f"[fetch_changelog] {msg}", file=sys.stderr)


def fetch_html(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        log(f"Błąd pobierania {url}: {exc}")
        return None


def entry_hash(vendor: str, title: str, date: str | None) -> str:
    key = f"{vendor}|{title.strip().lower()}|{(date or '').strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def parse_entries(html: str, vendor: str, source_url: str) -> list[dict]:
    """
    Heurystyka: strony release notes zwykle grupują wpisy pod nagłówkami
    (h2/h3, często z datą) z następującym po nich opisem (p/ul).
    Bierzemy każdy nagłówek jako granicę wpisu i zbieramy tekst do następnego
    nagłówka tego samego poziomu jako treść.
    """
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict] = []

    headings = soup.find_all(["h2", "h3"])
    if not headings:
        log(f"{vendor} ({source_url}): brak nagłówków h2/h3 -- struktura strony "
            f"prawdopodobnie inna niż zakładana, selektor wymaga korekty.")
        return entries

    for heading in headings:
        title = heading.get_text(" ", strip=True)
        if not title or len(title) < 3:
            continue

        content_parts = []
        for sibling in heading.find_next_siblings():
            if sibling.name in ("h2", "h3"):
                break
            text = sibling.get_text(" ", strip=True)
            if text:
                content_parts.append(text)
            if len(content_parts) >= 5:
                break
        content = " ".join(content_parts)

        date_match = DATE_PATTERN.search(title) or DATE_PATTERN.search(content)
        date_str = date_match.group(0) if date_match else None

        entries.append({
            "vendor": vendor,
            "title": title,
            "summary": content[:500],
            "date_guess": date_str,
            "source": source_url,
            "hash": entry_hash(vendor, title, date_str),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    log(f"{vendor} ({source_url}): sparsowano {len(entries)} potencjalnych wpisów.")
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

    for key, cfg in SOURCES.items():
        html = fetch_html(cfg["url"])
        if html is None:
            warnings.append(f"{key}: nie udało się pobrać {cfg['url']}.")
            continue

        parsed = parse_entries(html, cfg["vendor"], cfg["url"])
        if not parsed:
            warnings.append(
                f"{key}: parsowanie {cfg['url']} nie dało żadnych wpisów -- "
                f"sprawdź selektory w SOURCES/parse_entries."
            )
            continue

        for entry in parsed:
            if entry["hash"] not in existing_hashes:
                all_entries.append(entry)
                existing_hashes.add(entry["hash"])
                new_count += 1

    # Przytnij per-vendor do MAX_ENTRIES_PER_VENDOR. Kolejność w pliku nie ma
    # znaczenia dla wyświetlania -- strona (app.js) sortuje wpisy po realnej
    # dacie przy renderowaniu, niezależnie od kolejności zapisu tutaj.
    by_vendor: dict[str, list[dict]] = {}
    for e in all_entries:
        by_vendor.setdefault(e["vendor"], []).append(e)
    trimmed: list[dict] = []
    for vendor, items in by_vendor.items():
        trimmed.extend(items[-MAX_ENTRIES_PER_VENDOR:])

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
