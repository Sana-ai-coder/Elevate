from datetime import datetime, timezone
from backend.app import create_app
from backend.models import db, User, TestResult, School
import pytest


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

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


def test_teacher_sees_only_own_school_reports(client):
    with client.application.app_context():
        # create school
        s1 = School(name='Sunrise High', slug='sunrise')
        s2 = School(name='Other School', slug='other')
        db.session.add_all([s1, s2])
        db.session.commit()

        # create teacher via signup endpoint then update role and school
        signup = client.post('/api/auth/signup', json={'name': 'Ms Teacher', 'email': 'teacher@example.com', 'password': 'Password1!'})
        assert signup.status_code == 201
        # set role to teacher and assign school
        teacher = User.query.filter_by(email='teacher@example.com').first()
        teacher.role = 'teacher'
        teacher.school_id = s1.id
        db.session.commit()

        # create students in both schools
        st1 = User(name='Student One', email='s1@example.com', password_hash='x', role='student', school_id=s1.id, is_verified=True)
        st2 = User(name='Student Two', email='s2@example.com', password_hash='x', role='student', school_id=s1.id, is_verified=True)
        st3 = User(name='Other Student', email='o1@example.com', password_hash='x', role='student', school_id=s2.id, is_verified=True)
        db.session.add_all([st1, st2, st3])
        db.session.commit()

        # create test results
        tr1 = TestResult(user_id=st1.id, subject='Math', total_questions=10, correct_answers=8, average_time_per_question=5, started_at=_utcnow())
        tr2 = TestResult(user_id=st2.id, subject='Science', total_questions=8, correct_answers=6, average_time_per_question=6, started_at=_utcnow())
        tr3 = TestResult(user_id=st3.id, subject='Math', total_questions=10, correct_answers=2, average_time_per_question=10, started_at=_utcnow())
        db.session.add_all([tr1, tr2, tr3])
        db.session.commit()

    # login to get token
    login = client.post('/api/auth/login', json={'email': 'teacher@example.com', 'password': 'Password1!'})
    assert login.status_code == 200
    token = login.get_json()['token']

    # fetch reports as teacher
    res = client.get('/api/teacher/reports', headers={'Authorization': f'Bearer {token}'})
    assert res.status_code == 200
    items = res.get_json()['items']
    emails = [it['student_email'] for it in items]
    assert 's1@example.com' in emails
    assert 's2@example.com' in emails
    assert 'o1@example.com' not in emails


def test_teacher_create_classroom_auto_provisions_school(client):
    with client.application.app_context():
        signup = client.post('/api/auth/signup', json={
            'name': 'No School Teacher',
            'email': 'noschool@example.com',
            'password': 'Password1!'
        })
        assert signup.status_code == 201

        teacher = User.query.filter_by(email='noschool@example.com').first()
        teacher.role = 'teacher'
        teacher.school_id = None
        db.session.commit()

    login = client.post('/api/auth/login', json={'email': 'noschool@example.com', 'password': 'Password1!'})
    assert login.status_code == 200
    token = login.get_json()['token']

    create = client.post(
        '/api/teacher/classrooms',
        headers={'Authorization': f'Bearer {token}'},
        json={'name': 'Class 1', 'grade': 'high'},
    )
    assert create.status_code == 201, create.get_json()

    with client.application.app_context():
        refreshed = User.query.filter_by(email='noschool@example.com').first()
        assert refreshed.school_id is not None
