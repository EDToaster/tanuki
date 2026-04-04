# Phase 5: Reading Progress + Profiles

**Date:** 2026-04-04  
**Phase:** 5 of 6  
**Depends on:** Phase 1 (Flask app, book endpoints)

**Goal:** Add backend reading progress storage (SQLite via stdlib `sqlite3`) and multi-profile support via URL-prefix identity. Users bookmark `/u/{name}/` and progress syncs across any device on the local network. localStorage remains as an offline fallback tier.

**What this phase adds:**

- SQLite schema: `profiles` + `progress` tables, WAL mode, `INSERT OR REPLACE` upserts
- 7 new REST endpoints under `/api/profiles` and `/api/u/{name}/progress`
- Flask SPA routing: `/`, `/u/<name>/`, `/u/<name>/book/<id>` all serve `index.html`
- Profile picker page at `/` (auto-redirects to last profile via `localStorage`)
- Library view: single bulk progress fetch → "continue reading" badges
- Reader: dual-write on every page turn (backend + localStorage fallback)
- "Continue from chapter N, page M?" restore banner with "Start over" action
- Docker volume for `/data/progress.db`

---

### Task 1: SQLite schema and database initialization

**Files:** `server.py`, `tests/test_server.py`

**Step 1: Write failing tests**

```python
import sqlite3, os, pytest
from server import init_db, get_db

def test_init_db_creates_tables(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'progress.db')
    monkeypatch.setenv('DB_PATH', db_path)
    init_db()
    con = sqlite3.connect(db_path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert 'profiles' in tables
    assert 'progress' in tables
    con.close()

def test_init_db_idempotent(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'progress.db')
    monkeypatch.setenv('DB_PATH', db_path)
    init_db()
    init_db()   # second call must not raise
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "init_db" -v
```

**Step 3: Implement**

Add to `server.py`:

```python
import sqlite3, os

DB_PATH = os.environ.get('DB_PATH', '/data/progress.db')

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

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

CREATE INDEX IF NOT EXISTS idx_progress_profile      ON progress (profile_id);
CREATE INDEX IF NOT EXISTS idx_progress_profile_book ON progress (profile_id, book_id);
"""

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(_SCHEMA)
    con.close()

def get_db():
    con = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con
```

Call `init_db()` at server startup, just before `app.run(...)`:

```python
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8090, debug=True)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "init_db" -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: SQLite schema and init_db for reading progress"
```

---

### Task 2: Profile name validation helper

**Files:** `server.py`, `tests/test_server.py`

**Step 1: Write failing tests**

```python
from server import validate_profile_name

def test_valid_profile_names():
    for name in ['howard', 'Alice', 'user-1', 'user_2', 'A' * 32]:
        assert validate_profile_name(name) is True

def test_invalid_profile_names():
    for name in ['', 'a' * 33, 'has space', 'has/slash', 'has.dot', '<script>']:
        assert validate_profile_name(name) is False
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "validate_profile" -v
```

**Step 3: Implement**

```python
import re

_PROFILE_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]{1,32}$')

def validate_profile_name(name: str) -> bool:
    return bool(_PROFILE_NAME_RE.match(name))
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "validate_profile" -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: profile name validation helper"
```

---

### Task 3: Profile endpoints

**Files:** `server.py`, `tests/test_server.py`

**Step 1: Write failing tests**

```python
def test_list_profiles_empty(client):
    r = client.get('/api/profiles')
    assert r.status_code == 200
    assert r.get_json() == []

def test_create_profile(client):
    r = client.post('/api/profiles', json={'name': 'howard'})
    assert r.status_code == 201
    data = r.get_json()
    assert data['name'] == 'howard'
    assert 'created_at' in data

def test_create_profile_duplicate_409(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.post('/api/profiles', json={'name': 'howard'})
    assert r.status_code == 409

def test_create_profile_invalid_name_400(client):
    r = client.post('/api/profiles', json={'name': 'bad name!'})
    assert r.status_code == 400

def test_delete_profile(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.delete('/api/profiles/howard')
    assert r.status_code == 204

def test_delete_profile_not_found(client):
    r = client.delete('/api/profiles/nobody')
    assert r.status_code == 404
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "profile" -v
```

