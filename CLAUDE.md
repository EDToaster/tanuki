# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Tanuki** — a self-hosted EPUB reader optimized for language learning (Chinese, Korean). Backend: Python 3.12 + Flask. Frontend: vanilla JS/CSS (no build step, no bundler). Database: SQLite 3 (WAL mode). Package manager: `uv`.

## Common Commands

All commands run from `ebook-reader/`:

```bash
# Run dev server (http://localhost:8090)
python server.py

# Install deps
uv pip install -r requirements-dev.txt

# Run all tests
pytest tests/ -v

# Run a single test
pytest tests/test_server.py::test_name -v

# Run tests matching a keyword
pytest tests/ -k "segmenter" -v
```

Docker (from repo root):
```bash
docker-compose up -d   # boots reader + Caddy at http://books.homelab.local
```

## Architecture

### Request Flow

```
Browser (vanilla JS)
  → Flask routes (server.py)
      → EPUB parser (zipfile + lxml/BS4)
      → HTML normalizer (BS4 allowlist)
      → Segmenter registry → language-specific segmenter
      → DictProvider chain → NIKL / Wiktionary / fallback
      → SQLite progress store
```

### Key Patterns

**Segmenter registry** (`server.py`): Language-keyed `dict[str, Segmenter]`. Each `Segmenter` subclass implements `segment(text) → list[(word, lookup_term)]`. Adding a language means one class + one registry entry. Current: `JiebaSegmenter` (zh), `KoreanJosaSegmenter` (ko), `WhitespaceSegmenter` (default).

**DictProvider chain** (`server.py`): Language-keyed `dict[str, list[DictProvider]]`. Providers tried in order; first non-`None` result wins. Current: `NIKLProvider → WiktionaryProvider` for Korean, `WiktionaryProvider` for Chinese/default. Disk cache at `/tmp/dict_cache.json` + client-side session `Map` for dedup.

**HTML normalization pipeline** (`server.py`): Explicit allowlist (tags + attributes). Handles: XSS removal (`<script>`, `on*` attrs, `javascript:` URIs), image path rewriting to `/book/{id}/asset/{path}`, inline style filtering, SVG unwrapping, ruby tag preservation for furigana.

**Progress persistence**: Dual-write — `localStorage` (immediate, offline safe) + backend SQLite PUT (async, fire-and-forget). On load: try backend first, fall back to `localStorage`.

**Multi-user profiles**: URL-prefix pattern `/u/{name}/` — no auth, no sessions. Profiles are bookmarks. `localStorage` stores `lastProfile` for convenience.

### Frontend State Machine

Single-page app, client-side routing via `history.pushState`:
```
/ (no profile) → Profile picker
/u/:name/      → Library view
/u/:name/book/:id → Reader (chapter + paginated columns)
```

CSS multi-column pagination: `column-width: 100vw; overflow: hidden` + `translateX` navigation (GPU-composited). Page count via `Math.round((scrollWidth + 1) / innerWidth)`.

### API Surface

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/library` | Book list + metadata |
| GET | `/book/:id/chapter/:n` | Normalized + segmented HTML |
| GET | `/book/:id/asset/:path` | EPUB resources (images, CSS) |
| GET | `/api/dict?word=&lang=` | Dictionary lookup |
| GET/POST/DELETE | `/api/profiles[/:name]` | Profile CRUD |
| GET/PUT/DELETE | `/api/u/:name/progress[/:book_id]` | Reading progress |

## Test Fixtures

Sample EPUBs in `fixtures/`: `sample-zh.epub`, `sample-ko.epub`, `sample-en.epub`. Copy to `ebook-reader/books/` to test locally.

## Design Docs

`docs/` contains phased implementation plans (`phase-1` through `phase-6`) and design explorations for each major subsystem. `docs/README.md` is the architecture index.
