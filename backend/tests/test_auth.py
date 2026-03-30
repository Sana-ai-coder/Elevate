from backend.app import create_app
from backend.models import db, User
import pytest

@pytest.fixture
def app():
    app = create_app('testing')
    app.config.update({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app

@pytest.fixture
def client(app):
    return app.test_client()


def test_signup_login_me_and_refresh(client):
    # signup
    r = client.post('/api/auth/signup', json={'name': 'Alice', 'email': 'a@example.com', 'password': 'Password1!'})
    assert r.status_code == 201
    data = r.get_json()
    assert 'token' in data

    # login
    r2 = client.post('/api/auth/login', json={'email': 'a@example.com', 'password': 'Password1!'})
    assert r2.status_code == 200
    token = r2.get_json()['token']

    # me
    r3 = client.get('/api/auth/me', headers={'Authorization': f'Bearer {token}'})
    assert r3.status_code == 200
    assert r3.get_json()['user']['email'] == 'a@example.com'

    # refresh
    r4 = client.post('/api/auth/refresh', headers={'Authorization': f'Bearer {token}'})
    assert r4.status_code == 200
    assert 'token' in r4.get_json()


def test_role_enforcement(client):
    # signup student
    client.post('/api/auth/signup', json={'name': 'Stu', 'email': 'stu@example.com', 'password': 'Password1!'})
    login = client.post('/api/auth/login', json={'email': 'stu@example.com', 'password': 'Password1!'})
    tok = login.get_json()['token']

    # student trying to access teacher endpoint should be forbidden
    res = client.get('/api/teacher/reports', headers={'Authorization': f'Bearer {tok}'})
    assert res.status_code == 403 or res.status_code == 401

    # promote to teacher and re-login
    from backend.models import User, db as _db
    with client.application.app_context():
        u = User.query.filter_by(email='stu@example.com').first()
        u.role = 'teacher'
        _db.session.commit()

    login2 = client.post('/api/auth/login', json={'email': 'stu@example.com', 'password': 'Password1!'})
    tok2 = login2.get_json()['token']
    res2 = client.get('/api/teacher/reports', headers={'Authorization': f'Bearer {tok2}'})
    # teacher endpoint should now be accessible (may return empty list)
    assert res2.status_code == 200 or res2.status_code == 204 or res2.status_code == 200
