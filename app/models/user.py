from app.extensions import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at    = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    is_active     = db.Column(db.Boolean, default=True)

    # Relaciones
    uploads = db.relationship('FileUpload', backref='owner', lazy=True, cascade='all, delete-orphan')
    insights = db.relationship('AIInsight', backref='owner', lazy=True, cascade='all, delete-orphan')
    cleaning_decisions = db.relationship(
        'CleaningDecision',
        backref='decider',
        lazy=True,
        cascade='all, delete-orphan',
    )
    dashboard_configurations = db.relationship(
        'DashboardConfiguration',
        backref='owner',
        lazy=True,
        cascade='all, delete-orphan',
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
