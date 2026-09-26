from datetime import datetime, timezone

from app.extensions import db


class DashboardConfiguration(db.Model):
    __tablename__ = 'dashboard_configurations'
    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'file_id',
            name='uq_dashboard_configuration_user_file',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    file_id = db.Column(db.Integer, db.ForeignKey('file_uploads.id'), nullable=False)
    metrics = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

