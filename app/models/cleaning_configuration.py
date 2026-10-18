from datetime import datetime, timezone
from app.extensions import db


class CleaningConfiguration(db.Model):
    __tablename__ = 'cleaning_configurations'
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('file_uploads.id'), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    source_version = db.Column(db.String(255), nullable=False)
    columns = db.Column(db.JSON, nullable=False, default=dict)
    duplicates = db.Column(db.JSON, nullable=False, default=dict)
    revision = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __mapper_args__ = {'version_id_col': revision}
