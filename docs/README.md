# Ebook Reader — Implementation Index

A lightweight, self-hosted EPUB reader optimised for language learning (Chinese and Korean). Replaces Kavita and Calibre-Web. Pure HTML/JS frontend, Flask backend, deployed as a Docker container in the existing media-stack. Tap any character or word to see its pronunciation and definition; select multiple characters to look up a phrase. Reading progress syncs across devices via a backend SQLite store. Multiple users share one instance via URL-based profiles (`/u/{name}/`).

---

## Architecture

```
┌─────────────────────┐   GET /library                  ┌──────────────────────┐
│                     │ ──────────────────────────────▶  │                      │
│  Browser            │   GET /book/:id/chapter/:n        │  Flask (Python 3.12) │
│  (vanilla JS)       │ ──────────────────────────────▶  │  media-stack Docker  │
│                     │   GET /api/dict?word=&lang=        │  /books (read-only)  │
│                     │ ──────────────────────────────▶  │  /data/progress.db   │
│                     │                                   │                      │
│                     │   GET /api/u/:name/progress        │                      │
│                     │ ──────────────────────────────▶  └──────────────────────┘
│                     │                                           │
│                     │                              ┌────────────┴───────────┐
│                     │                              │  DictProvider chain    │
│                     │                              │  ko → [NIKL, Wikt.]   │
│                     │                              │  zh → [Wiktionary]    │
└─────────────────────┘                              └────────────────────────┘
```

- Python/Flask server runs in the existing `media-stack` Docker Compose
- `/media/books` mounted read-only; `/data` mounted writable for SQLite
- Frontend is `index.html` + vanilla JS + CSS — zero client-side dependencies
- Dictionary lookups proxied through backend (`/api/dict`) — no direct third-party calls from browser
- Accessible at `http://books.homelab.local` via Caddy

---

## Implementation Phases

| Phase | File | Description | Depends on |
|-------|------|-------------|------------|
| 1 | [Phase 1: Core Foundation + Security](2026-04-04-phase-1-foundation.md) | Project scaffold, EPUB parsing, `/library` + `/book/:id/chapter/:n` endpoints, CSS multi-column pagination, swipe/keyboard navigation, all 6 security/bug fixes | — |
| 2 | [Phase 2: Style Normalization](2026-04-04-phase-2-styling.md) | HTML allowlist pipeline, inline style filtering, `<br>` collapse, image path rewriting, `/book/:id/asset/:path` endpoint, SVG wrapper detection, unified reader CSS, dark mode, ruby preservation | Phase 1 |
| 3 | [Phase 3: Dictionary Infrastructure](2026-04-04-phase-3-dictionary.md) | Backend `/api/dict` endpoint, `DictProvider` interface, `WiktionaryProvider`, `NIKLProvider` (KRDICT API + proxy), provider chain registry, frontend updated to call `/api/dict` | Phase 1 |
| 4 | [Phase 4: Language Intelligence](2026-04-04-phase-4-language.md) | `Segmenter` interface + registry, `JiebaSegmenter` (zh), `KoreanJosaSegmenter` (ko), `WhitespaceSegmenter` (default), leaf-node text replacement in chapter pipeline | Phase 1, Phase 3 |
| 5 | [Phase 5: Reading Progress + Profiles](2026-04-04-phase-5-progress.md) | SQLite schema, 7 REST endpoints, URL-prefix profiles (`/u/{name}/`), profile picker, library "continue reading" badges, reader dual-write (backend + localStorage fallback), Docker `/data` volume | Phase 1 |
| 6 | [Phase 6: Polish + Infrastructure](2026-04-04-phase-6-polish.md) | Server-side EPUB metadata cache, client-side dict lookup cache, chapter error handling, empty chapter sentinel, chapter lookahead buffer, Docker Compose + Caddy entries, remove Kavita/Calibre-Web | Phase 1–5 |

---

## Key Design Decisions

