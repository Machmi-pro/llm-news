#!/usr/bin/env python3
"""
Pobiera listę modeli Claude (Anthropic) i Codex/GPT (OpenAI) z API llm-stats.com
zamiast scrapować strony dokumentacji producentów.

Dlaczego zmiana: strona /docs/en/about-claude/models/overview Anthropic okazała
się być referencyjną tabelą identyfikatorów (Claude API ID / AWS Bedrock ID /
GCP Vertex ID), nie spisem dat premier -- scrapowanie jej nigdy nie dostarczy
sensownej daty wydania, bo tej informacji tam po prostu nie ma w ustrukturyzowanej
formie. llm-stats.com daje to samo (lista modeli) jako czyste JSON, plus cenę
i wyniki benchmarkowe (top_scores) w jednym zapytaniu.

WAŻNE -- niepewność schematu: nie miałam możliwości przetestować tego na żywo
(brak dostępu do sieci w środowisku, w którym pisałam ten skrypt, i endpoint
wymaga klucza, którego nie mam). Nie wiem na 100% pod jaką nazwą pola API
zwraca datę wydania modelu -- normalize() sprawdza kilka prawdopodobnych nazw
(release_date, created_at, created, first_seen, added_at) i bierze pierwszą,
która istnieje. Przy pierwszym realnym uruchomieniu sprawdź log w GitHub
Actions -- wypisuje pełną listę kluczy pierwszego zwróconego modelu, co
pozwoli doprecyzować normalize(), jeśli żadne z powyższych pól nie pasuje.

Wymaga zmiennej środowiskowej LLM_STATS_API_KEY (GitHub Secret -> przekazywana
przez workflow jako zmienna env, patrz .github/workflows/models-weekly.yml).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "models.json"

API_BASE = "https://api.llm-stats.com/stats/v1"

# Klucze, pod jakimi API może zwracać datę wydania -- sprawdzane po kolei.
# Jeśli po pierwszym uruchomieniu w logu zobaczysz inną nazwę pola z datą,
# dopisz ją na początek tej listy.
DATE_FIELD_CANDIDATES = ["release_date", "created_at", "created", "first_seen", "added_at"]

ORGANIZATIONS = {
    "claude": "anthropic",
    "codex": "openai",
}


def log(msg: str) -> None:
    print(f"[fetch_models] {msg}", file=sys.stderr)


def get_api_key() -> str | None:
    key = os.environ.get("LLM_STATS_API_KEY")
    if not key:
        log("Brak zmiennej środowiskowej LLM_STATS_API_KEY -- sprawdź, czy sekret jest "
            "ustawiony w repo (Settings -> Secrets and variables -> Actions) i czy "
            "workflow go przekazuje w sekcji env.")
    return key


def fetch_all_models(organization: str, api_key: str) -> list[dict]:
    """Pobiera wszystkie modele danej organizacji, obsługując paginację (next_cursor)."""
    headers = {
        "User-Agent": "claude-codex-tracker/1.0",
        "Authorization": f"Bearer {api_key}",
    }
    models: list[dict] = []
    cursor = None
    page = 1

    while True:
        params = {"organization": organization, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(f"{API_BASE}/models", headers=headers, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log(f"{organization}: błąd zapytania do API (strona {page}): {exc}")
            if hasattr(exc, "response") and exc.response is not None:
                log(f"{organization}: treść odpowiedzi błędu: {exc.response.text[:500]}")
            break

        data = resp.json()
        batch = data.get("models", [])
        models.extend(batch)
        log(f"{organization}: strona {page} -- {len(batch)} modeli "
            f"(łącznie {len(models)}/{data.get('total', '?')}).")

        if page == 1 and batch:
            log(f"{organization}: klucze pierwszego modelu w odpowiedzi API: "
                f"{sorted(batch[0].keys())} -- sprawdź, czy DATE_FIELD_CANDIDATES "
                f"w tym skrypcie pasuje do realnego pola z datą.")

        cursor = data.get("next_cursor")
        if not cursor or not batch:
            break
        page += 1
        if page > 20:
            log(f"{organization}: przerwano po 20 stronach (zabezpieczenie przed pętlą) "
                f"-- jeśli to za mało, zwiększ limit w kodzie.")
            break

    return models


def extract_date(model: dict) -> str | None:
    for field in DATE_FIELD_CANDIDATES:
        value = model.get(field)
        if value:
            return str(value)
    return None


def normalize(model: dict) -> dict:
    """Spłaszcza pola API do kształtu, którego oczekuje docs/app.js."""
    pricing = None
    providers = model.get("providers") or []
    if providers:
        first = providers[0]
        pricing = {
            "provider_name": first.get("provider_name"),
            "input_price_per_m": first.get("input_price_per_m"),
            "output_price_per_m": first.get("output_price_per_m"),
        }

    return {
        "id": model.get("id"),
        "name": model.get("name") or model.get("id"),
        "release_date_guess": extract_date(model),
        "tier": (model.get("organization") or {}).get("name"),
        "top_scores": model.get("top_scores") or {},
        "pricing": pricing,
        "notes": "",
        "source": "https://llm-stats.com (API v1/models)",
    }


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

    api_key = get_api_key()
    if not api_key:
        result["fetch_warnings"].append(
            "Brak LLM_STATS_API_KEY -- pominięto pobieranie, zachowano poprzednie dane."
        )
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(1)

    for local_key, org in ORGANIZATIONS.items():
        raw_models = fetch_all_models(org, api_key)
        if not raw_models:
            warning = (
                f"{org}: API nie zwróciło żadnych modeli -- zachowano poprzednie dane "
                f"dla '{local_key}'. Sprawdź log powyżej pod kątem błędu zapytania "
                f"(np. zły klucz, zła nazwa parametru 'organization')."
            )
            log(warning)
            result["fetch_warnings"].append(warning)
            continue
        result[local_key] = [normalize(m) for m in raw_models]

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Zapisano {DATA_PATH} ({len(result['claude'])} Claude, {len(result['codex'])} Codex).")


if __name__ == "__main__":
    main()
