# Backend Reading Progress & Multi-Profile Design

**Date:** 2026-04-03  
**Status:** Proposal  
**Author:** explorer-progress-v2  
**Related:**
- [2026-04-03-ebook-reader-design.md](./2026-04-03-ebook-reader-design.md) — base architecture
- [2026-04-03-reading-progress-design.md](./2026-04-03-reading-progress-design.md) — localStorage-only v1 design (now extended)

---

## Summary

This document extends the ebook reader with:

1. **Multi-profile support** — multiple users on the same homelab with no login/auth
2. **Backend reading progress storage** — SQLite (Python stdlib, zero new dependencies) so progress syncs across devices
3. **Graceful degradation** — localStorage fallback when the backend is unavailable

The v1 reading progress design (localStorage-only) is *not* replaced — it becomes the offline fallback and write-through cache layer.

---

## 1. Profile Identity

### 1.1 Option Analysis

Three candidates exist for identifying users without authentication:

| Option | Description | Survives Restart | Cross-Device | Multi-user Same Device |
|--------|-------------|-----------------|--------------|----------------------|
| **A. Cookie** | `profile=howard` cookie set on first visit | ✅ Yes (1-year expiry) | ⚠️ Requires picking profile on each new device | ❌ One cookie per browser — last user wins |
| **B. URL prefix** | `/u/howard/` prefix on all routes | ✅ Yes (via bookmark) | ✅ Bookmark works from any browser | ✅ Each user has a distinct URL to bookmark |
| **C. Name header** | `X-Profile: howard` on every request | ❌ Browser can't set custom headers on navigation | ❌ Not viable without Service Worker | ❌ Invisible, no browser-native way to set |

**Option C is not viable** — browsers have no mechanism for users to set custom request headers on regular navigation without a Service Worker. Eliminated.

**Option A (cookie) analysis:** Works within a single browser on a device. A cookie is just per-browser: if Howard uses Chrome and Alice uses Firefox on the same machine, they each get their own cookie and never conflict. But if Howard switches from Chrome to Safari on the same machine, he needs to pick his profile again. On a new device, first visit requires profile selection. The server reads the cookie from the request; no JS needed for the actual API calls.

**Option B (URL prefix) analysis:** The profile name is embedded in the URL itself. Bookmark `/u/howard/` and it works forever from any device, any browser. Two users sharing a device each bookmark their own URL — no last-write-wins conflict. Every API call includes the profile name in the path, making routing explicit and stateless. The tradeoff: every frontend route gains a `/u/{name}` prefix, and the root `/` becomes a profile picker/redirector.

### 1.2 Recommendation: URL Prefix

**Use `/u/{profile_name}/` as the profile identity mechanism.**

Reasons:
- **Cross-device without setup**: A bookmarked URL works from any browser on the local network with zero configuration.
- **Shared-device friendly**: Multiple users each bookmark their personal URL. No profile-switching UI needed.
- **Stateless client**: No cookie management, no `localStorage` lookup for identity, no `SameSite` edge cases, no private-mode breakage.
- **Self-documenting**: The URL makes the active profile visible in the address bar at all times.
- **Consistent with REST**: Profile name is a resource identifier in the URL, not a hidden side-channel.

The one real cost is that the Flask backend must add `/u/<profile_name>/` variants of the reader routes. This is ~5 extra route decorators and one `get_or_create_profile` helper.

**localStorage as a local convenience cache for "last used profile":**  
On a new device, the user must visit the profile picker or type `/u/howard/` directly. To reduce friction, the root `/` handler can read `localStorage.getItem('lastProfile')` and redirect automatically if a last-used profile is found. This is opt-in convenience, not an identity mechanism.

### 1.3 Profile Name Rules

- 1–32 characters
- Letters, digits, hyphens, underscores only (URL-safe, no encoding needed)
- Case-insensitive stored, case-preserving displayed
- Validated server-side; 400 on invalid names

---

## 2. Storage Backend

### 2.1 Why SQLite

The base design says "no ORM, no database" but this referred to the book catalog (EPUBs on disk are the source of truth). Reading progress is *user state*, not catalog data — it needs to persist across restarts and be queried efficiently. SQLite via Python's built-in `sqlite3` module is:

