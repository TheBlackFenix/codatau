import os

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import login_required, current_user

from app.extensions import db
from app.models.file_upload import FileUpload
from app.models.ai_insight import AIInsight
from app.services.data_service import DataService
from app.services.dataset_pipeline import DatasetPipeline
from app.services.ai_service import AIService
from app.services.dataset_context_service import DatasetContextService
from app.services.cleaning_decision_service import CleaningDecisionService
from app.services.quality_insight_service import QualityInsightService
from app.services.semantic_constraint_service import SemanticConstraintService
from app.services.ai_dashboard_service import AIDashboardService
from app.services.ai_providers import AIProviderError, AIProviderFactory
from app.forms.file_forms import CleaningActionForm
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
    recommendation = None

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
            profile = pipeline.ensure_current_profile(
                active_file.active_stored_filename,
                filepath,
            )
            context_outcome = DatasetContextService.latest(
                active_file,
                profile,
                current_user.id,
                current_app.config,
            )
            if context_outcome:
                source_parquet, _ = pipeline.paths_for(
                    active_file.active_stored_filename
                )
                profile = SemanticConstraintService.enrich_plan(
                    profile,
                    context_outcome.context,
                    source_parquet,
                )
            insights = QualityInsightService.decorate(
                AIService.generate_display_insights(df, summary),
                profile,
                CleaningDecisionService.active_for_file(active_file.id),
            )
            metric_layout = DashboardService.layout_for(active_file, summary)
            metric_cards = DashboardService.cards_for(metric_layout, summary)
            chart_data = DashboardService.build_charts(df, summary, metric_layout, context_outcome.context if context_outcome else {})
            recommendation = AIDashboardService.latest(
                active_file, summary, context_outcome.context if context_outcome else {},
                current_app.config,
            )

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
        recommendation=recommendation,
        action_form=CleaningActionForm(),
        ai_configured=AIProviderFactory.is_configured(current_app.config),
        aggregation_labels=DashboardService.AGGREGATION_LABELS,
    )


def _recommendation_inputs(record):
    pipeline = DatasetPipeline(current_app.config['ANALYTICS_FOLDER'], current_app.config['PROFILE_SAMPLE_SIZE'])
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], record.filename)
    summary = DataService.get_summary(pipeline.load_dataframe_or_source(record.active_stored_filename, filepath))
    profile = pipeline.ensure_current_profile(record.active_stored_filename, filepath)
    context = DatasetContextService.latest(record, profile, current_user.id, current_app.config)
    return summary, profile, context


@dashboard_bp.route('/dashboard/files/<int:file_id>/recommendations', methods=['POST'])
@login_required
def recommend_metrics(file_id):
    record = FileUpload.query.filter_by(id=file_id, user_id=current_user.id).first_or_404()
    if not CleaningActionForm().validate_on_submit():
        return 'Formulario inválido.', 400
    try:
        summary, profile, context = _recommendation_inputs(record)
        if context is None:
            context = DatasetContextService.analyze(record, profile, current_user.id, current_app.config)
        AIDashboardService.recommend(record, summary, context.context, current_app.config)
        db.session.commit()
        flash('Propuesta lista. Revísala y acepta las métricas para crear tu dashboard.', 'success')
    except AIProviderError as error:
        db.session.commit()  # Preserve the safe audit of the failed provider call.
        flash(error.user_message + ' Puedes configurar tus métricas manualmente.', 'warning')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('No se pudo proponer el dashboard del archivo %s', record.id)
        flash('No pudimos preparar las métricas. Los datos siguen intactos.', 'warning')
    session['active_file_id'] = record.id
    return redirect(url_for('dashboard.index'))


@dashboard_bp.route('/dashboard/files/<int:file_id>/recommendations/apply', methods=['POST'])
@login_required
def apply_recommended_metrics(file_id):
    record = FileUpload.query.filter_by(id=file_id, user_id=current_user.id).first_or_404()
    if not CleaningActionForm().validate_on_submit():
        return 'Formulario inválido.', 400
    summary, _, context = _recommendation_inputs(record)
    run = AIDashboardService.latest(record, summary, context.context if context else {}, current_app.config)
    if run is None or str(run.id) != request.form.get('run_id'):
        flash('La propuesta ya no corresponde a la versión activa. Genera una nueva.', 'warning')
    else:
        DashboardService.save_layout(record, current_user.id, run.result['metrics'], summary)
        db.session.commit()
        flash('Dashboard creado. Puedes quitar, agregar y ordenar sus métricas.', 'success')
    session['active_file_id'] = record.id
    return redirect(url_for('dashboard.index'))


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