**Step 3: Implement**

```python
from flask import Flask, jsonify, request, abort, Response

@app.route('/api/profiles', methods=['GET'])
def list_profiles():
    with get_db() as con:
        rows = con.execute('SELECT name, created_at FROM profiles ORDER BY created_at').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/profiles', methods=['POST'])
def create_profile():
    body = request.get_json(silent=True) or {}
    name = body.get('name', '')
    if not validate_profile_name(name):
        return jsonify({'error': 'name must be 1–32 chars, letters/digits/hyphens/underscores only'}), 400
    try:
        with get_db() as con:
            con.execute('INSERT INTO profiles (name) VALUES (?)', (name,))
            row = con.execute('SELECT name, created_at FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': f"profile '{name}' already exists"}), 409

@app.route('/api/profiles/<name>', methods=['DELETE'])
def delete_profile(name):
    with get_db() as con:
        cur = con.execute('DELETE FROM profiles WHERE name=? COLLATE NOCASE', (name,))
    if cur.rowcount == 0:
        abort(404)
    return Response(status=204)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "profile" -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: profile CRUD endpoints (GET/POST /api/profiles, DELETE /api/profiles/<name>)"
```

---

### Task 4: Progress endpoints

**Files:** `server.py`, `tests/test_server.py`

**Step 1: Write failing tests**

```python
def test_get_all_progress_empty(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.get('/api/u/howard/progress')
    assert r.status_code == 200
    assert r.get_json() == []

def test_get_all_progress_unknown_profile(client):
    r = client.get('/api/u/nobody/progress')
    assert r.status_code == 404

def test_put_and_get_progress(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.put('/api/u/howard/progress/three-body',
                   json={'chapter_id': 4, 'page_index': 2})
    assert r.status_code == 200
    data = r.get_json()
    assert data['chapter_id'] == 4
    assert data['page_index'] == 2

def test_put_progress_upserts(client):
    client.post('/api/profiles', json={'name': 'howard'})
    client.put('/api/u/howard/progress/three-body', json={'chapter_id': 4, 'page_index': 2})
    client.put('/api/u/howard/progress/three-body', json={'chapter_id': 5, 'page_index': 0})
    r = client.get('/api/u/howard/progress/three-body')
    assert r.get_json()['chapter_id'] == 5

def test_put_progress_autocreates_profile(client):
    # PUT auto-creates the profile if it doesn't exist
    r = client.put('/api/u/newuser/progress/three-body',
                   json={'chapter_id': 1, 'page_index': 0})
    assert r.status_code == 200

def test_delete_progress(client):
    client.post('/api/profiles', json={'name': 'howard'})
    client.put('/api/u/howard/progress/three-body', json={'chapter_id': 4, 'page_index': 2})
    r = client.delete('/api/u/howard/progress/three-body')
    assert r.status_code == 204
    r2 = client.get('/api/u/howard/progress/three-body')
    assert r2.status_code == 404

def test_put_progress_invalid_body(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.put('/api/u/howard/progress/three-body', json={'chapter_id': 'bad'})
    assert r.status_code == 400
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "progress" -v
```

**Step 3: Implement**

