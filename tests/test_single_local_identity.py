import concurrent.futures
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from apps.api.database import Database
from apps.api.main import create_app
from apps.api.user_repository import UserRepository

@pytest.fixture
def local_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('SHIJU_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SHIJU_ENV', 'development')
    for key in ('DATABASE_URL', 'SHIJU_DATABASE_PATH'):
        monkeypatch.delenv(key, raising=False)
    from apps.api.settings import ApiSettings
    return ApiSettings.from_env()

def test_two_browsers_share_default_identity_without_setup_or_login(local_settings):
    app = create_app(local_settings)
    with TestClient(app) as first:
        second = TestClient(app)
        user = first.get('/v1/local-profile').json()['profile']
        assert user['display_name'] == '请输入用户名' and user['role'] == 'user'
        second.cookies.set('shiju_token', 'a-different-old-browser-user')
        other = second.get('/v1/profile/me', headers={'Authorization': 'Bearer old-user-token'}).json()
        assert other['id'] == user['id']
        assert second.get('/v1/local-profile').json()['profile']['id'] == user['id']
        response = second.post('/v1/local-profile', json={'display_name': '另一个人'})
        assert response.json()['profile']['id'] == user['id']
        assert response.json()['profile']['display_name'] == '请输入用户名'
        first.patch('/v1/local-profile', json={'display_name': '我的诗稿'})
        assert second.get('/v1/local-profile').json()['profile']['display_name'] == '我的诗稿'
        assert first.post('/v1/auth/login').status_code == 404
        assert first.post('/v1/auth/logout').status_code == 404
        assert first.get('/v1/forum/threads').status_code == 404
        assert first.get('/v1/admin/users').status_code == 404
    with TestClient(create_app(local_settings)) as restarted:
        assert restarted.get('/v1/profile/me').json()['id'] == user['id']

def test_concurrent_first_access_creates_exactly_one_user(local_settings):
    database = Database(local_settings.database_url)
    database.initialize()

    def get_profile(_):
        return UserRepository(database).local_profile()['id']
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        ids = list(executor.map(get_profile, range(12)))
    assert len(set(ids)) == 1
    with database.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1

def test_database_has_no_account_credentials_and_keeps_one_profile(local_settings):
    app = create_app(local_settings)
    user = app.state.users.local_profile()
    with app.state.database.connect() as db:
        columns = {row['name'] for row in db.execute('PRAGMA table_info(users)')}
    assert columns == {'id','public_id','username','display_name','created_at','bio','avatar_url','avatar_stored'}
    assert not hasattr(app.state.users, 'create_user')
    with pytest.raises(sqlite3.IntegrityError, match='只能维护一个用户'):
        with app.state.database.connect() as db:
            db.execute("INSERT INTO users(id,public_id,username,display_name) VALUES ('second','second','second','其他人')")
    with pytest.raises(sqlite3.IntegrityError, match='不能删除'):
        with app.state.database.connect() as db:
            db.execute('DELETE FROM users WHERE id=?', (user['id'],))
