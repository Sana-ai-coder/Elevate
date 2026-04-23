"""
backend/routes/admin.py
=======================
Full admin API — all 10 tasks wired end-to-end.
Auth: X-Admin-Token header (existing) OR JWT Bearer with role='admin'.
Supabase-safe: uses SQLAlchemy ORM only, no raw PRAGMA/SQLite calls.
"""

import csv
import re
import os
import random
import string
from datetime import datetime, timedelta
from io import StringIO
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, make_response
from sqlalchemy import func, or_

from ..models import (
    AuditLog,
    AnswerLog,
    Classroom,
    ClassroomStudent,
    db,
    EmotionLog,
    MCQPipelineEvent,
    ModelVersion,
    Question,
    School,
    SyllabusTopic,
    TeacherRequest,
    Test,
    TestResult,
    TrainingJob,
    User,
    utcnow,
)
from ..security import decode_token, get_token_from_request, hash_password
from ..validation import sanitize_string
from ..notifications import send_email
from ..hf_training_service import (
    get_hf_strict_training_status,
    get_hf_training_service_url,
    start_hf_strict_training,
)

admin_bp = Blueprint("admin", __name__)

# ─── Auth helpers ────────────────────────────────────────────────────────────

def _check_admin_token():
    """Accept either the legacy X-Admin-Token header OR a valid admin JWT."""
    # 1. Legacy static token (kept for CI/scripts)
    token = request.headers.get("X-Admin-Token")
    expected = current_app.config.get("ADMIN_TOKEN")
    if token and expected and token == expected:
        return True

    # 2. JWT with role == admin
    jwt_raw = get_token_from_request()
    if jwt_raw:
        payload = decode_token(jwt_raw)
        if payload and payload.get("role") == "admin":
            user_id = int(payload.get("sub", 0))
            user = db.session.get(User, user_id)
            if user and user.role == "admin":
                g.current_user = user
                return True
    return False


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _check_admin_token():
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ─── Audit helper ─────────────────────────────────────────────────────────────

def _audit(action, target_type=None, target_id=None, target_label=None,
           before=None, after=None):
    """Write one audit log row. Never raises — failures are logged only."""
    try:
        admin = getattr(g, "current_user", None)
        entry = AuditLog(
            admin_id=admin.id if admin else None,
            admin_email=admin.email if admin else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            before_value=before,
            after_value=after,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "")[:512],
        )
        db.session.add(entry)
        db.session.flush()   # part of current transaction; committed by caller
    except Exception as exc:
        current_app.logger.warning("[AuditLog] write failed: %s", exc)


# ─── 1. Platform stats ────────────────────────────────────────────────────────

@admin_bp.get("/stats")
@admin_required
def stats():
    return jsonify({
        "users": User.query.count(),
        "questions": Question.query.count(),
        "emotion_logs": EmotionLog.query.count(),
        "answer_logs": AnswerLog.query.count(),
        "schools": School.query.count(),
        "tests": Test.query.count(),
        "test_results": TestResult.query.count(),
    })


# ─── 2. User management ───────────────────────────────────────────────────────

