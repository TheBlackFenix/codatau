import os
import io

from flask import Blueprint, abort, current_app, render_template, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.models.file_upload import FileUpload
from app.services.dataset_pipeline import DatasetPipeline

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')


@reports_bp.route('/')
@login_required
def index():
    files = (
        FileUpload.query
        .filter_by(user_id=current_user.id)
        .order_by(FileUpload.uploaded_at.desc())
        .all()
    )
    return render_template('reports/index.html', files=files)


@reports_bp.route('/download/<int:file_id>')
@login_required
def download(file_id):
    record = FileUpload.query.filter_by(
        id=file_id, user_id=current_user.id
    ).first_or_404()

    filepath = os.path.join(
        current_app.config['UPLOAD_FOLDER'],
        record.filename
    )

    pipeline = DatasetPipeline(
        current_app.config['ANALYTICS_FOLDER'],
        current_app.config['PROFILE_SAMPLE_SIZE'],
    )
    try:
        df = pipeline.load_dataframe_or_source(
            record.active_stored_filename,
            filepath,
        )
    except (OSError, ValueError):
        abort(404)

    buffer = io.BytesIO()
    df.to_csv(buffer, index=False, encoding='utf-8-sig')
    buffer.seek(0)

    base_name = secure_filename(record.original_name.rsplit('.', 1)[0]) or 'archivo'
    download_name = f"procesado_{base_name}.csv"

    return send_file(
        buffer,
        mimetype='text/csv',
        as_attachment=True,
        download_name=download_name
    )
