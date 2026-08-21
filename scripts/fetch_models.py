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
    Próbuje wyciągnąć tabelę/listę modeli. Strategia:
    1. Szukaj tabel <table> -- typowe dla stron dokumentacji z listą modeli.
    2. Jeśli brak tabel, szukaj nagłówków (h2/h3) zawierających wzorzec ID modelu,
       a następnie sąsiadującego tekstu jako opisu/daty.
    3. Fallback: regex po całym tekście strony, wyciąga unikalne ID modeli,
       bez dat (do ręcznego uzupełnienia).
    """
    soup = BeautifulSoup(html, "lxml")
    pattern = MODEL_ID_PATTERNS[vendor]
    models: dict[str, dict] = {}

    # 1) Tabele
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            row_text = " | ".join(cells)
            match = pattern.search(row_text)
            if not match:
                continue
            model_id = match.group(0)
            date_match = DATE_PATTERN.search(row_text)
            models[model_id] = {
                "id": model_id,
                "raw_row": cells,
                "release_date_guess": date_match.group(0) if date_match else None,
                "source": source_url,
            }

    if models:
        log(f"{vendor}: znaleziono {len(models)} modeli w tabelach.")
        return list(models.values())

    # 2) Nagłówki + otaczający tekst
    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = heading.get_text(" ", strip=True)
        match = pattern.search(text)
        if not match:
            continue
        model_id = match.group(0)
        # zbierz tekst kilku kolejnych elementów siostrzanych jako kontekst
        context_parts = []
        for sibling in heading.find_next_siblings():
            if sibling.name in ("h2", "h3", "h4"):
                break
            context_parts.append(sibling.get_text(" ", strip=True))
            if len(context_parts) >= 3:
                break
        context = " ".join(context_parts)
        date_match = DATE_PATTERN.search(context)
        models[model_id] = {
            "id": model_id,
            "raw_row": [text, context[:300]],
            "release_date_guess": date_match.group(0) if date_match else None,
            "source": source_url,
        }

    if models:
        log(f"{vendor}: znaleziono {len(models)} modeli w nagłówkach.")
        return list(models.values())

    # 3) Fallback: regex po całym tekście
    full_text = soup.get_text(" ", strip=True)
    for match in pattern.finditer(full_text):
        model_id = match.group(0)
        if model_id not in models:
            models[model_id] = {
                "id": model_id,
                "raw_row": [],
                "release_date_guess": None,
                "source": source_url,
            }

    log(f"{vendor}: fallback regex znalazł {len(models)} unikalnych ID modeli "
        f"(bez dat -- do ręcznej weryfikacji, jeśli ta gałąź się uruchomiła).")
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