- **Zero new dependencies**: `sqlite3` is in the Python standard library since Python 2.5
- **Zero setup**: No daemon, no config, just a file on disk
- **Concurrent-read safe**: With WAL mode, multiple readers don't block each other
- **More than sufficient**: Progress data is tiny (~50 bytes per record); 5,000 books × 10 profiles = 500 rows = negligible

The Docker volume mount that currently brings in `/books` read-only would need a second writable mount for the SQLite database file (e.g., `/data/progress.db`).

### 2.2 Schema

```sql
-- Enable WAL mode for concurrent read performance
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    UNIQUE NOT NULL COLLATE NOCASE,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS progress (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    book_id    TEXT    NOT NULL,
    chapter_id INTEGER NOT NULL,
    page_index INTEGER NOT NULL,
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (profile_id, book_id)
);

CREATE INDEX IF NOT EXISTS idx_progress_profile ON progress (profile_id);
CREATE INDEX IF NOT EXISTS idx_progress_profile_book ON progress (profile_id, book_id);
```

**Design notes:**

- `UNIQUE (profile_id, book_id)` enables clean upsert via `INSERT OR REPLACE INTO progress ...` — atomic, no two-step read-modify-write.
- `ON DELETE CASCADE` on `profile_id`: deleting a profile deletes all its progress records.
- `COLLATE NOCASE` on `profiles.name`: "Howard" and "howard" are the same profile — prevents accidental duplicates.
- Timestamps use ISO 8601 UTC stored as TEXT — consistent with the frontend's `updatedAt` field and trivially comparable.
- No `page_index` is nullable. If chapter has only one page, store 0.

### 2.3 Database Initialization

The server initializes the schema on startup if the file doesn't exist:

```
On startup:
  → open connection to /data/progress.db (create if absent)
  → PRAGMA journal_mode=WAL
  → execute CREATE TABLE IF NOT EXISTS for both tables
  → PRAGMA foreign_keys=ON
```

No migration tooling needed: `IF NOT EXISTS` makes this idempotent. Schema changes in the future would require a manual migration, but for v1 this is acceptable given the homelab context.

### 2.4 Docker Volume Addition

```yaml
ebook-reader:
  build: ./ebook-reader
  container_name: ebook-reader
  volumes:
    - {{ media_path }}/books:/books:ro
    - {{ data_path }}/ebook-reader:/data   # ← new: writable mount for SQLite
  ports:
    - "8090:8090"
  restart: unless-stopped
```

The `/data` directory on the host persists the SQLite file across container rebuilds.

---

## 3. API Endpoints

### 3.1 Full Endpoint Table

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/profiles` | List all profiles |
| `POST` | `/api/profiles` | Create a new profile |
| `DELETE` | `/api/profiles/{name}` | Delete a profile and all its progress |
| `GET` | `/api/u/{name}/progress` | Get all progress for a profile (bulk, for library view) |
| `GET` | `/api/u/{name}/progress/{book_id}` | Get progress for one book |
| `PUT` | `/api/u/{name}/progress/{book_id}` | Upsert progress for one book |
| `DELETE` | `/api/u/{name}/progress/{book_id}` | Clear progress for one book ("start over") |

Existing endpoints remain unchanged:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/library` | List all books (unchanged) |
| `GET` | `/book/:id/cover` | Cover image (unchanged) |
| `GET` | `/book/:id/chapter/:n` | Chapter HTML (unchanged) |

### 3.2 Profile Endpoints

**`GET /api/profiles`**

Response `200 OK`:
```json
[
  { "name": "howard", "created_at": "2026-04-03T10:00:00Z" },
  { "name": "alice",  "created_at": "2026-04-03T11:30:00Z" }
]
```

Empty array `[]` if no profiles exist.

---

**`POST /api/profiles`**

Request body:
```json
{ "name": "howard" }
```

Response `201 Created`:
```json
{ "name": "howard", "created_at": "2026-04-03T10:00:00Z" }
```

Response `409 Conflict` if name already exists:
```json
{ "error": "profile 'howard' already exists" }
```

Response `400 Bad Request` if name is invalid:
```json
{ "error": "name must be 1–32 chars, letters/digits/hyphens/underscores only" }
```

---

