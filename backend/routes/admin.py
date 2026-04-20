from flask import Blueprint, current_app, request, jsonify
import re
from ..models import db, User, Question, EmotionLog, AnswerLog, TeacherRequest, School, SyllabusTopic, TestResult
from ..security import hash_password
from ..validation import sanitize_string
from ..notifications import send_email
from ..hf_training_service import (
    get_hf_strict_training_status,
    get_hf_training_service_url,
    start_hf_strict_training,
)
from sqlalchemy import func
import csv
from io import StringIO

admin_bp = Blueprint("admin", __name__)


def _check_admin_token():
    token = request.headers.get("X-Admin-Token")
    expected = current_app.config.get("ADMIN_TOKEN")
    return token and expected and token == expected


@admin_bp.get("/stats")
def stats():
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401

    user_count = User.query.count()
    question_count = Question.query.count()
    emotion_logs = EmotionLog.query.count()
    answer_logs = AnswerLog.query.count()

    return {
        "users": user_count,
        "questions": question_count,
        "emotion_logs": emotion_logs,
        "answer_logs": answer_logs,
    }


@admin_bp.post('/teacher-requests')
def create_teacher_request():
    """Create a teacher request (public endpoint)."""
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    grade = data.get('grade')

    if not name or not email or not password:
        return jsonify({'error': 'Missing required fields'}), 400

    # Prevent duplicate accounts
    if User.query.filter_by(email=email).first() or TeacherRequest.query.filter_by(email=email, status='pending').first():
        return jsonify({'error': 'Email already registered or pending'}), 409

    tr = TeacherRequest(name=name, email=email, password_hash=hash_password(password), grade=grade)
    db.session.add(tr)
    db.session.commit()
    current_app.logger.info(f'Teacher request created: {email}')
    return jsonify({'message': 'Request submitted', 'request': tr.as_dict()}), 201