@admin_bp.get("/users")
@admin_required
def list_users():
    """
    GET /api/admin/users
    Query params: role, school_id, search (name/email), is_active,
                  page (default 1), per_page (default 20, max 100)
    """
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = min(100, max(1, int(request.args.get("per_page", 20) or 20)))

    q = User.query

    role = request.args.get("role")
    if role:
        q = q.filter(User.role == role)

    school_id = request.args.get("school_id")
    if school_id:
        q = q.filter(User.school_id == int(school_id))

    search = (request.args.get("search") or "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(or_(User.name.ilike(like), User.email.ilike(like)))

    is_active = request.args.get("is_active")
    if is_active is not None:
        q = q.filter(User.is_verified == (is_active.lower() == "true"))

    pag = q.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    items = []
    for u in pag.items:
        d = u.as_dict()
        d["school_name"] = u.school.name if u.school else None
        items.append(d)

    return jsonify({
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": pag.total,
        "pages": pag.pages,
    })


@admin_bp.patch("/users/<int:uid>")
@admin_required
def update_user(uid):
    """
    PATCH /api/admin/users/<uid>
    Body: { role, school_id, is_verified, name }
    Allowed changes: role, school_id, is_verified (enable/disable), name.
    """
    u = db.session.get(User, uid)
    if not u:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    before = u.as_dict()
    changed = {}

    if "role" in data:
        new_role = data["role"]
        if new_role not in ("student", "teacher", "admin"):
            return jsonify({"error": "invalid role"}), 400
        changed["role"] = (u.role, new_role)
        u.role = new_role

    if "school_id" in data:
        sid = data["school_id"]
        if sid is not None:
            school = db.session.get(School, int(sid))
            if not school:
                return jsonify({"error": "school not found"}), 404
        changed["school_id"] = (u.school_id, sid)
        u.school_id = sid

    if "is_verified" in data:
        changed["is_verified"] = (u.is_verified, bool(data["is_verified"]))
        u.is_verified = bool(data["is_verified"])

    if "name" in data:
        new_name = sanitize_string(data["name"], max_length=120)
        if new_name:
            changed["name"] = (u.name, new_name)
            u.name = new_name

    if not changed:
        return jsonify({"error": "no valid fields to update"}), 400

    _audit(
        action="user.updated",
        target_type="user",
        target_id=u.id,
        target_label=u.email,
        before=before,
        after=u.as_dict(),
    )
    db.session.commit()
    return jsonify({"user": u.as_dict()})


@admin_bp.delete("/users/<int:uid>")
@admin_required
def disable_user(uid):
    """Soft-disable: sets is_verified=False (does NOT delete the row)."""
    u = db.session.get(User, uid)
    if not u:
        return jsonify({"error": "not found"}), 404
    before = {"is_verified": u.is_verified}
    u.is_verified = False
    _audit("user.disabled", "user", u.id, u.email, before, {"is_verified": False})
    db.session.commit()
    return jsonify({"message": "user disabled"})

@admin_bp.get('/users/csv-template')
def get_csv_template():
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Email', 'Role', 'Grade'])
    writer.writerow(['John Doe', 'john.doe@example.com', 'student', '10'])
    writer.writerow(['Jane Smith', 'jane.smith@example.com', 'teacher', ''])
    
    from flask import make_response
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=elevate_users_template.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@admin_bp.post('/users/bulk-import')
def bulk_import_users():
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401
        
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
        
    file = request.files['file']
    
    # FIX 1: Safely get the admin user from the global context
    admin_user = getattr(g, "current_user", None)
    if not admin_user:
        return jsonify({"error": "Session expired or admin context lost."}), 401
    
    # FIX 2: Use StringIO directly matching your file's imports
    stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.DictReader(stream)
    
    updated_count = 0
    not_found_count = 0
    
    for row in csv_input:
        email = row.get('Email', '').strip().lower()
        role = row.get('Role', 'student').strip().lower()
        
        if not email:
            continue
            
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            # User exists! Link them to the school.
            existing_user.school_id = admin_user.school_id
            if role in ['student', 'teacher'] and existing_user.role != 'admin':
                existing_user.role = role
            updated_count += 1
        else:
            # User does not exist, ignore and count as not found
            not_found_count += 1
            
    db.session.commit()
    return jsonify({
        "message": "Import processed successfully", 
        "updated": updated_count,
        "not_found": not_found_count
    }), 200

@admin_bp.post('/users/single-add')
def single_add_user():
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401
        
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    name = data.get('name', '').strip()
    role = data.get('role', 'student').strip().lower()
    
    # FIX 1: Safely get the admin user from the global context
    admin_user = getattr(g, "current_user", None)
    if not admin_user:
        return jsonify({"error": "Session expired or admin context lost."}), 401
    
    exact_user = User.query.filter_by(email=email).first()
    if exact_user:
        exact_user.school_id = admin_user.school_id
        if exact_user.role != 'admin':
            exact_user.role = role
        db.session.commit()
        return jsonify({"status": "linked", "message": f"Success! {exact_user.name} has been added to your school."}), 200
        
    similar_user = User.query.filter(User.name.ilike(f"%{name}%")).first()
    if similar_user:
        parts = similar_user.email.split('@')
        masked = parts[0][:3] + "***@" + parts[1] if len(parts[0]) > 3 else "***@" + parts[1]
        return jsonify({
            "status": "similar_found", 
            "error": f"We could not find the exact email '{email}'.",
            "suggestion": f"However, a user named '{similar_user.name}' ({masked}) exists. Is this who you meant? Please verify their exact email and try again."
        }), 409 

    return jsonify({
        "error": f"No user named '{name}' is registered on Elevate. They must sign up for an account first before you can add them to your school."
    }), 400

# ─── 3. School hierarchy ──────────────────────────────────────────────────────

@admin_bp.get("/schools")
@admin_required
def list_schools():
    items = [s.as_dict() for s in School.query.order_by(School.created_at.desc()).all()]
    return jsonify({"items": items})


@admin_bp.get("/schools/<int:sid>/hierarchy")
@admin_required
def school_hierarchy(sid):
    """
    Returns { school, teachers: [{...teacher, classrooms:[...], students:[...]}] }
    Full nested view consumed by the School Hierarchy panel.
    """
    school = db.session.get(School, sid)
    if not school:
        return jsonify({"error": "not found"}), 404

    teachers = User.query.filter_by(school_id=sid, role="teacher").all()
    result_teachers = []

    for t in teachers:
        classrooms_q = Classroom.query.filter_by(teacher_id=t.id, school_id=sid).all()
        class_list = []
        for c in classrooms_q:
            memberships = (
                ClassroomStudent.query
                .filter_by(classroom_id=c.id, is_active=True)
                .all()
            )
            students = []
            for m in memberships:
                s = db.session.get(User, m.student_id)
                if s:
                    students.append({"id": s.id, "name": s.name, "email": s.email, "grade": s.grade})
            class_list.append({**c.as_dict(), "students": students})
        result_teachers.append({
            **t.as_dict(),
            "classrooms": class_list,
        })

    # Students directly in the school but not in any classroom
    all_student_ids_in_classrooms = set()
    for t_data in result_teachers:
        for c in t_data["classrooms"]:
            for s in c["students"]:
                all_student_ids_in_classrooms.add(s["id"])

    unassigned_students = (
        User.query
        .filter_by(school_id=sid, role="student")
        .filter(User.id.notin_(all_student_ids_in_classrooms))
        .all()
    )

    return jsonify({
        "school": school.as_dict(),
        "teachers": result_teachers,
        "unassigned_students": [
            {"id": s.id, "name": s.name, "email": s.email, "grade": s.grade}
            for s in unassigned_students
        ],
    })


@admin_bp.post("/schools")
@admin_required
def create_school():
    data = request.get_json(silent=True) or {}
    name = sanitize_string(data.get("name") or "", max_length=255)
    slug = sanitize_string(data.get("slug") or "", max_length=128).lower()
    slug = re.sub(r"[^a-z0-9\s_-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    if not name:
        return jsonify({"error": "name required"}), 400
    if not slug:
        return jsonify({"error": "slug required"}), 400
    if School.query.filter(func.lower(School.name) == name.lower()).first():
        return jsonify({"error": "school already exists"}), 409
    if School.query.filter(func.lower(School.slug) == slug.lower()).first():
        return jsonify({"error": "slug already exists"}), 409
    s = School(name=name, slug=slug)
    db.session.add(s)
    _audit("school.created", "school", None, name, None, {"name": name, "slug": slug})
    db.session.commit()
    return jsonify({"school": s.as_dict()}), 201


@admin_bp.delete("/schools/<int:sid>")
@admin_required
def delete_school(sid):
    s = db.session.get(School, sid)
    if not s:
        return jsonify({"error": "not found"}), 404
    _audit("school.deleted", "school", s.id, s.name, s.as_dict(), None)
    db.session.delete(s)
    db.session.commit()
    return jsonify({"message": "deleted"})

@admin_bp.get('/schools/hierarchy')
def get_schools_hierarchy():
    if not _check_admin_token():
        return {"error": "unauthorized"}, 401
    
    try:
        from ..models import School
        schools = School.query.all()
        # Return the basic school data needed for the dropdowns
        items = [{"id": s.id, "name": s.name, "slug": getattr(s, 'slug', '')} for s in schools]
        return jsonify({"items": items}), 200
    except Exception as e:
        # Fallback if School model isn't fully migrated yet
        return jsonify({"items": []}), 200

# ─── 4 & 5. Training monitor ──────────────────────────────────────────────────

@admin_bp.post("/ml/train-strict")
@admin_required
def trigger_strict_ml_training():
    result = start_hf_strict_training()

    admin_user = getattr(g, "current_user", None)
    
    # FIX: Use model_name to match PostgreSQL schema
    job = TrainingJob(
        job_id=result.get("payload", {}).get("job_id") if isinstance(result.get("payload"), dict) else None,
        model_name="emotion",  
        triggered_by=admin_user.id if admin_user else None,
        trigger_source="admin_ui",
        status="queued" if result.get("ok") else "failed",
        started_at=utcnow(),
        error_message=result.get("error") if not result.get("ok") else None,
    )
    db.session.add(job)
    _audit("ml.train_strict.triggered", "training_job", None, "emotion", None,
           {"job_id": job.job_id, "status": job.status})
    db.session.commit()

    if not result.get("ok"):
        return jsonify({
            "ok": False,
            "error": result.get("error") or "failed to trigger training",
            "db_job_id": job.id,
        }), int(result.get("status_code") or 502)

    return jsonify({
        "ok": True,
        "db_job_id": job.id,
        **(result.get("payload") if isinstance(result.get("payload"), dict) else {})
    }), 202

@admin_bp.get("/ml/train-strict/<job_id>")
@admin_required
def strict_ml_training_status(job_id):
    result = get_hf_strict_training_status(job_id)
    db_job = TrainingJob.query.filter_by(job_id=job_id).order_by(TrainingJob.created_at.desc()).first()
    
    if db_job and result.get("ok"):
        payload = result.get("payload") or {}
        remote_status = payload.get("status")
        if remote_status and db_job.status != remote_status:
            db_job.status = remote_status
        if remote_status in ("completed", "failed") and not db_job.finished_at:
            db_job.finished_at = utcnow()
            if db_job.started_at:
                db_job.duration_seconds = int((db_job.finished_at - db_job.started_at).total_seconds())
        if payload.get("metrics"):
            db_job.metrics = payload["metrics"]
        if payload.get("logs"):
            db_job.logs = str(payload["logs"])[:8000]
        db.session.commit()

    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error") or "failed"}), int(result.get("status_code") or 502)

    return jsonify({"ok": True, "db_job": db_job.as_dict() if db_job else None, **(result.get("payload") or {})})

@admin_bp.get("/ml/training-jobs")
@admin_required
def list_training_jobs():
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = min(100, max(1, int(request.args.get("per_page", 20) or 20)))
    
    q = TrainingJob.query
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    if request.args.get("model_name"):
        q = q.filter_by(model_name=request.args["model_name"])
        
    pag = q.order_by(TrainingJob.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    items = []
    for j in pag.items:
        items.append(j.as_dict())

    return jsonify({"items": items, "page": page, "per_page": per_page, "total": pag.total, "pages": pag.pages})

@admin_bp.get("/ml/training-jobs/<int:jid>")
@admin_required
def get_training_job_detail(jid):
    j = db.session.get(TrainingJob, jid)
    if not j:
        return jsonify({"error": "not found"}), 404
    return jsonify({"job": j.as_dict()})

# ─── 6. Model version registry ───────────────────────────────────────────────

@admin_bp.get("/ml/model-versions")
@admin_required
def list_model_versions():
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = min(100, max(1, int(request.args.get("per_page", 20) or 20)))
    
    # FIX: Use model_name for UI payload parsing and DB querying
    model_name = request.args.get("model_name") or request.args.get("model_type")
    q = ModelVersion.query
    if model_name:
        q = q.filter_by(model_name=model_name)
        
    pag = q.order_by(ModelVersion.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    items = []
    for v in pag.items:
        d = v.as_dict()
        d["model_name"] = getattr(v, "model_name", "unknown")
        d["is_production"] = (getattr(v, "status", "") == "production")
        d["is_rollback_candidate"] = (getattr(v, "status", "") == "rollback_candidate")
        items.append(d)

    return jsonify({"items": items, "page": page, "per_page": per_page, "total": pag.total, "pages": pag.pages})

@admin_bp.get("/ml/model-versions/registry-summary")
@admin_required
def registry_summary():
    model_names = db.session.query(ModelVersion.model_name).distinct().all()
    items = []
    for (m_name,) in model_names:
        if not m_name: continue
        total = ModelVersion.query.filter_by(model_name=m_name).count()
        current = ModelVersion.query.filter_by(model_name=m_name, status="production").order_by(ModelVersion.created_at.desc()).first()
        rollback = ModelVersion.query.filter_by(model_name=m_name, status="rollback_candidate").order_by(ModelVersion.created_at.desc()).first()
        previous = ModelVersion.query.filter_by(model_name=m_name, status="archived").order_by(ModelVersion.promoted_at.desc()).first()

        items.append({
            "model_name": m_name,
            "total_versions": total,
            "current_production": {"version_tag": current.version_tag} if current else None,
            "previous": {"version_tag": previous.version_tag} if previous else None,
            "rollback_target": {"version_tag": rollback.version_tag} if rollback else None
        })
    return jsonify({"items": items})

@admin_bp.post("/ml/model-versions")
@admin_required
def register_model_version():
    data = request.get_json(silent=True) or {}
    model_name = data.get("model_name") or data.get("model_type")
    version_tag = data.get("version_tag")
    if not model_name or not version_tag:
        return jsonify({"error": "Model Name and Version Tag required"}), 400

    v = ModelVersion(
        model_name=model_name, 
        version_tag=version_tag, 
        status="staging", 
        notes=data.get("notes"),
        artifact_path=data.get("artifact_uri")
    )
    db.session.add(v)
    _audit("model_version.registered", "model_version", None, f"{model_name}:{version_tag}", None, None)
    db.session.commit()
    return jsonify({"version": v.as_dict()}), 201

@admin_bp.post("/ml/model-versions/<int:vid>/promote")
@admin_required
def promote_model_version(vid):
    v = db.session.get(ModelVersion, vid)
    if not v:
        return jsonify({"error": "not found"}), 404

    old_prod = ModelVersion.query.filter_by(model_name=v.model_name, status="production").all()
    for old in old_prod:
        old.status = "archived"

    v.status = "production"
    admin_user = getattr(g, "current_user", None)
    v.promoted_by = admin_user.id if admin_user else None
    v.promoted_at = utcnow()
    
    _audit("model_version.promoted", "model_version", v.id, f"{v.model_name}:{v.version_tag}", None, {"status": "production"})
    db.session.commit()
    return jsonify({"version": v.as_dict()})

@admin_bp.post("/ml/model-versions/<int:vid>/rollback")
@admin_required
def rollback_model_version(vid):
    v = db.session.get(ModelVersion, vid)
    if not v:
        return jsonify({"error": "not found"}), 404

    old_rollbacks = ModelVersion.query.filter_by(model_name=v.model_name, status="rollback_candidate").all()
    for old in old_rollbacks:
        old.status = "archived"

    v.status = "rollback_candidate"
    _audit("model_version.rollback", "model_version", v.id, f"{v.model_name}:{v.version_tag}", None, {"status": "rollback_candidate"})
    db.session.commit()
    return jsonify({"version": v.as_dict(), "message": "rollback set"})

# ─── 7. MCQ pipeline observability ───────────────────────────────────────────

@admin_bp.get("/mcq/observability")
@admin_required
def mcq_observability():
    days = int(request.args.get("days", 30))
    since = utcnow() - timedelta(days=days)
    q = MCQPipelineEvent.query.filter(MCQPipelineEvent.created_at >= since)
    if request.args.get("subject"):
        q = q.filter_by(subject=request.args["subject"])

    events = q.all()
    total = len(events)
    success = sum(1 for e in events if e.outcome == "success")
    failed = sum(1 for e in events if e.outcome == "failed")
    fallback = sum(1 for e in events if e.fallback_used)

    latencies = [e.latency_ms for e in events if e.latency_ms]
    avg_latency = sum(latencies)/len(latencies) if latencies else 0
    gen_tot = sum(e.generated_count for e in events if e.generated_count)
    
    ts_dict = {}
    by_mode = {}
    for e in events:
        date_str = e.created_at.strftime("%Y-%m-%d")
        if date_str not in ts_dict:
            ts_dict[date_str] = {"date": date_str, "success": 0, "failure": 0, "fallback": 0}
        
        if e.outcome == "success": ts_dict[date_str]["success"] += 1
        if e.outcome == "failed": ts_dict[date_str]["failure"] += 1
        if e.fallback_used: ts_dict[date_str]["fallback"] += 1

        mode = getattr(e, "generation_mode", "standard") or "standard"
        by_mode[mode] = by_mode.get(mode, 0) + 1

    return jsonify({
        "summary": {
            "total": total,
            "success_rate": round((success/total*100) if total else 0, 1),
            "failure_rate": round((failed/total*100) if total else 0, 1),
            "fallback_rate": round((fallback/total*100) if total else 0, 1),
            "questions_generated_total": int(gen_tot),
            "questions_requested_total": int(gen_tot) + failed,
            "avg_latency_ms": round(avg_latency, 1)
        },
        "time_series": sorted(list(ts_dict.values()), key=lambda x: x["date"]),
        "by_mode": by_mode
    })


# ─── 8. Audit trail ──────────────────────────────────────────────────────────

@admin_bp.get("/audit-logs")
@admin_required
def list_audit_logs():
    """
    GET /api/admin/audit-logs
    Query params: action, target_type, admin_id, days, page, per_page
    """
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = min(100, max(1, int(request.args.get("per_page", 50) or 50)))

    q = AuditLog.query

    if request.args.get("action"):
        q = q.filter(AuditLog.action.ilike(f"%{request.args['action']}%"))
    if request.args.get("target_type"):
        q = q.filter_by(target_type=request.args["target_type"])
    if request.args.get("admin_id"):
        q = q.filter_by(admin_id=int(request.args["admin_id"]))
    days = request.args.get("days")
    if days:
        since = utcnow() - timedelta(days=int(days))
        q = q.filter(AuditLog.created_at >= since)

    pag = q.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        "items": [e.as_dict() for e in pag.items],
        "page": page,
        "per_page": per_page,
        "total": pag.total,
        "pages": pag.pages,
    })


# ─── 9 & 10. Test results — paginated, server-side filtered ──────────────────

@admin_bp.get("/test-results")
@admin_required
def list_test_results():
    """
    GET /api/admin/test-results
    Query params: start (ISO date), end (ISO date), subject, user_id,
                  school_id, format (csv), page, per_page
    """
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = min(200, max(1, int(request.args.get("per_page", 25) or 25)))
    fmt = request.args.get("format")

    q = TestResult.query

    start = request.args.get("start")
    if start:
        try:
            q = q.filter(TestResult.started_at >= datetime.fromisoformat(start))
        except ValueError:
            pass

    end = request.args.get("end")
    if end:
        try:
            q = q.filter(TestResult.started_at < datetime.fromisoformat(end) + timedelta(days=1))
        except ValueError:
            pass

    subject = request.args.get("subject")
    if subject:
        q = q.filter(TestResult.subject == subject)

    user_id = request.args.get("user_id")
    if user_id:
        q = q.filter(TestResult.user_id == int(user_id))

    school_id = request.args.get("school_id")
    if school_id:
        q = q.join(User, User.id == TestResult.user_id).filter(
            User.school_id == int(school_id)
        )

    # For CSV export we skip pagination and stream all results
    if fmt == "csv":
        rows = q.order_by(TestResult.started_at.desc()).all()
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow([
            "id", "user_id", "user_email", "test_id", "subject",
            "total_questions", "correct_answers", "score_pct",
            "avg_time_per_question", "started_at", "completed_at",
        ])
        for tr in rows:
            u = db.session.get(User, tr.user_id)
            score_pct = (
                round(tr.correct_answers / tr.total_questions * 100, 1)
                if tr.total_questions else 0
            )
            writer.writerow([
                tr.id,
                tr.user_id,
                u.email if u else "",
                tr.test_id,
                tr.subject,
                tr.total_questions,
                tr.correct_answers,
                score_pct,
                tr.average_time_per_question,
                tr.started_at.isoformat() if tr.started_at else "",
                tr.completed_at.isoformat() if getattr(tr, "completed_at", None) else "",
            ])
        return si.getvalue(), 200, {"Content-Type": "text/csv; charset=utf-8"}

    pag = q.order_by(TestResult.started_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    items = []
    for tr in pag.items:
        u = db.session.get(User, tr.user_id)
        d = tr.as_dict()
        d["user"] = {"id": u.id, "email": u.email, "name": u.name} if u else None
        d["score_pct"] = (
            round(tr.correct_answers / tr.total_questions * 100, 1)
            if tr.total_questions else 0
        )
        items.append(d)

    return jsonify({
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": pag.total,
        "pages": pag.pages,
    })


@admin_bp.get("/test-results/<int:uid>/history")
@admin_required
def test_results_history(uid):
    u = db.session.get(User, uid)
    if not u:
        return jsonify({"error": "not found"}), 404
    items = [
        tr.as_dict()
        for tr in TestResult.query.filter_by(user_id=uid)
        .order_by(TestResult.started_at.desc())
        .all()
    ]
    return jsonify({"user": u.as_dict(), "items": items})


# ─── Teacher requests (unchanged, kept for compatibility) ────────────────────

@admin_bp.post("/teacher-requests")
def create_teacher_request():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    email = data.get("email")
    password = data.get("password")
    grade = data.get("grade")
    if not name or not email or not password:
        return jsonify({"error": "Missing required fields"}), 400
    if (
        User.query.filter_by(email=email).first()
        or TeacherRequest.query.filter_by(email=email, status="pending").first()
    ):
        return jsonify({"error": "Email already registered or pending"}), 409
    tr = TeacherRequest(
        name=name, email=email,
        password_hash=hash_password(password), grade=grade
    )
    db.session.add(tr)
    db.session.commit()
    return jsonify({"message": "Request submitted", "request": tr.as_dict()}), 201


@admin_bp.get("/teacher-requests")
@admin_required
def list_teacher_requests():
    page = max(1, int(request.args.get("page", 1) or 1))
    per_page = min(100, max(1, int(request.args.get("per_page", 20) or 20)))
    status = request.args.get("status", "pending")
    q = TeacherRequest.query
    if status:
        q = q.filter_by(status=status)
    pag = q.order_by(TeacherRequest.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify({
        "items": [r.as_dict() for r in pag.items],
        "page": page, "per_page": per_page,
        "total": pag.total,
    })


@admin_bp.post("/teacher-requests/<int:req_id>/approve")
@admin_required
def approve_teacher_request(req_id):
    tr = db.session.get(TeacherRequest, req_id)
    if not tr:
        return jsonify({"error": "not found"}), 404
    if tr.status != "pending":
        return jsonify({"error": "already processed"}), 400
    if User.query.filter_by(email=tr.email).first():
        tr.status = "rejected"
        db.session.commit()
        return jsonify({"error": "user already exists, request rejected"}), 409
    user = User(
        name=tr.name, email=tr.email,
        password_hash=tr.password_hash, grade=tr.grade,
        role="teacher", is_verified=True
    )
    db.session.add(user)
    tr.status = "approved"
    _audit("teacher_request.approved", "user", None, tr.email, None,
           {"email": tr.email, "role": "teacher"})
    db.session.commit()
    send_email(
        tr.email,
        "Your Elevate teacher request has been approved",
        f"Hello {tr.name},\n\nYour teacher account is approved. Log in at {tr.email}.\n\nElevate Team",
    )
    return jsonify({"message": "approved", "user": {"id": user.id, "email": user.email}})


@admin_bp.post("/teacher-requests/<int:req_id>/reject")
@admin_required
def reject_teacher_request(req_id):
    tr = db.session.get(TeacherRequest, req_id)
    if not tr:
        return jsonify({"error": "not found"}), 404
    if tr.status != "pending":
        return jsonify({"error": "already processed"}), 400
    tr.status = "rejected"
    _audit("teacher_request.rejected", "user", None, tr.email, None, {"status": "rejected"})
    db.session.commit()
    send_email(
        tr.email,
        "Your Elevate teacher request",
        f"Hello {tr.name},\n\nWe could not approve your request at this time.\n\nElevate Team",
    )
    return jsonify({"message": "rejected"})


# ─── Syllabus topics (unchanged) ─────────────────────────────────────────────

@admin_bp.post("/syllabus-topics")
@admin_required
def create_syllabus_topic():
    data = request.get_json(silent=True) or {}
    subject = data.get("subject")
    slug = data.get("slug")
    if not subject or not slug:
        return jsonify({"error": "subject and slug required"}), 400
    if SyllabusTopic.query.filter_by(slug=slug).first():
        return jsonify({"error": "slug exists"}), 409
    t = SyllabusTopic(
        subject=subject,
        grade=data.get("grade"),
        slug=slug,
        title=data.get("title"),
        description=data.get("description"),
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({"topic": t.as_dict()}), 201


@admin_bp.get("/syllabus-topics")
@admin_required
def list_syllabus_topics():
    q = SyllabusTopic.query
    if request.args.get("subject"):
        q = q.filter_by(subject=request.args["subject"])
    if request.args.get("grade"):
        q = q.filter_by(grade=request.args["grade"])
    items = [t.as_dict() for t in q.order_by(SyllabusTopic.created_at.desc()).all()]
    return jsonify({"items": items})


@admin_bp.delete("/syllabus-topics/<int:topic_id>")
@admin_required
def delete_syllabus_topic(topic_id):
    t = db.session.get(SyllabusTopic, topic_id)
    if not t:
        return jsonify({"error": "not found"}), 404
    db.session.delete(t)
    db.session.commit()
    return jsonify({"message": "deleted"})