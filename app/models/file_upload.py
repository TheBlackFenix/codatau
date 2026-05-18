from app.extensions import db
from datetime import datetime


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
    uploaded_at   = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at  = db.Column(db.DateTime)

    # Relación con insights
    insights = db.relationship('AIInsight', backref='file', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<FileUpload {self.original_name}>'