```python
def _get_or_create_profile_id(con, name: str) -> int | None:
    """Return profile id for name, auto-creating if the name is valid."""
    if not validate_profile_name(name):
        return None
    row = con.execute('SELECT id FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if row:
        return row['id']
    con.execute('INSERT INTO profiles (name) VALUES (?)', (name,))
    return con.execute('SELECT id FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()['id']

def _require_profile_id(con, name: str) -> int:
    """Return profile id or abort 404."""
    row = con.execute('SELECT id FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if not row:
        abort(404)
    return row['id']

@app.route('/api/u/<name>/progress', methods=['GET'])
def get_all_progress(name):
    with get_db() as con:
        profile_id = _require_profile_id(con, name)
        rows = con.execute(
            'SELECT book_id, chapter_id, page_index, updated_at FROM progress WHERE profile_id=?',
            (profile_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/u/<name>/progress/<book_id>', methods=['GET'])
def get_progress(name, book_id):
    with get_db() as con:
        profile_id = _require_profile_id(con, name)
        row = con.execute(
            'SELECT book_id, chapter_id, page_index, updated_at FROM progress WHERE profile_id=? AND book_id=?',
            (profile_id, book_id)
        ).fetchone()
    if not row:
        abort(404)
    return jsonify(dict(row))

@app.route('/api/u/<name>/progress/<book_id>', methods=['PUT'])
def put_progress(name, book_id):
    body = request.get_json(silent=True) or {}
    chapter_id = body.get('chapter_id')
    page_index  = body.get('page_index')
    if not isinstance(chapter_id, int) or not isinstance(page_index, int) or chapter_id < 0 or page_index < 0:
        return jsonify({'error': 'chapter_id and page_index must be non-negative integers'}), 400
    with get_db() as con:
        profile_id = _get_or_create_profile_id(con, name)
        if profile_id is None:
            abort(400)
        now = __import__('datetime').datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        con.execute(
            '''INSERT INTO progress (profile_id, book_id, chapter_id, page_index, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, book_id) DO UPDATE SET
                   chapter_id=excluded.chapter_id,
                   page_index=excluded.page_index,
                   updated_at=excluded.updated_at''',
            (profile_id, book_id, chapter_id, page_index, now)
        )
        row = con.execute(
            'SELECT book_id, chapter_id, page_index, updated_at FROM progress WHERE profile_id=? AND book_id=?',
            (profile_id, book_id)
        ).fetchone()
    return jsonify(dict(row))

@app.route('/api/u/<name>/progress/<book_id>', methods=['DELETE'])
def delete_progress(name, book_id):
    with get_db() as con:
        profile_id = _require_profile_id(con, name)
        con.execute('DELETE FROM progress WHERE profile_id=? AND book_id=?', (profile_id, book_id))
    return Response(status=204)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "progress" -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: reading progress endpoints (GET/PUT/DELETE /api/u/<name>/progress)"
```

---

### Task 5: SPA routing — Flask serves index.html for all frontend routes

**Files:** `server.py`

The frontend is a single-page app. Flask must serve `index.html` for `/`, `/u/<name>/`, and `/u/<name>/book/<id>`. The JS router reads `window.location.pathname` to decide which view to render.

**Step 1: Implement**

```python
from flask import send_from_directory

@app.route('/')
@app.route('/u/<name>/')
@app.route('/u/<name>/book/<book_id>')
def spa(name=None, book_id=None):
    return send_from_directory(app.static_folder, 'index.html')
```

