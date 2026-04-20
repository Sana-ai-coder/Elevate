from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    grade = db.Column(db.String(32), nullable=True)  # elementary, middle, high, college
    role = db.Column(db.String(32), default="student", nullable=False, index=True)  # 'student'|'teacher'|'admin'
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    reset_token = db.Column(db.String(255), nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # Teacher-specific fields
    assigned_subjects = db.Column(db.JSON, nullable=True)  # List of subjects teacher can teach
    
    school = db.relationship("School", back_populates="users")
    progress = db.relationship("UserProgress", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    test_results = db.relationship("TestResult", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    emotion_logs = db.relationship("EmotionLog", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    subject_performance = db.relationship("SubjectPerformance", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    answers = db.relationship("AnswerLog", back_populates="user", lazy="dynamic", cascade="all, delete-orphan")
    
    # Teacher relationships
    created_tests = db.relationship("Test", back_populates="created_by_teacher", lazy="dynamic")
    created_classrooms = db.relationship("Classroom", back_populates="teacher", lazy="dynamic", foreign_keys="Classroom.teacher_id")
    classroom_memberships = db.relationship("ClassroomStudent", back_populates="student", lazy="dynamic", foreign_keys="ClassroomStudent.student_id")
    assigned_tests = db.relationship("TestAssignment", back_populates="student", lazy="dynamic", foreign_keys="TestAssignment.student_id")
    created_assignments = db.relationship("TestAssignment", back_populates="teacher", lazy="dynamic", foreign_keys="TestAssignment.assigned_by")
    settings = db.relationship("UserSetting", back_populates="user", uselist=False, cascade="all, delete-orphan")
    
    def as_dict(self):
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'grade': self.grade,
            'role': self.role,
            'school_id': self.school_id,
            'is_verified': self.is_verified,
            'assigned_subjects': self.assigned_subjects,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class School(db.Model):
    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    slug = db.Column(db.String(128), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    users = db.relationship("User", back_populates="school", lazy="dynamic")
    classrooms = db.relationship("Classroom", back_populates="school", lazy="dynamic")

    def as_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(64), nullable=False, index=True)
    grade = db.Column(db.String(32), nullable=False, index=True)
    difficulty = db.Column(db.String(16), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    options = db.Column(db.JSON, nullable=False)
    correct_index = db.Column(db.Integer, nullable=False)
    hint = db.Column(db.Text, nullable=True)
    explanation = db.Column(db.Text, nullable=True)
    tags = db.Column(db.JSON, nullable=True)
    # Syllabus/topic metadata: topic slug and optional readability level
    syllabus_topic = db.Column(db.String(128), nullable=True, index=True)
    readability_level = db.Column(db.String(32), nullable=True)

    # Generation metadata
    is_generated = db.Column(db.Boolean, default=False, nullable=False, index=True)
    generated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    generation_meta = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    
    answers = db.relationship("AnswerLog", back_populates="question", lazy="dynamic")
    generated_by_user = db.relationship("User", primaryjoin="User.id==Question.generated_by", viewonly=True)


class UserProgress(db.Model):
    __tablename__ = "user_progress"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject = db.Column(db.String(64), nullable=False, index=True)
    total_questions = db.Column(db.Integer, default=0, nullable=False)
    correct_answers = db.Column(db.Integer, default=0, nullable=False)
    current_difficulty = db.Column(db.String(16), default="medium", nullable=False)
    last_updated = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    user = db.relationship("User", back_populates="progress")
    
    __table_args__ = (db.UniqueConstraint('user_id', 'subject', name='uix_user_subject'),)


class EmotionLog(db.Model):
    __tablename__ = "emotion_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    emotion = db.Column(db.String(32), nullable=False, index=True)
    confidence = db.Column(db.Float, nullable=False)
    context = db.Column(db.String(64), nullable=True)
    timestamp = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User", back_populates="emotion_logs")


class SubjectPerformance(db.Model):
    __tablename__ = "subject_performance"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject = db.Column(db.String(64), nullable=False, index=True)
    accuracy = db.Column(db.Float, default=0.0, nullable=False)
    streak = db.Column(db.Integer, default=0, nullable=False)
    best_streak = db.Column(db.Integer, default=0, nullable=False)
    total_time_spent = db.Column(db.Integer, default=0, nullable=False)
    last_practiced_at = db.Column(db.DateTime, nullable=True)
    
    user = db.relationship("User", back_populates="subject_performance")
    
    __table_args__ = (db.UniqueConstraint('user_id', 'subject', name='uix_user_subject_perf'),)


class AnswerLog(db.Model):
    __tablename__ = "answer_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    selected_index = db.Column(db.Integer, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)
    time_spent = db.Column(db.Integer, nullable=False)
    difficulty_at_time = db.Column(db.String(16), nullable=True)
    emotion_at_time = db.Column(db.String(32), nullable=True)
    answered_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    # Optional link to a TestResult for per-test detail aggregation
    test_id = db.Column(db.Integer, db.ForeignKey("test_results.id", ondelete="SET NULL"), nullable=True, index=True)

    user = db.relationship("User", back_populates="answers")
    question = db.relationship("Question", back_populates="answers")
    test = db.relationship("TestResult", back_populates="answers")


class Test(db.Model):
    __tablename__ = "tests"
    __test__ = False

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    subject = db.Column(db.String(64), nullable=False, index=True)
    grade = db.Column(db.String(32), nullable=False, index=True)
    topic = db.Column(db.String(128), nullable=True, index=True)
    difficulty = db.Column(db.String(16), nullable=False, index=True)  # easy, medium, hard
    time_limit = db.Column(db.Integer, default=30, nullable=False)  # minutes
    question_count = db.Column(db.Integer, nullable=False)
    total_points = db.Column(db.Integer, default=100, nullable=False)
    
    # Relationships
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Status and scheduling
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    
    # Relationships
    created_by_teacher = db.relationship("User", back_populates="created_tests")
    school = db.relationship("School")
    test_results = db.relationship("TestResult", back_populates="test", lazy="dynamic")
    test_questions = db.relationship("TestQuestion", back_populates="test", lazy="dynamic", cascade="all, delete-orphan")
    assignments = db.relationship("TestAssignment", back_populates="test", lazy="dynamic", cascade="all, delete-orphan")
    
    def as_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'subject': self.subject,
            'grade': self.grade,
            'topic': self.topic,
            'difficulty': self.difficulty,
            'time_limit': self.time_limit,
            'question_count': self.question_count,
            'total_points': self.total_points,
            'created_by': self.created_by,
            'school_id': self.school_id,
            'is_active': self.is_active,
            'is_published': self.is_published,
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class TestQuestion(db.Model):
    __tablename__ = "test_questions"
    __test__ = False
    
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    order = db.Column(db.Integer, nullable=False)
    points = db.Column(db.Integer, default=1, nullable=False)
    
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    
    # Relationships
    test = db.relationship("Test", back_populates="test_questions")
    question = db.relationship("Question")
    
    __table_args__ = (db.UniqueConstraint('test_id', 'question_id', name='uix_test_question'),)


class Classroom(db.Model):
    __tablename__ = "classrooms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    grade = db.Column(db.String(32), nullable=True, index=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    school = db.relationship("School", back_populates="classrooms")
    teacher = db.relationship("User", back_populates="created_classrooms", foreign_keys=[teacher_id])
    students = db.relationship("ClassroomStudent", back_populates="classroom", lazy="dynamic", cascade="all, delete-orphan")
    assignments = db.relationship("TestAssignment", back_populates="classroom", lazy="dynamic", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint('name', 'school_id', 'teacher_id', name='uix_classroom_name_school_teacher'),
    )

    def as_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'grade': self.grade,
            'school_id': self.school_id,
            'teacher_id': self.teacher_id,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ClassroomStudent(db.Model):
    __tablename__ = "classroom_students"

    id = db.Column(db.Integer, primary_key=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    enrolled_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    classroom = db.relationship("Classroom", back_populates="students")
    student = db.relationship("User", back_populates="classroom_memberships", foreign_keys=[student_id])

    __table_args__ = (
        db.UniqueConstraint('classroom_id', 'student_id', name='uix_classroom_student'),
    )

    def as_dict(self):
        return {
            'id': self.id,
            'classroom_id': self.classroom_id,
            'student_id': self.student_id,
            'is_active': self.is_active,
            'enrolled_at': self.enrolled_at.isoformat() if self.enrolled_at else None,
        }


class TestAssignment(db.Model):
    __tablename__ = "test_assignments"
    __test__ = False

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True)
    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id", ondelete="SET NULL"), nullable=True, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)
    status = db.Column(db.String(32), default='assigned', nullable=False, index=True)
    is_mandatory = db.Column(db.Boolean, default=True, nullable=False)
    allow_late = db.Column(db.Boolean, default=False, nullable=False)
    require_camera = db.Column(db.Boolean, default=True, nullable=False)
    require_emotion = db.Column(db.Boolean, default=True, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    published_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    test = db.relationship("Test", back_populates="assignments")
    classroom = db.relationship("Classroom", back_populates="assignments")
    student = db.relationship("User", back_populates="assigned_tests", foreign_keys=[student_id])
    teacher = db.relationship("User", back_populates="created_assignments", foreign_keys=[assigned_by])

    def as_dict(self):
        return {
            'id': self.id,
            'test_id': self.test_id,
            'classroom_id': self.classroom_id,
            'student_id': self.student_id,
            'assigned_by': self.assigned_by,
            'notes': self.notes,
            'due_at': self.due_at.isoformat() if self.due_at else None,
            'status': self.status,
            'is_mandatory': self.is_mandatory,
            'allow_late': self.allow_late,
            'require_camera': self.require_camera,
            'require_emotion': self.require_emotion,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class TestResult(db.Model):
    __tablename__ = "test_results"
    __test__ = False

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id", ondelete="SET NULL"), nullable=True, index=True)
    subject = db.Column(db.String(64), nullable=False, index=True)
    total_questions = db.Column(db.Integer, nullable=False)
    correct_answers = db.Column(db.Integer, nullable=False)
    total_points = db.Column(db.Integer, default=100, nullable=False)
    earned_points = db.Column(db.Integer, default=0, nullable=False)
    average_time_per_question = db.Column(db.Float, nullable=True)
    started_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    
    # Test status
    status = db.Column(db.String(32), default='in_progress', nullable=False)  # in_progress, completed, abandoned, expired

    user = db.relationship("User", back_populates="test_results")
    test = db.relationship("Test", back_populates="test_results")
    answers = db.relationship("AnswerLog", back_populates="test", lazy="dynamic")
    
    def as_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'test_id': self.test_id,
            'subject': self.subject,
            'total_questions': self.total_questions,
            'correct_answers': self.correct_answers,
            'total_points': self.total_points,
            'earned_points': self.earned_points,
            'average_time_per_question': self.average_time_per_question,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'status': self.status
        }


class TeacherRequest(db.Model):
    __tablename__ = "teacher_requests"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    grade = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(32), default='pending', nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def as_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'grade': self.grade,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class SyllabusTopic(db.Model):
    __tablename__ = 'syllabus_topics'

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(64), nullable=False, index=True)
    grade = db.Column(db.String(32), nullable=True, index=True)
    slug = db.Column(db.String(128), nullable=False, unique=True, index=True)
    title = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def as_dict(self):
        return {
            'id': self.id,
            'subject': self.subject,
            'grade': self.grade,
            'slug': self.slug,
            'title': self.title,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class TeacherIntervention(db.Model):
    __tablename__ = "teacher_interventions"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type = db.Column(db.String(64), nullable=False, default="note", index=True)
    title = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="planned", index=True)

    subject = db.Column(db.String(64), nullable=True, index=True)
    topic = db.Column(db.String(128), nullable=True, index=True)
    due_at = db.Column(db.DateTime, nullable=True, index=True)

    classroom_id = db.Column(db.Integer, db.ForeignKey("classrooms.id", ondelete="SET NULL"), nullable=True, index=True)
    related_test_id = db.Column(db.Integer, db.ForeignKey("tests.id", ondelete="SET NULL"), nullable=True, index=True)

    student_ids = db.Column(db.JSON, nullable=True)
    assignment_ids = db.Column(db.JSON, nullable=True)
    cluster_payload = db.Column(db.JSON, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    teacher = db.relationship("User", foreign_keys=[teacher_id])
    classroom = db.relationship("Classroom", foreign_keys=[classroom_id])
    related_test = db.relationship("Test", foreign_keys=[related_test_id])

    def as_dict(self):
        return {
            "id": self.id,
            "teacher_id": self.teacher_id,
            "action_type": self.action_type,
            "title": self.title,
            "notes": self.notes,
            "status": self.status,
            "subject": self.subject,
            "topic": self.topic,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "classroom_id": self.classroom_id,
            "related_test_id": self.related_test_id,
            "student_ids": self.student_ids or [],
            "assignment_ids": self.assignment_ids or [],
            "cluster_payload": self.cluster_payload or [],
            "metadata": self.metadata_json or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TeacherDocument(db.Model):
    __tablename__ = "teacher_documents"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True)

    title = db.Column(db.String(255), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_ext = db.Column(db.String(16), nullable=True, index=True)
    content_type = db.Column(db.String(128), nullable=True)
    file_size_bytes = db.Column(db.Integer, nullable=False)
    content_sha256 = db.Column(db.String(64), nullable=False, index=True)
    storage_path = db.Column(db.String(512), nullable=True)

    status = db.Column(db.String(32), nullable=False, default="processing", index=True)
    error_message = db.Column(db.Text, nullable=True)
    chunk_count = db.Column(db.Integer, nullable=False, default=0)
    token_count = db.Column(db.Integer, nullable=False, default=0)
    metadata_json = db.Column(db.JSON, nullable=True)

    uploaded_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    processed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    teacher = db.relationship("User", foreign_keys=[teacher_id])
    school = db.relationship("School", foreign_keys=[school_id])
    chunks = db.relationship(
        "TeacherDocumentChunk",
        back_populates="document",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def as_dict(self):
        return {
            "id": self.id,
            "teacher_id": self.teacher_id,
            "school_id": self.school_id,
            "title": self.title,
            "filename": self.filename,
            "file_ext": self.file_ext,
            "content_type": self.content_type,
            "file_size_bytes": self.file_size_bytes,
            "content_sha256": self.content_sha256,
            "status": self.status,
            "error_message": self.error_message,
            "chunk_count": self.chunk_count,
            "token_count": self.token_count,
            "metadata": self.metadata_json or {},
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TeacherDocumentChunk(db.Model):
    __tablename__ = "teacher_document_chunks"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("teacher_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    chunk_id = db.Column(db.String(64), nullable=False, index=True)
    chunk_index = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    text_hash = db.Column(db.String(64), nullable=False, index=True)
    char_start = db.Column(db.Integer, nullable=True)
    char_end = db.Column(db.Integer, nullable=True)
    token_count = db.Column(db.Integer, nullable=False, default=0)

    embedding_vector = db.Column(db.JSON, nullable=True)
    # Optional pgvector-backed storage (kept as text in ORM for sqlite test compatibility).
    embedding_vector_pg = db.Column(db.Text, nullable=True)
    embedding_dim = db.Column(db.Integer, nullable=True)
    embedding_provider = db.Column(db.String(64), nullable=True)
    embedding_model = db.Column(db.String(128), nullable=True)
    embedding_status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    vector_store = db.Column(db.String(32), nullable=False, default="python")
    metadata_json = db.Column(db.JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    document = db.relationship("TeacherDocument", back_populates="chunks")
    teacher = db.relationship("User", foreign_keys=[teacher_id])

    __table_args__ = (
        db.UniqueConstraint("document_id", "chunk_index", name="uix_teacher_document_chunk_order"),
        db.UniqueConstraint("document_id", "chunk_id", name="uix_teacher_document_chunk_id"),
    )

    def as_dict(self):
        return {
            "id": self.id,
            "document_id": self.document_id,
            "teacher_id": self.teacher_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "token_count": self.token_count,
            "embedding_dim": self.embedding_dim,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_status": self.embedding_status,
            "vector_store": self.vector_store,
            "metadata": self.metadata_json or {},
        }


class RagRetrievalEvent(db.Model):
    __tablename__ = "rag_retrieval_events"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    test_id = db.Column(db.Integer, db.ForeignKey("tests.id", ondelete="SET NULL"), nullable=True, index=True)
    document_id = db.Column(db.Integer, db.ForeignKey("teacher_documents.id", ondelete="SET NULL"), nullable=True, index=True)

    generation_mode_requested = db.Column(db.String(32), nullable=False, default="standard", index=True)
    generation_mode_effective = db.Column(db.String(32), nullable=False, default="standard", index=True)
    vector_store_requested = db.Column(db.String(32), nullable=True)
    vector_store_effective = db.Column(db.String(32), nullable=True, index=True)

    status = db.Column(db.String(32), nullable=False, default="success", index=True)
    fallback_reason = db.Column(db.String(128), nullable=True, index=True)
    error_message = db.Column(db.Text, nullable=True)

    selected_doc_count = db.Column(db.Integer, nullable=False, default=0)
    candidate_chunk_count = db.Column(db.Integer, nullable=False, default=0)
    retrieval_count = db.Column(db.Integer, nullable=False, default=0)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    avg_similarity = db.Column(db.Float, nullable=False, default=0.0)
    max_similarity = db.Column(db.Float, nullable=False, default=0.0)

    provenance_count = db.Column(db.Integer, nullable=False, default=0)
    coverage = db.Column(db.Float, nullable=False, default=0.0)
    relevance = db.Column(db.Float, nullable=False, default=0.0)
    duplication = db.Column(db.Float, nullable=False, default=0.0)

    requested_count = db.Column(db.Integer, nullable=False, default=0)
    generated_count = db.Column(db.Integer, nullable=False, default=0)
    service_latency_ms = db.Column(db.Float, nullable=True)

    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    teacher = db.relationship("User", foreign_keys=[teacher_id])
    test = db.relationship("Test", foreign_keys=[test_id])
    document = db.relationship("TeacherDocument", foreign_keys=[document_id])

    def as_dict(self):
        return {
            "id": self.id,
            "teacher_id": self.teacher_id,
            "test_id": self.test_id,
            "document_id": self.document_id,
            "generation_mode_requested": self.generation_mode_requested,
            "generation_mode_effective": self.generation_mode_effective,
            "vector_store_requested": self.vector_store_requested,
            "vector_store_effective": self.vector_store_effective,
            "status": self.status,
            "fallback_reason": self.fallback_reason,
            "error_message": self.error_message,
            "selected_doc_count": self.selected_doc_count,
            "candidate_chunk_count": self.candidate_chunk_count,
            "retrieval_count": self.retrieval_count,
            "confidence": self.confidence,
            "avg_similarity": self.avg_similarity,
            "max_similarity": self.max_similarity,
            "provenance_count": self.provenance_count,
            "coverage": self.coverage,
            "relevance": self.relevance,
            "duplication": self.duplication,
            "requested_count": self.requested_count,
            "generated_count": self.generated_count,
            "service_latency_ms": self.service_latency_ms,
            "metadata": self.metadata_json or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class UserSetting(db.Model):
    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    settings_json = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", back_populates="settings")

    def as_dict(self):
        return {
            "user_id": self.user_id,
            "settings": self.settings_json or {},
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
