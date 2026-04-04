# Ebook Reader Design

**Date:** 2026-04-03  
**Status:** Approved

## Overview

A lightweight, self-hosted EPUB reader optimized for language learning (Chinese and Korean). Replaces Kavita and Calibre-Web. Pure HTML/JS frontend, minimal Python backend, deployed as a Docker container in the existing media-stack.

Primary use case: read foreign-language books, tap individual characters to see pronunciation and definition, select multiple characters to look up a phrase.

---

## Architecture

```
┌─────────────┐     GET /library                ┌──────────────────┐
│             │ ──────────────────────────────▶  │                  │
│  Browser    │     GET /book/:id/chapter/:n      │  Python server   │
│  (pure JS)  │ ──────────────────────────────▶  │  (media-stack)   │
│             │                                   │  /books on disk  │
│             │     fetch() Wiktionary API         │                  │
│             │ ──────────────────────────────▶  └──────────────────┘
│             │       (direct, no proxy)
└─────────────┘
```

- Python server runs in the existing `media-stack` Docker compose
- `/media/books` mounted read-only into the container
- Frontend is a single `index.html` + vanilla JS, served by the same Python server
- Dictionary lookups go directly from the browser to `en.wiktionary.org` (CORS open via `origin=*`)
- No framework, no bundler, no build step

---

## Backend

### Stack

Python with the standard library (`http.server` or minimal FastAPI). No ORM, no database — EPUBs on disk are the source of truth.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/library` | List all books with metadata |
| `GET` | `/book/:id/cover` | Stream cover image |
| `GET` | `/book/:id/chapter/:n` | Return pre-processed HTML fragment for chapter N |
| `GET` | `/` | Serve `index.html` |
| `GET` | `/static/*` | Serve JS/CSS assets |

### `/library` Response

```json
[
  {
    "id": "the-three-body-problem",
    "title": "三体",
    "author": "刘慈欣",
    "cover_url": "/book/the-three-body-problem/cover",
    "chapter_count": 30,
    "language": "zh"
  }
]
```

`id` is derived from the EPUB filename (slugified). Language is read from the EPUB OPF `<dc:language>` field.

### Chapter Pre-processing Pipeline

For each chapter request:

1. Open EPUB ZIP, locate chapter N via the OPF spine
2. Extract chapter HTML
3. Strip `<head>`, EPUB-internal hrefs, external stylesheets
4. Wrap tappable units in `<span class="w">`:
   - **Chinese (`zh`):** every individual character (including punctuation skipped — only wrap `\u4e00-\u9fff` and CJK extension ranges)
   - **Korean (`ko`):** every space-delimited token
   - **Other languages:** no wrapping (plain text, user can still text-select)
5. Return a clean `<article data-lang="zh">` fragment

Individual characters are the lookup unit. No segmentation. For multi-character words, the user selects text manually.

---

## Frontend

### Pages

1. **Library view** — grid of book covers + titles. Tap to open.
2. **Reader view** — full-screen paginated reading experience.

### EPUB Rendering & Pagination

CSS multi-column handles pagination without any JS text-splitting:

```css
.chapter-content {
  column-width: 100vw;
  column-gap: 0;
  height: 100vh;
  overflow: hidden;
  will-change: transform;
}
```

The full chapter HTML is injected into this container. The browser reflows and splits into columns automatically — each column is one "page."

Navigation translates the container:

```js
container.style.transform = `translateX(${-pageIndex * window.innerWidth}px)`;
```

Page count after render:

```js
const pageCount = Math.round(container.scrollWidth / window.innerWidth);
```

Touch swipe (left/right) advances pages. Keyboard arrow keys work on desktop.

### Chapter Lookahead Buffer

On entering chapter N, silently fetch chapters N+1 through N+5 in the background. Store HTML strings in a `Map<chapterIndex, htmlString>`. Chapter transitions are instant — swap innerHTML and recalculate page count. Evict chapters more than 2 behind the current position to keep memory bounded.

### Dictionary Popup

**Trigger — single tap:** any `<span class="w">` element. Extract `innerText`.

**Trigger — text selection:** a small floating "Look up" pill appears above the native selection. Extract `window.getSelection().toString()`.

**Lookup:**

```
GET https://en.wiktionary.org/w/api.php
  ?action=parse
  &page={word}
  &prop=sections|text
  &format=json
  &origin=*
```

**Display — bottom sheet popup:**

```
┌─────────────────────────────┐
│  字                          │
│  zì  (Mandarin)             │
│  ─────────────────────────  │
│  1. character; letter; word │
│  2. (literary) courtesy name│
│                             │
│            [Open in Wiktionary ↗]  │
└─────────────────────────────┘
```

Priority: pronunciation line first (pinyin / romanization), then numbered definitions. Dismiss on tap outside or swipe down. "Open in Wiktionary" link for the full entry.

If Wiktionary returns no result or errors, show a brief "No result" state with the "Open in Wiktionary" link still present.

### UI / Mobile

- No framework, no dependencies beyond `fflate` (EPUB unzip is done server-side, so actually no client-side dependencies at all)
- Font: system font stack
- Touch targets minimum 44px
- Bottom sheet popup uses CSS `transform: translateY` animation
- Status bar: book title | chapter X of Y | page X of Y

---

## Infrastructure

### Docker

New service added to `roles/media-stack/templates/docker-compose.yml.j2`:

```yaml
ebook-reader:
  build: ./ebook-reader
  container_name: ebook-reader
  volumes:
    - {{ media_path }}/books:/books:ro
  ports:
    - "8090:8090"
  restart: unless-stopped
```

### Caddy

New entry in `caddy_sites`:

```yaml
- { name: books, port: 8090, host: "{{ media_stack_ip }}" }
```

Accessible at `http://books.homelab.local`.

### Migration

Kavita and Calibre-Web can be removed from the compose file once the new reader is working. No data migration needed — both read from the same `/media/books` directory.

---

## Out of Scope (v1)

- Vertical writing mode (top-to-bottom, right-to-left) — design is forward-compatible, add later
- CJK word segmentation — user selects multi-character phrases manually
- Reading progress sync across devices
- Offline / PWA / service worker caching
- Annotations or highlights that persist
- MOBI, PDF, CBZ support