**Step 2: Verify**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/
curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/u/howard/
curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/u/howard/book/three-body
```

All should return `200`.

**Step 3: Commit**

```bash
git add server.py
git commit -m "feat: SPA routing — Flask serves index.html for all frontend routes"
```

---

### Task 6: Frontend — profile picker and library view with progress badges

**Files:** `static/index.html`, `static/app.js`, `static/style.css`

The JS router dispatches on `window.location.pathname`:

```js
// app.js — router
function route() {
  const path = window.location.pathname;
  const profileMatch = path.match(/^\/u\/([^\/]+)\//);
  const bookMatch    = path.match(/^\/u\/([^\/]+)\/book\/([^\/]+)/);

  if (bookMatch)    return showReader(bookMatch[1], bookMatch[2]);
  if (profileMatch) return showLibrary(profileMatch[1]);
  showProfilePicker();
}
window.addEventListener('popstate', route);
route();
```

**Profile picker (`showProfilePicker`):**

```js
async function showProfilePicker() {
  // Auto-redirect if last profile is known
  const last = localStorage.getItem('lastProfile');
  if (last) { navigate(`/u/${last}/`); return; }

  const profiles = await fetch('/api/profiles').then(r => r.json());

  document.getElementById('app').innerHTML = `
    <div class="picker-screen">
      <h1>Who's reading?</h1>
      <div class="picker-grid" id="picker-grid"></div>
      <button id="new-profile-btn">+ New profile</button>
    </div>`;

  const grid = document.getElementById('picker-grid');
  profiles.forEach(p => {
    const btn = document.createElement('button');
    btn.className = 'profile-btn';
    btn.textContent = p.name;
    btn.addEventListener('click', () => selectProfile(p.name));
    grid.appendChild(btn);
  });

  document.getElementById('new-profile-btn').addEventListener('click', async () => {
    const name = prompt('Profile name (letters, digits, hyphens, underscores):');
    if (!name) return;
    const r = await fetch('/api/profiles', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name})
    });
    if (r.ok) selectProfile(name);
    else alert((await r.json()).error);
  });
}

function selectProfile(name) {
  localStorage.setItem('lastProfile', name);
  navigate(`/u/${name}/`);
}

