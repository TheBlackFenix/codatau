from app.extensions import db
from datetime import datetime, timezone


class AIInsight(db.Model):
    __tablename__ = 'ai_insights'

    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    file_id      = db.Column(db.Integer, db.ForeignKey('file_uploads.id'), nullable=False)
    insight_type = db.Column(db.String(50), nullable=False)  # warning, info, success, danger
    message      = db.Column(db.Text, nullable=False)
    created_at   = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self):
        return f'<AIInsight {self.insight_type}: {self.message[:40]}>'
