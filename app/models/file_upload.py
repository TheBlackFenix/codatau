from app.extensions import db
from datetime import datetime, timezone


class FileUpload(db.Model):
    __tablename__ = 'file_uploads'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    filename      = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    file_type     = db.Column(db.String(10), nullable=False)   # csv, xlsx, xls
    file_size     = db.Column(db.Integer)                      # bytes
    row_count     = db.Column(db.Integer, default=0)
    column_count  = db.Column(db.Integer, default=0)
    status        = db.Column(db.String(20), default='pending')  # pending, processed, error
    uploaded_at   = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at  = db.Column(db.DateTime(timezone=True))

    # Relación con insights
    insights = db.relationship('AIInsight', backref='file', lazy=True, cascade='all, delete-orphan')
    versions = db.relationship(
        'DatasetVersion',
        backref='file',
        lazy=True,
        cascade='all, delete-orphan',
    )
    cleaning_decisions = db.relationship(
        'CleaningDecision',
        backref='file',
        lazy=True,
        cascade='all, delete-orphan',
    )

    @property
    def active_version(self):
        active = [version for version in self.versions if version.is_active]
        return max(active, key=lambda version: version.version_number, default=None)

    @property
    def active_stored_filename(self):
        version = self.active_version
        return version.stored_filename if version else self.filename

    def __repr__(self):
        return f'<FileUpload {self.original_name}>'
