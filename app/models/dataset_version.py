from datetime import datetime, timezone

from app.extensions import db


class DatasetVersion(db.Model):
    __tablename__ = 'dataset_versions'
    __table_args__ = (
        db.UniqueConstraint('file_id', 'version_number', name='uq_dataset_version'),
    )

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(
        db.Integer,
        db.ForeignKey('file_uploads.id'),
        nullable=False,
        index=True,
    )
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    quarantine_filename = db.Column(db.String(255))
    operations = db.Column(db.JSON, nullable=False, default=list)
    metrics = db.Column(db.JSON, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f'<DatasetVersion file={self.file_id} v={self.version_number}>'
