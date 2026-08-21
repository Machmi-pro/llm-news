# claude-codex-tracker

Statyczna strona (GitHub Pages) śledząca dostępne modele Claude i Codex/OpenAI
oraz najnowsze zmiany (release notes) publikowane bezpośrednio przez producentów.

## Struktura

```
.github/workflows/
  models-weekly.yml       -> co tydzień odświeża listę dostępnych modeli
  changelog-daily.yml     -> co dzień dopisuje nowe wpisy "co nowego"
scripts/
  fetch_models.py         -> Claude + Codex/OpenAI: lista modeli, daty, status
  fetch_changelog.py      -> Anthropic + OpenAI: release notes, tylko nowe wpisy
docs/                      -> źródło GitHub Pages (Settings -> Pages -> /docs)
  index.html / style.css / app.js
  data/models.json        -> wynik fetch_models.py
  data/changelog.json     -> wynik fetch_changelog.py (rosnące archiwum)
```

## Jak uruchomić

1. Załóż nowe repo na GitHub, wrzuć zawartość tego folderu.
2. Settings -> Pages -> Source: `Deploy from a branch`, branch `main`, folder `/docs`.
3. Settings -> Actions -> General -> Workflow permissions: **Read and write permissions**
   (workflowy same commitują zaktualizowane pliki JSON).
4. Odpal ręcznie oba workflowy raz (`Actions` -> wybierz workflow -> `Run workflow`),
   żeby zobaczyć realny wynik zamiast danych startowych.

## Ważne zastrzeżenie

Strony producentów (Anthropic, OpenAI) nie mają publicznego API do listy modeli
ani do release notes — to zwykły HTML. Skrypty w `scripts/` parsują ten HTML
heurystycznie (wzorce tekstowe + selektory CSS w `SELECTORS` na górze pliku).
**Nie testowałem tych selektorów na żywych stronach** (środowisko, w którym
budowałem repo, nie ma dostępu do sieci) — pierwsze uruchomienie w Actions
prawdopodobnie będzie wymagało drobnej korekty selektorów, jeśli struktura
strony różni się od założeń. Skrypty logują wyraźnie co się nie udało parsować,
zamiast cicho zwracać puste dane.

`docs/data/*.json` zawiera na start dane zebrane ręcznie (sierpień 2026) —
tylko po to, żeby strona nie była pusta przed pierwszym uruchomieniem workflow.

## Dane wejściowe do sekcji "co nowego"

- Anthropic: `https://platform.claude.com/docs/en/release-notes/overview`
  oraz `https://support.claude.com/en/articles/12138966-release-notes`
- OpenAI: `https://platform.openai.com/docs/changelog` (do zweryfikowania —
  OpenAI rozbija informacje między kilka miejsc bardziej niż Anthropic;
  jeśli ten URL się zmienił, popraw `SELECTORS["openai_changelog"]["url"]`
  w `scripts/fetch_changelog.py`)

## Dane wejściowe do sekcji "dostępne modele"

- Anthropic: `https://platform.claude.com/docs/en/about-claude/models/overview`
- OpenAI: `https://platform.openai.com/docs/models`
