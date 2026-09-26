import os

from flask import Blueprint, current_app, jsonify, render_template, request, session
from flask_login import login_required, current_user

from app.extensions import db
from app.models.file_upload import FileUpload
from app.models.ai_insight import AIInsight
from app.services.data_service import DataService
from app.services.dataset_pipeline import DatasetPipeline
from app.services.ai_service import AIService
from app.services.dashboard_service import (
    DashboardConfigurationError,
    DashboardService,
)

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
@login_required
def index():
    all_files = (
        FileUpload.query
        .filter_by(user_id=current_user.id)
        .order_by(FileUpload.uploaded_at.desc())
        .all()
    )

    active_file_id = session.get('active_file_id')
    active_file = None
    summary = None
    chart_data = {}
    insights = []

    if active_file_id:
        active_file = FileUpload.query.filter_by(
            id=active_file_id, user_id=current_user.id
        ).first()

    if not active_file and all_files:
        active_file = all_files[0]
        session['active_file_id'] = active_file.id

    if active_file:
        filepath = os.path.join(
            current_app.config['UPLOAD_FOLDER'],
            active_file.filename
        )
        try:
            pipeline = DatasetPipeline(
                current_app.config['ANALYTICS_FOLDER'],
                current_app.config['PROFILE_SAMPLE_SIZE'],
            )
            df = pipeline.load_dataframe_or_source(
                active_file.active_stored_filename,
                filepath,
            )
            summary = DataService.get_summary(df)
            insights = AIService.generate_display_insights(df, summary)
            metric_layout = DashboardService.layout_for(active_file, summary)
            metric_cards = DashboardService.cards_for(metric_layout, summary)
            chart_data = DashboardService.build_charts(df, summary, metric_layout)

        except Exception:
            current_app.logger.exception(
                'No se pudo construir el dashboard para el archivo %s',
                active_file.id,
            )
            summary = None
            chart_data = {}

    if not active_file or not summary:
        metric_layout = []
        metric_cards = []

    total_files = len(all_files)
    total_rows = sum(f.row_count or 0 for f in all_files)
    total_insights = AIInsight.query.filter_by(user_id=current_user.id).count()

    return render_template('dashboard/index.html',
        all_files=all_files,
        active_file=active_file,
        summary=summary,
        chart_data=chart_data,
        metric_layout=metric_layout,
        metric_cards=metric_cards,
        insights=insights,
        total_files=total_files,
        total_rows=total_rows,
        total_insights=total_insights,
    )


@dashboard_bp.route('/dashboard/files/<int:file_id>/metrics', methods=['POST'])
@login_required
def save_metrics(file_id):
    record = FileUpload.query.filter_by(
        id=file_id,
        user_id=current_user.id,
    ).first_or_404()
    payload = request.get_json(silent=True) or {}
    try:
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], record.filename)
        pipeline = DatasetPipeline(
            current_app.config['ANALYTICS_FOLDER'],
            current_app.config['PROFILE_SAMPLE_SIZE'],
        )
        dataframe = pipeline.load_dataframe_or_source(
            record.active_stored_filename,
            filepath,
        )
        summary = DataService.get_summary(dataframe)
        layout = DashboardService.save_layout(
            record,
            current_user.id,
            payload.get('metrics'),
            summary,
        )
        db.session.commit()
    except DashboardConfigurationError as error:
        db.session.rollback()
        return jsonify({'error': str(error)}), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            'No se pudo guardar el dashboard del archivo %s',
            record.id,
        )
        return jsonify({'error': 'No pudimos guardar el dashboard.'}), 500

    return jsonify({
        'metrics': layout,
        'message': 'Dashboard guardado.',
    })
