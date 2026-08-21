#!/usr/bin/env python3
"""
Pobiera listę aktualnie dostępnych modeli Claude (Anthropic) i Codex/GPT (OpenAI)
bezpośrednio ze stron producentów i zapisuje do docs/data/models.json.

UWAGA: strony producentów nie mają publicznego API dla listy modeli.
Ten skrypt parsuje HTML heurystycznie. Jeśli producent zmieni strukturę strony,
regex/selektory poniżej trzeba będzie skorygować -- skrypt loguje wyraźnie,
gdy nic nie udało się wyciągnąć, zamiast cicho nadpisywać dane pustką.

Nie nadpisujemy istniejącego pliku danymi pustymi: jeśli fetch się nie powiedzie
dla danego producenta, zachowujemy poprzedni zapis dla tej sekcji i tylko
dopisujemy ostrzeżenie w polu "fetch_warnings".
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "models.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; claude-codex-tracker/1.0; +https://github.com/)"
}

SOURCES = {
    "claude": "https://platform.claude.com/docs/en/about-claude/models/overview",
    "codex": "https://platform.openai.com/docs/models",
}

# Wzorce ID modeli -- służą jako siatka bezpieczeństwa, gdy parsowanie po
# strukturze HTML (nagłówki/tabele) zawiedzie i trzeba spaść na regex po
# surowym tekście strony.
MODEL_ID_PATTERNS = {
    "claude": re.compile(r"\bclaude-[a-z0-9][a-z0-9\-\.]*\b", re.IGNORECASE),
    "codex": re.compile(r"\b(?:gpt-[0-9][a-z0-9\-\.]*|codex-[a-z0-9\-\.]+|o[0-9]-[a-z0-9\-]*)\b", re.IGNORECASE),
}

DATE_PATTERN = re.compile(
    r"\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b"
)


def log(msg: str) -> None:
    print(f"[fetch_models] {msg}", file=sys.stderr)


def fetch_html(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        log(f"Błąd pobierania {url}: {exc}")
        return None


def parse_models_generic(html: str, vendor: str, source_url: str) -> list[dict]:
    """
    W przeciwieństwie do wcześniejszej wersji: NIE zatrzymuje się na pierwszej
    metodzie, która coś znalazła -- łączy wyniki z tabel, nagłówków i fallbacku
    tekstowego, bo różne strony różnie strukturyzują dane i poleganie tylko na
    jednej metodzie gubiło modele (np. gdy jeden wiersz tabeli zawierał kilka
    ID naraz, a parsowano tylko pierwsze trafienie w wierszu).

    Deduplikacja po ID z małymi literami (bo realne stringi API są lowercase,
    a nagłówki/opisy na stronie bywają zapisane "GPT-5.6" zamiast "gpt-5.6" --
    to ten sam model, nie dwa różne).
    """
    soup = BeautifulSoup(html, "lxml")
    pattern = MODEL_ID_PATTERNS[vendor]
    models: dict[str, dict] = {}  # klucz: model_id.lower()

    def register(model_id: str, raw_row: list[str], date_str: str | None) -> None:
        key = model_id.lower()
        if key not in models:
            models[key] = {
                "id": model_id,
                "raw_row": raw_row,
                "release_date_guess": date_str,
                "source": source_url,
            }
            return
        existing = models[key]
        # preferuj formę zapisaną małymi literami jako wyświetlane ID
        # (bliższe realnym stringom API niż nagłówki typu "GPT-5.6")
        if model_id.islower() and not existing["id"].islower():
            existing["id"] = model_id
        if date_str and not existing["release_date_guess"]:
            existing["release_date_guess"] = date_str
        if raw_row and not existing["raw_row"]:
            existing["raw_row"] = raw_row

    # 1) Tabele -- zbieramy WSZYSTKIE ID znalezione w każdym wierszu, nie tylko pierwsze
    table_hits = 0
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            row_text = " | ".join(cells)
            found_ids = {m.group(0) for m in pattern.finditer(row_text)}
            if not found_ids:
                continue
            date_match = DATE_PATTERN.search(row_text)
            date_str = date_match.group(0) if date_match else None
            for model_id in found_ids:
                register(model_id, cells, date_str)
                table_hits += 1
    log(f"{vendor}: tabele dały {table_hits} trafień ID ({len(models)} unikalnych modeli).")

    # 2) Nagłówki + kontekst -- dosypuje to, czego nie było w tabelach, nie nadpisuje istniejących
    heading_hits = 0
    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = heading.get_text(" ", strip=True)
        found_ids = {m.group(0) for m in pattern.finditer(text)}
        if not found_ids:
            continue
        context_parts = []
        for sibling in heading.find_next_siblings():
            if sibling.name in ("h2", "h3", "h4"):
                break
            context_parts.append(sibling.get_text(" ", strip=True))
            if len(context_parts) >= 3:
                break
        context = " ".join(context_parts)
        date_match = DATE_PATTERN.search(text) or DATE_PATTERN.search(context)
        date_str = date_match.group(0) if date_match else None
        for model_id in found_ids:
            register(model_id, [text, context[:300]], date_str)
            heading_hits += 1
    log(f"{vendor}: nagłówki dosypały {heading_hits} trafień ({len(models)} unikalnych modeli łącznie).")

    # 3) Fallback: regex po całym tekście strony -- dosypuje TYLKO ID, których
    # nie znaleziono wyżej (bez dat -- do ręcznej weryfikacji jeśli się pojawią)
    full_text = soup.get_text(" ", strip=True)
    fallback_hits = 0
    for match in pattern.finditer(full_text):
        model_id = match.group(0)
        if model_id.lower() not in models:
            register(model_id, [], None)
            fallback_hits += 1
    if fallback_hits:
        log(f"{vendor}: fallback regex dosypał {fallback_hits} ID nieznalezionych wcześniej "
            f"(bez dat -- do ręcznej weryfikacji).")

    log(f"{vendor}: {len(models)} unikalnych modeli łącznie z {source_url}.")
    return list(models.values())


def load_existing() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("Istniejący models.json jest uszkodzony -- zaczynam od pustej struktury.")
    return {"claude": [], "codex": [], "last_updated": None, "fetch_warnings": []}


def main() -> None:
    existing = load_existing()
    result = {
        "claude": existing.get("claude", []),
        "codex": existing.get("codex", []),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "fetch_warnings": [],
    }

    for vendor, url in SOURCES.items():
        html = fetch_html(url)
        if html is None:
            warning = f"{vendor}: nie udało się pobrać {url} -- zachowano poprzednie dane."
            log(warning)
            result["fetch_warnings"].append(warning)
            continue

        parsed = parse_models_generic(html, vendor, url)
        if not parsed:
            warning = (
                f"{vendor}: parsowanie {url} nie znalazło żadnych modeli -- "
                f"prawdopodobnie zmieniła się struktura strony, selektory wymagają korekty. "
                f"Zachowano poprzednie dane."
            )
            log(warning)
            result["fetch_warnings"].append(warning)
            continue

        result[vendor] = parsed

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"Zapisano {DATA_PATH} ({len(result['claude'])} Claude, {len(result['codex'])} Codex).")


if __name__ == "__main__":
    main()