### 1. Provider/adapter pattern for dictionary sources
All dictionary lookups go through a single `DictProvider` interface with a `lookup(word) → NormalizedEntry` contract. The backend routes via `GET /api/dict?word=&lang=` to a language-specific provider chain (e.g. `ko → [NIKLProvider, WiktionaryProvider]`). Adding a new language or source (e.g. CC-CEDICT for Chinese in v2) requires implementing one class and one registry entry — zero frontend changes.

### 2. Language-agnostic segmenter registry
Chapter text processing calls `get_segmenter(lang).segment(text)` and receives `(word, lookup_term)` pairs. `JiebaSegmenter` handles Chinese (raises lookup accuracy from ~40% to ~80%), `KoreanJosaSegmenter` handles Korean particle stripping (raises Wiktionary hit rate from ~15% to ~65%), and `WhitespaceSegmenter` is the safe default. Adding Japanese support means implementing `SudachiSegmenter` and one line in the registry.

### 3. URL-prefix profiles — no authentication
Users are identified by `/u/{name}/` URL prefixes. Profiles are bookmarks, not accounts. This is cross-device (any browser, any device on the LAN), multi-user on the same device, and stateless on the server. localStorage stores `lastProfile` as a convenience auto-redirect — not as identity.

### 4. SQLite via Python stdlib for progress storage
`sqlite3` is in the Python standard library — zero new dependencies. WAL mode provides concurrent-read performance. `INSERT OR REPLACE` with a `UNIQUE(profile_id, book_id)` constraint gives atomic upserts. localStorage remains as an offline fallback tier; the backend is tried first on every read and write.

### 5. HTML allowlist normalization
The chapter preprocessing pipeline strips everything not on an explicit allowlist of tags and attributes. Inline styles are filtered to a three-property allowlist (`font-style`, `font-weight`, `vertical-align`) to preserve meaningful semantic formatting while discarding publisher branding. This produces a consistent visual style across all books regardless of origin. The approach also handles the security requirement (XSS) and the UX requirement (unified styling) with the same pipeline pass.

### 6. CSS multi-column pagination
Pagination is handled entirely by the browser's CSS multi-column engine (`column-width: 100vw; height: 100vh; overflow: hidden`) with no JS text-splitting. Navigation uses `translateX` (GPU-composited, no layout reflow). Page count is calculated as `Math.max(1, Math.round((scrollWidth + 1) / innerWidth))` — the `+1` tolerance fixes off-by-one errors on hi-DPI displays. This approach works well in practice but has known failure modes: images wider than the viewport bleed into adjacent columns (fixed by `img { max-width: 100% }` in the reset stylesheet, applied in Phase 2).

---

## Source Design Documents

| Document | Description |
|----------|-------------|
| [Original Design](2026-04-03-ebook-reader-design.md) | Architecture, endpoints, UI, infrastructure |
| [Completeness Report](../completeness-report.md) | 8 gaps: XSS, caching, error states, Flask inconsistency, missing Dockerfile |
| [Korean Tokenization Analysis](2026-04-03-korean-tokenization-analysis.md) | Josa stripping design, Wiktionary coverage analysis |
| [NIKL Dictionary Design](2026-04-03-nikl-dictionary-design.md) | KRDICT API research, DictProvider pattern, normalized response format |
| [jieba Segmentation Design](2026-04-03-jieba-segmentation-design.md) | Segmenter interface, BeautifulSoup leaf-node replacement, performance analysis |
| [Backend Progress Design](2026-04-03-backend-progress-design.md) | SQLite schema, profile identity options, API endpoints, dual-write strategy |
| [EPUB Styling Normalization](2026-04-03-epub-styling-normalization-design.md) | HTML allowlist, unified CSS, ruby handling, dark mode, SVG wrapper detection |
| [Adversarial Review](2026-04-03-ebook-reader-adversarial-review.md) | Critical evaluation of CSS multi-column, Wiktionary coverage, zero-dep bet |
| [Synthesis](2026-04-03-synthesis.md) | Prioritised findings from all explorers, must-fix vs. should-add vs. defer |