@admin_bp.get('/teacher-requests')
def list_teacher_requests():
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401

    # Filtering & pagination
    try:
        page = int(request.args.get('page', 1))
    except Exception:
        page = 1
    try:
        per_page = int(request.args.get('per_page', 20))
    except Exception:
        per_page = 20
    per_page = min(max(per_page, 1), 100)

    status = request.args.get('status', 'pending')
    q = TeacherRequest.query
    if status:
        q = q.filter_by(status=status)

    pag = q.order_by(TeacherRequest.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    items = [r.as_dict() for r in pag.items]
    return jsonify({
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': pag.total,
    })


@admin_bp.post('/teacher-requests/<int:req_id>/approve')
def approve_teacher_request(req_id):
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401
    tr = db.session.get(TeacherRequest, req_id)
    if not tr:
        return jsonify({'error': 'not found'}), 404
    if tr.status != 'pending':
        return jsonify({'error': 'already processed'}), 400

    # Create user account from request
    if User.query.filter_by(email=tr.email).first():
        tr.status = 'rejected'
        db.session.commit()
        return jsonify({'error': 'user already exists, request rejected'}), 409

    user = User(name=tr.name, email=tr.email, password_hash=tr.password_hash, grade=tr.grade, is_verified=True)
    db.session.add(user)
    tr.status = 'approved'
    db.session.commit()
    current_app.logger.info(f'Teacher request approved and user created: {tr.email}')

    # Notify requester
    subject = 'Your Elevate teacher request has been approved'
    body = f"Hello {tr.name},\n\nYour teacher account has been approved. You may now log in using {tr.email}.\n\nRegards,\nElevate Team"
    send_email(tr.email, subject, body)

    return jsonify({'message': 'approved', 'user': {'id': user.id, 'email': user.email, 'name': user.name}})


@admin_bp.post('/teacher-requests/<int:req_id>/reject')
def reject_teacher_request(req_id):
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401
    tr = db.session.get(TeacherRequest, req_id)
    if not tr:
        return jsonify({'error': 'not found'}), 404
    if tr.status != 'pending':
        return jsonify({'error': 'already processed'}), 400
    tr.status = 'rejected'
    db.session.commit()
    current_app.logger.info(f'Teacher request rejected: {tr.email}')

    # Notify requester
    subject = 'Your Elevate teacher request has been rejected'
    body = f"Hello {tr.name},\n\nWe reviewed your teacher access request but could not approve it at this time.\n\nRegards,\nElevate Team"
    send_email(tr.email, subject, body)

    return jsonify({'message': 'rejected'})


@admin_bp.get('/debug-db')
def debug_db():
    """Temporary debug route: returns DB URL, users table columns, and alembic version."""
    from sqlalchemy import text
    try:
        db_url = current_app.config.get('SQLALCHEMY_DATABASE_URI')
        with db.engine.connect() as conn:
            raw_cols = conn.execute(text("PRAGMA table_info('users');")).fetchall()
            cols = []
            for r in raw_cols:
                # PRAGMA returns: cid, name, type, notnull, dflt_value, pk
                cols.append({
                    'cid': r[0],
                    'name': r[1],
                    'type': r[2],
                    'notnull': r[3],
                    'default': r[4],
                    'pk': r[5]
                })
            try:
                ver_row = conn.execute(text("SELECT version_num FROM alembic_version; ")).fetchone()
                ver = ver_row[0] if ver_row else None
            except Exception:
                ver = None
        return jsonify({'db_url': db_url, 'users_columns': cols, 'alembic_version': ver})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Admin Schools endpoints ---
@admin_bp.post('/schools')
def create_school():
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    data = request.get_json(silent=True) or {}
    name = sanitize_string(data.get('name') or '', max_length=255)
    slug = sanitize_string(data.get('slug') or '', max_length=128).lower()
    slug = re.sub(r"[^a-z0-9\s_-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    if not name:
        return jsonify({'error': 'name required'}), 400
    if not slug:
        return jsonify({'error': 'slug required'}), 400
    if School.query.filter(func.lower(School.name) == name.lower()).first():
        return jsonify({'error': 'already exists'}), 409
    if School.query.filter(func.lower(School.slug) == slug.lower()).first():
        return jsonify({'error': 'slug exists'}), 409
    s = School(name=name, slug=slug)
    db.session.add(s)
    db.session.commit()
    return jsonify({'school': s.as_dict()}), 201


@admin_bp.get('/schools')
def list_schools():
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    items = [s.as_dict() for s in School.query.order_by(School.created_at.desc()).all()]
    return jsonify({'items': items}), 200


@admin_bp.delete('/schools/<int:sid>')
def delete_school(sid):
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    s = db.session.get(School, sid)
    if not s:
        return jsonify({'error': 'not found'}), 404
    db.session.delete(s)
    db.session.commit()
    return jsonify({'message': 'deleted'}), 200


# --- Syllabus topics ---
@admin_bp.post('/syllabus-topics')
def create_syllabus_topic():
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    data = request.get_json(silent=True) or {}
    subject = data.get('subject')
    grade = data.get('grade')
    slug = data.get('slug')
    title = data.get('title')
    description = data.get('description')
    if not subject or not slug:
        return jsonify({'error': 'subject and slug required'}), 400
    if SyllabusTopic.query.filter_by(slug=slug).first():
        return jsonify({'error': 'slug exists'}), 409
    t = SyllabusTopic(subject=subject, grade=grade, slug=slug, title=title, description=description)
    db.session.add(t)
    db.session.commit()
    return jsonify({'topic': t.as_dict()}), 201


@admin_bp.get('/syllabus-topics')
def list_syllabus_topics():
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    subject = request.args.get('subject')
    grade = request.args.get('grade')
    q = SyllabusTopic.query
    if subject:
        q = q.filter_by(subject=subject)
    if grade:
        q = q.filter_by(grade=grade)
    items = [t.as_dict() for t in q.order_by(SyllabusTopic.created_at.desc()).all()]
    return jsonify({'items': items}), 200


@admin_bp.delete('/syllabus-topics/<int:topic_id>')
def delete_syllabus_topic(topic_id):
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    t = db.session.get(SyllabusTopic, topic_id)
    if not t:
        return jsonify({'error': 'not found'}), 404
    db.session.delete(t)
    db.session.commit()
    return jsonify({'message': 'deleted'}), 200


# --- Test results and CSV export ---
@admin_bp.get('/test-results')
def list_test_results():
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    fmt = request.args.get('format')
    start = request.args.get('start')
    end = request.args.get('end')
    q = TestResult.query
    if start:
        try:
            from datetime import datetime
            s = datetime.fromisoformat(start)
            q = q.filter(TestResult.started_at >= s)
        except Exception:
            pass
    if end:
        try:
            from datetime import datetime, timedelta
            e = datetime.fromisoformat(end) + timedelta(days=1)
            q = q.filter(TestResult.started_at < e)
        except Exception:
            pass

    items = []
    for tr in q.order_by(TestResult.started_at.desc()).all():
        u = db.session.get(User, tr.user_id)
        items.append({
            'id': tr.id,
            'user': {'id': u.id, 'email': u.email},
            'test_id': tr.test_id,
            'subject': tr.subject,
            'total_questions': tr.total_questions,
            'correct_answers': tr.correct_answers,
            'avg_time_per_question': tr.average_time_per_question
        })

    if fmt == 'csv':
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(['id','user_email','test_id','subject','total_questions','correct_answers','avg_time_per_question'])
        for it in items:
            writer.writerow([it['id'], it['user']['email'], it['test_id'], it['subject'], it['total_questions'], it['correct_answers'], it['avg_time_per_question']])
        output = si.getvalue()
        return output, 200, {'Content-Type': 'text/csv; charset=utf-8'}

    return jsonify({'items': items}), 200


@admin_bp.get('/test-results/<int:user_id>/history')
def test_results_history(user_id):
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401
    u = db.session.get(User, user_id)
    if not u:
        return jsonify({'error': 'not found'}), 404
    items = [tr.as_dict() for tr in TestResult.query.filter_by(user_id=user_id).order_by(TestResult.started_at.desc()).all()]
    return jsonify({'items': items}), 200


@admin_bp.post('/ml/train-strict')
def trigger_strict_ml_training():
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401

    result = start_hf_strict_training()
    if not result.get('ok'):
        return jsonify({
            'ok': False,
            'service_url': get_hf_training_service_url(),
            'service_endpoint': result.get('endpoint'),
            'status_code': result.get('status_code'),
            'error': result.get('error') or 'failed to trigger strict HF training',
        }), int(result.get('status_code') or 502)

    payload = result.get('payload') if isinstance(result.get('payload'), dict) else {}
    return jsonify({
        'ok': True,
        'service_url': get_hf_training_service_url(),
        'service_endpoint': result.get('endpoint'),
        'service_latency_ms': result.get('latency_ms'),
        **payload,
    }), 202


@admin_bp.get('/ml/train-strict/<job_id>')
def strict_ml_training_status(job_id):
    if not _check_admin_token():
        return {'error': 'unauthorized'}, 401

    result = get_hf_strict_training_status(job_id)
    if not result.get('ok'):
        return jsonify({
            'ok': False,
            'service_url': get_hf_training_service_url(),
            'service_endpoint': result.get('endpoint'),
            'status_code': result.get('status_code'),
            'error': result.get('error') or 'failed to fetch strict HF training status',
        }), int(result.get('status_code') or 502)

    payload = result.get('payload') if isinstance(result.get('payload'), dict) else {}
    return jsonify({
        'ok': True,
        'service_url': get_hf_training_service_url(),
        'service_endpoint': result.get('endpoint'),
        'service_latency_ms': result.get('latency_ms'),
        **payload,
    }), 200
