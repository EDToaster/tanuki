import pytest, os
from server import app, init_db

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test.db')
    monkeypatch.setenv('DB_PATH', db_path)
    # patch the module-level DB_PATH variable
    import server
    monkeypatch.setattr(server, 'DB_PATH', db_path)
    init_db()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c
