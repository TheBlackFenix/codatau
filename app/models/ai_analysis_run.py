from datetime import datetime, timezone

from app.extensions import db


class AIAnalysisRun(db.Model):
    __tablename__ = 'ai_analysis_runs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    file_id = db.Column(db.Integer, db.ForeignKey('file_uploads.id'), nullable=False)
    purpose = db.Column(db.String(50), nullable=False, default='cleaning_analysis')
    provider = db.Column(db.String(50), nullable=False)
    model = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False, index=True)
    candidate_count = db.Column(db.Integer, nullable=False, default=0)
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    provider_request_id = db.Column(db.String(160))
    result = db.Column(db.JSON)
    error_code = db.Column(db.String(80))
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
