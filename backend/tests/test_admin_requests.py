import json
import pytest

from backend.app import create_app
from backend.models import db, TeacherRequest, User


@pytest.fixture
def app():
    app = create_app('testing')
    app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'EMAIL_BACKEND': 'memory',
    })
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def cleanup_db(app):
    """Ensure DB tables are clean before each test to avoid cross-test contamination."""
    with app.app_context():
        # Remove all teacher requests and users before each test
        db.session.query(TeacherRequest).delete()
        db.session.query(User).delete()
        db.session.commit()
    yield
    with app.app_context():
        db.session.query(TeacherRequest).delete()
        db.session.query(User).delete()
        db.session.commit()


def test_create_and_approve_request(client):
    # Create a teacher request
    payload = {
        'name': 'Alice Teacher',
        'email': 'alice@example.com',
        'password': 'secret',
        'grade': 'middle'
    }
    r = client.post('/api/admin/teacher-requests', json=payload)
    assert r.status_code == 201
    data = r.get_json()
    assert data['message'] == 'Request submitted'
    req_id = data['request']['id']

    # List (should be pending) using paginated response
    r = client.get('/api/admin/teacher-requests', headers={'X-Admin-Token': client.application.config.get('ADMIN_TOKEN')})
    assert r.status_code == 200
    arr = r.get_json()
    # ensure pagination structure
    assert 'items' in arr
    assert any(item['id'] == req_id for item in arr['items'])

    # Approve
    r = client.post(f'/api/admin/teacher-requests/{req_id}/approve', headers={'X-Admin-Token': client.application.config.get('ADMIN_TOKEN')})
    assert r.status_code == 200
    data = r.get_json()
    assert data['message'] == 'approved'

    # Verify user exists
    with client.application.app_context():
        u = User.query.filter_by(email='alice@example.com').first()
        assert u is not None
        # Email sent (memory backend)
        sent = client.application.extensions.get('sent_emails', [])
        assert any(e['to'] == 'alice@example.com' and 'approved' in e['subject'].lower() for e in sent)


def test_reject_request_sends_email(client):
    payload = {'name': 'Bob', 'email': 'bob@example.com', 'password': 'x', 'grade': 'elementary'}
    r = client.post('/api/admin/teacher-requests', json=payload)
    assert r.status_code == 201
    req_id = r.get_json()['request']['id']

    # Reject
    r = client.post(f'/api/admin/teacher-requests/{req_id}/reject', headers={'X-Admin-Token': client.application.config.get('ADMIN_TOKEN')})
    assert r.status_code == 200
    assert r.get_json()['message'] == 'rejected'

    # Ensure status updated and email sent
    with client.application.app_context():
        tr = db.session.get(TeacherRequest, req_id)
        assert tr.status == 'rejected'
        sent = client.application.extensions.get('sent_emails', [])
        assert any(e['to'] == 'bob@example.com' and 'rejected' in e['subject'].lower() for e in sent)


def test_pagination_of_requests(client):
    # Create several requests
    for i in range(5):
        payload = {'name': f'T{i}', 'email': f't{i}@example.com', 'password': 'p', 'grade': 'college'}
        r = client.post('/api/admin/teacher-requests', json=payload)
        assert r.status_code == 201

    # Fetch page 2 with per_page=2
    r = client.get('/api/admin/teacher-requests?per_page=2&page=2', headers={'X-Admin-Token': client.application.config.get('ADMIN_TOKEN')})
    assert r.status_code == 200
    data = r.get_json()
    assert data['page'] == 2
    assert data['per_page'] == 2
    assert data['total'] == 5
    assert len(data['items']) == 2
