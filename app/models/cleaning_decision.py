from datetime import datetime, timezone

from app.extensions import db


class CleaningDecision(db.Model):
    __tablename__ = 'cleaning_decisions'
    __table_args__ = (
        db.UniqueConstraint(
            'file_id',
            'operation_id',
            name='uq_cleaning_decision_operation',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(
        db.Integer,
        db.ForeignKey('file_uploads.id'),
        nullable=False,
        index=True,
    )
    decided_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    operation_id = db.Column(db.String(500), nullable=False)
    operation = db.Column(db.String(80), nullable=False)
    column_name = db.Column(db.String(500))
    choice = db.Column(db.String(20), nullable=False)  # apply, keep
    parameters = db.Column(db.JSON, nullable=False, default=dict)
    affected_rows = db.Column(db.Integer, nullable=False, default=0)
    reason = db.Column(db.Text)
    applied_version_number = db.Column(db.Integer)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
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

    def __repr__(self):
        return f'<CleaningDecision file={self.file_id} operation={self.operation_id}>'