function navigate(url) {
  history.pushState({}, '', url);
  route();
}
```

**Library view with progress badges (`showLibrary`):**

```js
async function showLibrary(profile) {
  const [books, allProgress] = await Promise.all([
    fetch('/library').then(r => r.json()),
    fetch(`/api/u/${profile}/progress`).then(r => r.ok ? r.json() : []).catch(() => [])
  ]);

  const progressMap = new Map(allProgress.map(p => [p.book_id, p]));

  // ... render book grid ...
  books.forEach(book => {
    const card = document.createElement('div');
    card.className = 'book-card';
    // Use textContent for XSS safety (Phase 1 requirement)
    const img = document.createElement('img');
    img.src = book.cover_url; img.alt = '';
    const title = document.createElement('div');
    title.className = 'book-title'; title.textContent = book.title;
    const author = document.createElement('div');
    author.className = 'book-author'; author.textContent = book.author;
    card.append(img, title, author);

    const prog = progressMap.get(book.id);
    if (prog) {
      const badge = document.createElement('div');
      badge.className = 'continue-badge';
      badge.textContent = `Ch ${prog.chapter_id + 1} · p ${prog.page_index + 1}`;
      card.appendChild(badge);
    }

    card.addEventListener('click', () => navigate(`/u/${profile}/book/${book.id}`));
    grid.appendChild(card);
  });
}
```

**Reader — progress save/restore (`showReader`):**

```js
async function showReader(profile, bookId) {
  // ... existing reader setup ...

  // Restore progress
  let startChapter = 0, startPage = 0;
  try {
    const r = await fetch(`/api/u/${profile}/progress/${bookId}`);
    if (r.ok) {
      const p = await r.json();
      startChapter = p.chapter_id;
      startPage    = p.page_index;
    }
  } catch {
    // Backend unavailable: fall back to localStorage
    const local = JSON.parse(localStorage.getItem(`progress:${bookId}`) || 'null');
    if (local) { startChapter = local.chapter_id; startPage = local.page_index; }
  }

  // Save on every page turn
  function saveProgress(chapterId, pageIndex) {
    const body = JSON.stringify({chapter_id: chapterId, page_index: pageIndex});
    fetch(`/api/u/${profile}/progress/${bookId}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body
    }).then(r => {
      if (r.ok) {
        // Dual-write to localStorage as offline cache
        localStorage.setItem(`progress:${bookId}`,
          JSON.stringify({chapter_id: chapterId, page_index: pageIndex}));
      }
    }).catch(() => {
      // Backend down: write to localStorage only, silently
      localStorage.setItem(`progress:${bookId}`,
        JSON.stringify({chapter_id: chapterId, page_index: pageIndex}));
    });
  }
}
```

**Step 1: Implement the above in `static/app.js`**

**Step 2: Add CSS for picker and badge in `static/style.css`**

```css
.picker-screen {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; height: 100vh; gap: 2rem;
}
.picker-grid { display: flex; gap: 1rem; flex-wrap: wrap; justify-content: center; }
.profile-btn {
  padding: 1rem 2rem; font-size: 1.1rem; border-radius: 8px;
  border: 2px solid #ccc; cursor: pointer; background: none;
}
.continue-badge {
  position: absolute; bottom: 6px; left: 6px;
  background: rgba(0,0,0,0.65); color: #fff;
  font-size: 0.7rem; padding: 2px 6px; border-radius: 4px;
}
```

**Step 3: Commit**

```bash
git add static/
git commit -m "feat: profile picker, library progress badges, reader progress save/restore"
```

---

### Task 7: Docker volume for progress.db

**Files:** `roles/media-stack/templates/docker-compose.yml.j2`, `Dockerfile`

**Step 1: Update Docker Compose**

```yaml
ebook-reader:
  build: ./ebook-reader
  container_name: ebook-reader
  volumes:
    - {{ media_path }}/books:/books:ro
    - {{ data_path }}/ebook-reader:/data    # ← new: writable mount for SQLite
  ports:
    - "8090:8090"
  restart: unless-stopped
  environment:
    - BOOKS_DIR=/books
    - DB_PATH=/data/progress.db
```

**Step 2: Verify Dockerfile ENV**

```dockerfile
ENV BOOKS_DIR=/books
ENV DB_PATH=/data/progress.db
```

**Step 3: Commit**

```bash
git add roles/ Dockerfile
git commit -m "feat: add writable /data volume for progress.db persistence"
```

---

### Task 8: Smoke test — profiles and progress with real EPUBs

```bash
cp ../../fixtures/sample-zh.epub books/
cp ../../fixtures/sample-ko.epub books/
python server.py
```

Open `http://localhost:8090` in two different browsers (e.g. Chrome and Safari) and verify:

- **Profile picker** — first visit shows the picker. Create two profiles (e.g. `howard` and `alice`). Each browser navigates to its own `/u/{name}/` URL.
- **Progress save** — in Chrome as `howard`, open `sample-zh.epub` and read several pages. In Safari as `alice`, open `sample-ko.epub` and read a few pages.
- **Cross-device sync** — open a third browser tab as `howard` at `/u/howard/`. Open `sample-zh.epub` — it should resume at the chapter and page where Chrome left off (progress badge visible in library, restore prompt in reader).
- **Isolation** — `alice`'s progress is not visible under `howard` and vice versa.
- **Offline fallback** — stop the server. Open Chrome (still on the reader page) and advance one page. Restart the server and check `GET /api/u/howard/progress/sample-zh` — the position should match what the reader shows (dual-write localStorage caught the offline write).
- **Start over** — in the restore banner, tap "Start over". Confirm `GET /api/u/howard/progress/sample-zh` returns 404 and the book opens at chapter 0 page 0.

```bash
# API sanity checks:
curl http://localhost:8090/api/profiles
# → [{"name":"howard",...}, {"name":"alice",...}]

curl http://localhost:8090/api/u/howard/progress
# → [{"book_id":"sample-zh","chapter_id":N,"page_index":M,...}]
```

**Commit:**

```bash
git commit -m "chore: verified Phase 5 complete — profiles and reading progress"
```

---

### Summary

After Phase 5, the reader has:

- Full multi-profile support: `/u/howard/`, `/u/alice/` are independent, bookmarkable, cross-device
- Backend progress stored in SQLite — no new Python packages
- Dual-write localStorage fallback — degrades gracefully when backend unavailable
- Profile auto-creation on first page turn — no friction for new users
- Library "continue reading" badges driven by a single bulk API call