**`DELETE /api/profiles/{name}`**

Response `204 No Content` on success (profile + all progress deleted via CASCADE).  
Response `404 Not Found` if profile does not exist.

### 3.3 Progress Endpoints

**`GET /api/u/{name}/progress`**

Returns all progress records for the profile. Used by the library view to render "continue reading" badges in one request.

Response `200 OK`:
```json
[
  {
    "book_id":    "the-three-body-problem",
    "chapter_id": 4,
    "page_index": 2,
    "updated_at": "2026-04-03T10:30:00Z"
  },
  {
    "book_id":    "death-end-rereverie",
    "chapter_id": 12,
    "page_index": 0,
    "updated_at": "2026-04-02T22:15:00Z"
  }
]
```

Empty array `[]` if no progress recorded. Response `404 Not Found` if profile does not exist.

---

**`GET /api/u/{name}/progress/{book_id}`**

Response `200 OK`:
```json
{
  "book_id":    "the-three-body-problem",
  "chapter_id": 4,
  "page_index": 2,
  "updated_at": "2026-04-03T10:30:00Z"
}
```

Response `404 Not Found` if no progress recorded for this book (or profile doesn't exist). The frontend treats 404 as "start from beginning."

---

**`PUT /api/u/{name}/progress/{book_id}`**

Request body:
```json
{
  "chapter_id": 4,
  "page_index": 2
}
```

Server sets `updated_at` to current UTC time. Upserts via `INSERT OR REPLACE`.

Response `200 OK`:
```json
{
  "book_id":    "the-three-body-problem",
  "chapter_id": 4,
  "page_index": 2,
  "updated_at": "2026-04-03T10:30:00Z"
}
```

Response `404 Not Found` if profile `{name}` does not exist.  
Response `400 Bad Request` if `chapter_id` or `page_index` are missing or not non-negative integers.

---

**`DELETE /api/u/{name}/progress/{book_id}`**

Response `204 No Content` on success.  
Response `404 Not Found` if profile doesn't exist (progress record may or may not exist; delete is idempotent).

### 3.4 Auto-Create Profile on PUT

A small quality-of-life rule: `PUT /api/u/{name}/progress/{book_id}` **auto-creates the profile** if it doesn't exist. Rationale: the first page turn in a reading session shouldn't require a separate profile-create call. The profile picker UI handles creation explicitly for the list view, but silent creation on first write prevents errors during normal reading.

This means the profile picker is UX-only (not a hard gate). The backend enforces name validity but not prior existence on writes.

---

## 4. Frontend Changes

### 4.1 Profile Picker (New Page / Modal)

The root `/` route serves a profile picker. This is the entry point for new devices.

```
┌─────────────────────────────────────┐
│  📚 Books                            │
│                                     │
│  Who's reading?                     │
│                                     │
│  ┌──────────┐  ┌──────────┐         │
│  │  howard  │  │  alice   │  [+ New]│
│  └──────────┘  └──────────┘         │
│                                     │
└─────────────────────────────────────┘
```

- Lists profiles from `GET /api/profiles`
- Tapping a profile → navigates to `/u/{name}/` and saves `lastProfile` to `localStorage`
- "+ New" button → inline input → `POST /api/profiles` → navigate to new profile
- On load: if `localStorage.getItem('lastProfile')` is set, auto-redirect to `/u/{name}/` (skip picker for returning users on the same device)

### 4.2 Library View at `/u/{name}/`

The library view gains backend-driven "continue reading" badges.

**Startup sequence:**
1. Fetch `/library` for the book list
2. Fetch `/api/u/{name}/progress` for all progress (one request, not N)
3. Build a `Map<bookId, progressRecord>` from the progress response
4. Render book grid; for each book, check the map and render badge if present

**On backend unavailable:** catch the fetch error on step 2, fall back to reading all `progress:*` keys from localStorage (same format as the v1 design). Library view renders correctly either way.

**"Continue reading" badge:** Same visual design as the localStorage proposal — `Ch {N} · p {M}` badge in the bottom-left of the cover card. `updatedAt` can render as a relative timestamp on hover/tap.

### 4.3 Reader View — Progress Save

The reader must know the active profile to save progress. The profile name is available in the URL path (parsed from `window.location.pathname`).

**Save on every page turn:**
```
User navigates page
→ PUT /api/u/{name}/progress/{book_id} { chapter_id, page_index }
  → On success: also write to localStorage (dual-write for offline resilience)
  → On failure (network error, backend down):
     → write to localStorage only (graceful degradation)
     → do NOT surface an error to the user — progress save failures are silent
```

The localStorage write-through ensures that if the backend is temporarily unavailable, local progress is preserved. On the next successful backend write, the backend catches up to the current position.

**Restore on book open:**
1. `GET /api/u/{name}/progress/{book_id}`
2. On `200`: use backend data. Load chapter `chapter_id`, navigate to `page_index` (clamped).
3. On `404`: start from beginning (no saved progress).
4. On network error / 5xx: fall back to `localStorage.getItem('progress:${bookId}')`. Same fallback behavior as the v1 design.

### 4.4 "Continue Reading" Restore Prompt

When backend data is used to restore position, show the same optional banner as the v1 design:

```
┌──────────────────────────────────────────────────┐
│  Continue from Chapter 5, page 3?  [Yes] [Start over] │
└──────────────────────────────────────────────────┘
```

"Start over" calls `DELETE /api/u/{name}/progress/{book_id}` and also `localStorage.removeItem('progress:${bookId}')`.

### 4.5 URL Structure Summary

| Route | Serves |
|-------|--------|
| `/` | Profile picker (or auto-redirect to last profile) |
| `/u/{name}/` | Library view for profile |
| `/u/{name}/book/{id}` | Reader view for book |

The Flask backend must serve `index.html` for all three patterns (SPA-style routing). The JS router inspects `window.location.pathname` to determine which view to render and which profile name is active.

---

## 5. Migration Path from localStorage-Only

### 5.1 Design Philosophy

The localStorage-only v1 design is not replaced — it becomes the **fallback tier**. The backend is tried first; localStorage is used when the backend is unavailable or unresponsive.

```
Progress read hierarchy:
  1. GET /api/u/{name}/progress/{book_id}
     ✅ Use backend data
     ❌ Fall back to localStorage

Progress write hierarchy:
  1. PUT /api/u/{name}/progress/{book_id}
     ✅ Also write to localStorage (dual-write)
     ❌ Write to localStorage only
```

### 5.2 What Changes vs. v1 localStorage Design

| Concern | v1 (localStorage) | v2 (backend + fallback) |
|---------|-------------------|------------------------|
| Primary store | `localStorage` | SQLite backend |
| Cross-device sync | ❌ No | ✅ Yes |
| Multi-profile | ❌ No | ✅ Yes (URL-based) |
| Offline reading | ✅ Always works | ✅ Falls back to localStorage |
| Progress data location | Browser only | Server + browser cache |
| `saveProgress()` | Write localStorage | PUT backend + write localStorage |
| `loadProgress()` | Read localStorage | GET backend → fallback localStorage |

### 5.3 No Breaking Changes to Existing localStorage Data

If a user has existing `progress:*` localStorage data from a pre-backend build:
- The fallback code path still reads it correctly (same key format, same JSON structure)
- The first successful `PUT` to the backend overwrites with current position
- There is no explicit migration of old localStorage data to backend — it's simply used as fallback until the backend is reached

This means a user who has been reading locally will seamlessly transition: their locally-stored position is the fallback on first load with the new backend (if backend has no record), and on the next page turn the backend gets the current position.

### 5.4 Backend Unavailability Handling

The frontend treats all of these as "backend unavailable" and falls back to localStorage:
- Network timeout (fetch with 3-second timeout recommended)
- `5xx` response from backend
- JSON parse error in response

The frontend does NOT fall back to localStorage on `404` — a 404 on `GET /api/u/{name}/progress/{book_id}` means "no saved progress for this book," which is a valid backend response, not an error.

---

## 6. Concurrency and Data Integrity

### 6.1 Concurrent Reads

SQLite in WAL mode allows multiple simultaneous readers. `GET /api/u/{name}/progress` can be served concurrently from multiple browser tabs or devices without conflict.

### 6.2 Concurrent Writes

Multiple browsers writing progress for the same `(profile, book)` simultaneously is possible (e.g., two devices open to the same book). The `INSERT OR REPLACE` upsert is atomic at the SQLite level. The last writer wins — whichever `PUT` request arrives last sets the final value. This is acceptable: both writes are valid reading positions and there is no semantic conflict. No optimistic locking needed.

### 6.3 Profile Name Collision

`UNIQUE COLLATE NOCASE` on `profiles.name` prevents two profiles with the same name in different cases. `POST /api/profiles` returns 409 on conflict. The profile picker UI should validate before submitting.

---

## 7. Security Considerations

### 7.1 Profile Name Injection

Profile names appear in URL paths and are used in SQL queries. Server-side validation (regex: `^[a-zA-Z0-9_-]{1,32}$`) prevents SQL injection (combined with parameterized queries) and path traversal. All SQL must use parameterized queries — never string interpolation.

### 7.2 Book ID Validation

`book_id` in progress endpoints comes from the URL. Validate it matches the slug format (same pattern as profile names, or at minimum reject anything containing `/`, `..`, or null bytes) before using in SQL. Note: the base design's path traversal risk (disc-3a62950a) applies equally here.

### 7.3 No Authentication

By design. This is a homelab on a trusted LAN. Any profile is accessible to anyone on the network. Users should understand this is not a private or secure system. The Caddy config provides network-level access control if needed (restrict to LAN CIDR).

### 7.4 Profile Enumeration

`GET /api/profiles` lists all profile names. This is intentional — users need to see available profiles on the picker screen. If this were a public deployment, it would be a privacy concern. For a homelab, it's expected behavior.

---

## 8. Summary

### 8.1 Decision Matrix

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Profile identity mechanism | URL prefix `/u/{name}/` | Cross-device, bookmarkable, multi-user same device, stateless client |
| Storage backend | SQLite via `sqlite3` (stdlib) | Zero new dependencies, sufficient for the data size |
| SQLite concurrency mode | WAL | Better read concurrency; no change for writes |
| Upsert strategy | `INSERT OR REPLACE` | Atomic, no two-phase read-write |
| Write behavior when backend down | Fall back to localStorage silently | User experience must not degrade for offline/homelab use |
| localStorage role | Fallback tier + write-through cache | Dual-write ensures offline resilience without a sync queue |
| Profile creation on first write | Auto-create | Prevents errors during normal use; picker is UX, not a gate |
| Conflict resolution (concurrent writes) | Last writer wins | Semantically valid; no locking complexity needed |

### 8.2 New Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `sqlite3` | Python stdlib | Already available in Python 3.x; no new install |
| `/data` Docker volume | Infrastructure | Writable mount for `progress.db` persistence |

No new Python packages. No new frontend libraries. The constraint "minimal Python stdlib + Flask, no new heavy dependencies" is preserved.

### 8.3 What the Reading Progress Design v1 Gets Right (Preserve)

- `chapterId` as the reliable anchor (not `pageIndex`) — **keep this**
- `pageIndex` clamped on restore — **keep this**
- Save on every page turn (not `beforeunload`) — **keep this**
- "Continue reading" badge in library grid — **keep this**
- Silent progress save failures — **keep this**

### 8.4 What This Design Adds

- Backend storage for cross-device sync
- Multi-profile support via URL-based identity
- Profile picker entry point at `/`
- Bulk progress fetch (`GET /api/u/{name}/progress`) for efficient library badge rendering
- Auto-create profile on first write (reduces friction)
- Graceful fallback to localStorage when backend unavailable

---

## Appendix: Route Map

```
GET  /                            → profile picker (SPA entry)
GET  /u/{name}/                   → library view (SPA)
GET  /u/{name}/book/{id}          → reader view (SPA)

GET  /library                     → book list (existing)
GET  /book/{id}/cover             → cover image (existing)
GET  /book/{id}/chapter/{n}       → chapter HTML (existing)

GET  /api/profiles                → list all profiles
POST /api/profiles                → create profile
DEL  /api/profiles/{name}         → delete profile

GET  /api/u/{name}/progress       → all progress for profile
GET  /api/u/{name}/progress/{id}  → progress for one book
PUT  /api/u/{name}/progress/{id}  → upsert progress
DEL  /api/u/{name}/progress/{id}  → clear progress ("start over")
```
