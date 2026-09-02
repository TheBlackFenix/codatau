import io
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.extensions import db
from app.models.file_upload import FileUpload
from app.models.ai_insight import AIInsight
from app.models.dataset_version import DatasetVersion
from app.models.cleaning_decision import CleaningDecision
from app.forms.file_forms import CleaningActionForm, UploadForm
from app.services.data_service import DataService, FileReadError
from app.services.validation_service import ValidationService
from app.services.ai_service import AIService
from app.services.dataset_pipeline import DatasetPipeline
from app.services.cleaning_executor import (
    CleaningExecutor,
    CleaningPlanError,
    select_configured_operations,
)
from app.services.cleaning_decision_service import CleaningDecisionService
from app.services.dashboard_service import DashboardService
from app.services.storage_service import LocalStorageService

files_bp = Blueprint('files', __name__, url_prefix='/files')

CLEANING_PARAMETER_FIELDS = {
    'cast_type': ('decimal_separator',),
    'parse_date': ('date_format',),
    'normalize_case': ('case_style',),
    'normalize_phone': ('phone_style',),
}

CLEANING_OPERATION_LABELS = {
    'blank_to_null': 'Convertir textos vacíos en valores nulos',
    'cast_type': 'Convertir a número',
    'handle_missing': 'Enviar filas incompletas a cuarentena',
    'normalize_boolean': 'Normalizar valores Sí/No',
    'normalize_case': 'Unificar mayúsculas y minúsculas',
    'normalize_phone': 'Normalizar teléfonos',
    'parse_date': 'Convertir a fecha',
    'remove_exact_duplicates': 'Eliminar filas duplicadas exactas',
    'review_invalid_values': 'Revisar valores inválidos con IA',
    'trim_text': 'Eliminar espacios externos',
    'validate_email': 'Validar correos electrónicos',
}


def _pipeline():
    return DatasetPipeline(
        current_app.config['ANALYTICS_FOLDER'],
        current_app.config['PROFILE_SAMPLE_SIZE'],
    )


def _storage():
    return LocalStorageService(current_app.config['UPLOAD_FOLDER'])


def _load_dataframe(record):
    source_path = _storage().path_for(record.filename)
    return _pipeline().load_dataframe_or_source(
        record.active_stored_filename,
        source_path,
    )


def _record_for_user(file_id):
    return FileUpload.query.filter_by(
        id=file_id,
        user_id=current_user.id,
    ).first_or_404()


def _cleaning_context(record):
    pipeline = _pipeline()
    stored_filename = record.active_stored_filename
    source_parquet, _ = pipeline.paths_for(stored_filename)
    if not source_parquet.exists():
        abort(404)
    source_path = _storage().path_for(record.filename)
    try:
        profile_data = pipeline.ensure_current_profile(stored_filename, source_path)
    except OSError:
        abort(404)
    return pipeline, stored_filename, source_parquet, profile_data


def _discard_failed_upload(storage, pipeline, stored_filename):
    db.session.rollback()
    try:
        storage.delete(stored_filename)
        pipeline.remove_artifacts(stored_filename)
    except OSError:
        current_app.logger.exception(
            'No se pudieron descartar todos los artefactos fallidos de %s',
            stored_filename,
        )


def _cleaning_parameter_overrides(form_data, operations, selected_ids):
    selected_ids = set(selected_ids)
    overrides = {}
    for operation in operations:
        operation_id = operation['id']
        if operation_id not in selected_ids:
            continue
        fields = CLEANING_PARAMETER_FIELDS.get(operation['operation'], ())
        values = {
            field: form_data.get(f'parameter:{operation_id}:{field}')
            for field in fields
        }
        overrides[operation_id] = values
    return overrides


def _submitted_cleaning_decisions(profile_data, resolved_ids=None):
    operations = profile_data['cleaning_plan']['operations']
    choices = {}
    has_explicit_choices = False
    for operation in operations:
        operation_id = operation['id']
        choice = request.form.get(f'decision:{operation_id}')
        if choice is None:
            continue
        has_explicit_choices = True
        if choice not in {'apply', 'keep'}:
            raise CleaningPlanError('Una decisión de limpieza no es válida.')
        choices[operation_id] = choice

    # Compatibility with previews submitted before the explicit Yes/No UI.
    if not has_explicit_choices:
        choices = {
            operation_id: 'apply'
            for operation_id in request.form.getlist('operation_ids')
        }

    if not choices:
        raise CleaningPlanError(
            'Decide Sí o No en al menos una sugerencia para continuar.'
        )

    resolved_ids = set(resolved_ids or ())
    if set(choices) & resolved_ids:
        raise CleaningPlanError(
            'Una de las sugerencias ya fue resuelta. Actualiza la página e inténtalo de nuevo.'
        )

    proposed = {operation['id']: operation for operation in operations}
    unknown = set(choices) - set(proposed)
    if unknown:
        raise CleaningPlanError(
            'El plan contiene decisiones desconocidas o desactualizadas.'
        )

    selected_ids = [
        operation_id
        for operation_id, choice in choices.items()
        if choice == 'apply'
    ]
    overrides = _cleaning_parameter_overrides(
        request.form,
        operations,
        selected_ids,
    )
    selected_operations = (
        select_configured_operations(
            profile_data['cleaning_plan'],
            selected_ids,
            overrides,
        )
        if selected_ids
        else []
    )
    configured = {
        operation['id']: operation
        for operation in selected_operations
    }
    decisions = []
    for operation_id, choice in choices.items():
        operation = configured.get(operation_id, proposed[operation_id])
        decisions.append({
            'operation_id': operation_id,
            'operation': operation['operation'],
            'column': operation.get('column'),
            'choice': choice,
            'parameters': operation.get('parameters') or {},
            'affected_rows': operation.get('affected_rows') or 0,
            'reason': operation.get('reason'),
        })
    return selected_operations, decisions


@files_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    form = UploadForm()

    if form.validate_on_submit():
        file = form.file.data
        original_name = Path(file.filename).name
        ext = os.path.splitext(original_name)[1].lower().lstrip('.')

        # Nombre único para evitar colisiones
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        storage = _storage()
        pipeline = _pipeline()
        processing_stage = 'almacenamiento inicial'

        try:
            filepath = storage.save(file, unique_name)

            # Leer archivo
            processing_stage = 'lectura del archivo'
            df = DataService.read_file(filepath)

            # Validar
            processing_stage = 'validación de estructura'
            errors, warnings = ValidationService.validate_file(df)

            if errors:
                storage.delete(unique_name)
                for e in errors:
                    flash(e, 'danger')
                return redirect(url_for('files.upload'))

            # Limpiar
            processing_stage = 'normalización de datos'
            df = DataService.clean_dataframe(df)

            # Crear artefacto analítico portable y perfil compacto para IA.
            processing_stage = 'creación del perfil analítico'
            artifact = pipeline.ingest_dataframe(df, unique_name, filepath)

            # Resumen
            summary = DataService.get_summary(df)

            # Guardar registro en BD
            processing_stage = 'registro del resultado'
            upload_record = FileUpload(
                user_id=current_user.id,
                filename=unique_name,
                original_name=original_name,
                file_type=ext,
                file_size=os.path.getsize(filepath),
                row_count=artifact.profile['row_count'],
                column_count=artifact.profile['column_count'],
                status='processed',
                processed_at=datetime.now(timezone.utc)
            )
            db.session.add(upload_record)
            db.session.flush()  # Para obtener el ID antes del commit

            # Generar insights IA
            insights = AIService.generate_insights(df, summary)
            for ins in insights:
                insight_record = AIInsight(
                    user_id=current_user.id,
                    file_id=upload_record.id,
                    insight_type=ins['type'],
                    message=ins['message']
                )
                db.session.add(insight_record)

            db.session.commit()
            session['active_file_id'] = upload_record.id

            # Flash de warnings de validación
            for w in warnings:
                flash(w, 'warning')

            flash(f'Archivo procesado correctamente: {summary["rows"]} filas, {summary["columns"]} columnas.', 'success')
            return redirect(url_for('files.results', file_id=upload_record.id))

        except FileReadError as error:
            _discard_failed_upload(storage, pipeline, unique_name)
            current_app.logger.warning(
                'Archivo rechazado %s (%s): %s',
                original_name,
                error.code,
                error,
            )
            flash(f'No pudimos leer “{original_name}”: {error.user_message}', 'danger')
            flash(f'Qué puedes revisar: {error.hint}', 'info')
            return redirect(url_for('files.upload'))
        except Exception as e:
            _discard_failed_upload(storage, pipeline, unique_name)
            error_reference = uuid.uuid4().hex[:8].upper()
            current_app.logger.exception(
                '[%s] No se pudo procesar %s durante %s: %s',
                error_reference,
                original_name,
                processing_stage,
                e,
            )
            flash(
                f'El archivo se recibió, pero falló durante {processing_stage}. '
                f'No se guardaron cambios. Referencia: {error_reference}.',
                'danger',
            )
            return redirect(url_for('files.upload'))

    if request.method == 'POST':
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, 'danger')

    return render_template('files/upload.html', form=form)


@files_bp.route('/results/<int:file_id>')
@login_required
def results(file_id):
    upload_record = FileUpload.query.filter_by(
        id=file_id, user_id=current_user.id
    ).first_or_404()

    try:
        dataframe = _load_dataframe(upload_record)
        summary = DataService.get_summary(dataframe)
        insights = AIService.generate_display_insights(dataframe, summary)
    except Exception as error:
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudieron abrir los resultados del archivo %s: %s',
            reference,
            upload_record.id,
            error,
        )
        flash(
            'El registro del archivo existe, pero sus datos procesados no están '
            f'disponibles. Referencia: {reference}.',
            'danger',
        )
        return redirect(url_for('files.upload'))

    metric_layout = DashboardService.layout_for(upload_record, summary)
    return render_template('files/results.html',
        record=upload_record,
        insights=insights,
        summary=summary,
        metric_layout=metric_layout,
        metric_cards=DashboardService.cards_for(metric_layout, summary),
    )


@files_bp.route('/select/<int:file_id>')
@login_required
def select(file_id):
    record = FileUpload.query.filter_by(
        id=file_id, user_id=current_user.id
    ).first_or_404()
    session['active_file_id'] = file_id
    flash(f'Archivo activo: {record.original_name}', 'info')
    return redirect(url_for('dashboard.index'))


@files_bp.route('/profile/<int:file_id>')
@login_required
def profile(file_id):
    record = _record_for_user(file_id)
    try:
        data_profile = _pipeline().ensure_current_profile(
            record.active_stored_filename,
            _storage().path_for(record.filename),
        )
    except Exception as error:
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudo cargar el perfil del archivo %s: %s',
            reference,
            record.id,
            error,
        )
        return jsonify({
            'error': 'El perfil analítico no está disponible.',
            'reference': reference,
        }), 404
    return jsonify(data_profile)


@files_bp.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete(file_id):
    record = FileUpload.query.filter_by(
        id=file_id, user_id=current_user.id
    ).first_or_404()

    storage = _storage()
    pipeline = _pipeline()
    version_filenames = [version.stored_filename for version in record.versions]

    # Limpiar sesión si era el archivo activo
    if session.get('active_file_id') == file_id:
        session.pop('active_file_id', None)

    db.session.delete(record)
    db.session.commit()

    # El registro es la fuente de verdad; un fallo físico no debe restaurarlo.
    try:
        storage.delete(record.filename)
        pipeline.remove_artifacts(record.filename)
        for stored_filename in version_filenames:
            pipeline.remove_artifacts(stored_filename)
    except OSError:
        current_app.logger.exception(
            'No se pudieron eliminar todos los artefactos del archivo %s',
            record.filename,
        )

    flash(f'Archivo "{record.original_name}" eliminado correctamente.', 'success')
    return redirect(url_for('files.upload'))


@files_bp.route('/cleaning/<int:file_id>')
@login_required
def cleaning(file_id):
    record = _record_for_user(file_id)
    try:
        _, _, _, profile_data = _cleaning_context(record)
    except (KeyError, TypeError, ValueError):
        current_app.logger.exception(
            'El perfil de limpieza del archivo %s no es válido',
            record.id,
        )
        flash(
            'No pudimos construir el plan de limpieza. Vuelve a cargar el archivo '
            'para regenerar su perfil.',
            'danger',
        )
        return redirect(url_for('files.results', file_id=record.id))
    if CleaningDecisionService.backfill_applied(record):
        db.session.commit()
    resolved_decisions = CleaningDecisionService.active_for_file(record.id)
    resolved_ids = {decision.operation_id for decision in resolved_decisions}
    current_operation_ids = {
        operation['id']
        for operation in profile_data['cleaning_plan']['operations']
    }
    operations = [
        operation
        for operation in profile_data['cleaning_plan']['operations']
        if operation['id'] not in resolved_ids
    ]
    executable_ids = {
        operation['id']
        for operation in operations
        if CleaningExecutor.is_executable(operation)
    }
    versions = DatasetVersion.query.filter_by(file_id=record.id).order_by(
        DatasetVersion.version_number.desc()
    ).all()
    return render_template(
        'files/cleaning.html',
        record=record,
        profile=profile_data,
        operations=operations,
        executable_ids=executable_ids,
        automatic_ids={
            operation['id']
            for operation in operations
            if operation['decision'] == 'automatic'
            and operation['id'] in executable_ids
        },
        operation_labels=CLEANING_OPERATION_LABELS,
        versions=versions,
        resolved_decisions=resolved_decisions,
        current_operation_ids=current_operation_ids,
        form=CleaningActionForm(),
    )


@files_bp.route('/cleaning')
@login_required
def cleaning_current():
    active_file_id = session.get('active_file_id')
    record = None
    if active_file_id:
        record = FileUpload.query.filter_by(
            id=active_file_id,
            user_id=current_user.id,
        ).first()
    if record is None:
        record = FileUpload.query.filter_by(user_id=current_user.id).order_by(
            FileUpload.uploaded_at.desc()
        ).first()
    if record is None:
        flash('Carga un archivo antes de iniciar una limpieza.', 'info')
        return redirect(url_for('files.upload'))
    session['active_file_id'] = record.id
    return redirect(url_for('files.cleaning', file_id=record.id))


@files_bp.route('/cleaning/<int:file_id>/preview', methods=['POST'])
@login_required
def cleaning_preview(file_id):
    form = CleaningActionForm()
    if not form.validate_on_submit():
        abort(400)
    record = _record_for_user(file_id)
    _, _, source_parquet, profile_data = _cleaning_context(record)
    try:
        if CleaningDecisionService.backfill_applied(record):
            db.session.commit()
        resolved_ids = {
            decision.operation_id
            for decision in CleaningDecisionService.active_for_file(record.id)
        }
        operations, decisions = _submitted_cleaning_decisions(
            profile_data,
            resolved_ids,
        )
        preview = CleaningExecutor().preview(source_parquet, operations)
    except CleaningPlanError as error:
        flash(str(error), 'warning')
        return redirect(url_for('files.cleaning', file_id=record.id))
    except Exception as error:
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudo generar la vista previa del archivo %s: %s',
            reference,
            record.id,
            error,
        )
        flash(
            'No pudimos generar la vista previa con esa combinación de reglas. '
            f'El archivo no fue modificado. Referencia: {reference}.',
            'danger',
        )
        return redirect(url_for('files.cleaning', file_id=record.id))

    return render_template(
        'files/cleaning_preview.html',
        record=record,
        operations=operations,
        decisions=decisions,
        preview=preview,
        operation_labels=CLEANING_OPERATION_LABELS,
        form=CleaningActionForm(),
    )


@files_bp.route('/cleaning/<int:file_id>/apply', methods=['POST'])
@login_required
def cleaning_apply(file_id):
    form = CleaningActionForm()
    if not form.validate_on_submit():
        abort(400)
    record = _record_for_user(file_id)
    pipeline, _, source_parquet, profile_data = _cleaning_context(record)
    try:
        if CleaningDecisionService.backfill_applied(record):
            db.session.commit()
        resolved_ids = {
            decision.operation_id
            for decision in CleaningDecisionService.active_for_file(record.id)
        }
        operations, decisions = _submitted_cleaning_decisions(
            profile_data,
            resolved_ids,
        )
    except CleaningPlanError as error:
        flash(str(error), 'warning')
        return redirect(url_for('files.cleaning', file_id=record.id))

    if not operations:
        try:
            CleaningDecisionService.save(record, current_user.id, decisions)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                'No se pudieron guardar las decisiones del archivo %s',
                record.id,
            )
            flash('No pudimos guardar las decisiones. Inténtalo de nuevo.', 'danger')
            return redirect(url_for('files.cleaning', file_id=record.id))
        flash(
            f'{len(decisions)} decisión(es) guardada(s). Los datos se conservaron sin cambios.',
            'success',
        )
        return redirect(url_for('files.cleaning', file_id=record.id))

    latest_version = DatasetVersion.query.filter_by(file_id=record.id).order_by(
        DatasetVersion.version_number.desc()
    ).first()
    version_number = (latest_version.version_number if latest_version else 0) + 1
    original_path = Path(record.filename)
    version_filename = (
        f'{original_path.stem}.v{version_number}{original_path.suffix}'
    )
    output_parquet, _ = pipeline.paths_for(version_filename)
    quarantine_parquet = pipeline.quarantine_path_for(version_filename)

    try:
        metrics = CleaningExecutor().apply(
            source_parquet,
            operations,
            output_parquet,
            quarantine_parquet,
        )
        source_path = _storage().path_for(record.filename)
        artifact = pipeline.profile_existing(version_filename, source_path)

        for version in record.versions:
            version.is_active = False
        version = DatasetVersion(
            file_id=record.id,
            created_by=current_user.id,
            version_number=version_number,
            stored_filename=version_filename,
            quarantine_filename=(
                quarantine_parquet.name if metrics['quarantined_rows'] else None
            ),
            operations=operations,
            metrics=metrics,
            is_active=True,
        )
        db.session.add(version)
        CleaningDecisionService.save(
            record,
            current_user.id,
            decisions,
            applied_version_number=version_number,
        )
        record.row_count = artifact.profile['row_count']
        record.column_count = artifact.profile['column_count']
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        pipeline.remove_artifacts(version_filename)
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudo aplicar la limpieza del archivo %s: %s',
            reference,
            record.id,
            error,
        )
        flash(
            f'No fue posible crear la nueva versión. Referencia: {reference}.',
            'danger',
        )
        return redirect(url_for('files.cleaning', file_id=record.id))

    flash(
        f'Versión {version_number} creada: {metrics["after_rows"]} filas válidas '
        f'y {metrics["quarantined_rows"]} en cuarentena. '
        f'{len(decisions)} decisión(es) resuelta(s).',
        'success',
    )
    return redirect(url_for('files.cleaning', file_id=record.id))


@files_bp.route(
    '/cleaning/<int:file_id>/decisions/<int:decision_id>/reopen',
    methods=['POST'],
)
@login_required
def cleaning_decision_reopen(file_id, decision_id):
    form = CleaningActionForm()
    if not form.validate_on_submit():
        abort(400)
    record = _record_for_user(file_id)
    decision = CleaningDecision.query.filter_by(
        id=decision_id,
        file_id=record.id,
        is_active=True,
    ).first_or_404()
    CleaningDecisionService.reopen(decision)
    db.session.commit()
    flash('La decisión volvió a quedar pendiente para revisión.', 'info')
    return redirect(url_for('files.cleaning', file_id=record.id))


@files_bp.route(
    '/cleaning/<int:file_id>/activate/<int:version_id>',
    methods=['POST'],
)
@login_required
def cleaning_activate(file_id, version_id):
    form = CleaningActionForm()
    if not form.validate_on_submit():
        abort(400)
    record = _record_for_user(file_id)
    target = None
    if version_id:
        target = DatasetVersion.query.filter_by(
            id=version_id,
            file_id=record.id,
        ).first_or_404()

    for version in record.versions:
        version.is_active = version is target
    try:
        if target:
            profile_data = _pipeline().load_profile(target.stored_filename)
            record.row_count = profile_data['row_count']
            record.column_count = profile_data['column_count']
            label = f'versión {target.version_number}'
        else:
            profile_data = _pipeline().load_profile(record.filename)
            record.row_count = profile_data['row_count']
            record.column_count = profile_data['column_count']
            label = 'versión original procesada'
    except Exception as error:
        db.session.rollback()
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudo activar una versión del archivo %s: %s',
            reference,
            record.id,
            error,
        )
        flash(
            'La versión seleccionada no está disponible; se conservó la versión '
            f'actual. Referencia: {reference}.',
            'danger',
        )
        return redirect(url_for('files.cleaning', file_id=record.id))
    db.session.commit()
    flash(f'Ahora estás usando la {label}.', 'success')
    return redirect(url_for('files.cleaning', file_id=record.id))


@files_bp.route('/cleaning/<int:file_id>/quarantine/<int:version_id>')
@login_required
def cleaning_quarantine(file_id, version_id):
    record = _record_for_user(file_id)
    version = DatasetVersion.query.filter_by(
        id=version_id,
        file_id=record.id,
    ).first_or_404()
    try:
        dataframe = _pipeline().load_quarantine_dataframe(version.stored_filename)
    except Exception as error:
        current_app.logger.exception(
            'No se pudo leer la cuarentena de la versión %s: %s',
            version.id,
            error,
        )
        abort(404)
    if dataframe is None:
        abort(404)

    buffer = io.BytesIO()
    dataframe.to_csv(buffer, index=False, encoding='utf-8-sig')
    buffer.seek(0)
    base_name = secure_filename(Path(record.original_name).stem) or 'archivo'
    return send_file(
        buffer,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'cuarentena_v{version.version_number}_{base_name}.csv',
    )


@files_bp.route('/insights')
@login_required
def insights_ia():
    active_file_id = session.get('active_file_id')
    active_file = None
    analysis = None

    all_files = FileUpload.query.filter_by(
        user_id=current_user.id
    ).order_by(FileUpload.uploaded_at.desc()).all()

    if active_file_id:
        active_file = FileUpload.query.filter_by(
            id=active_file_id, user_id=current_user.id
        ).first()

    if not active_file and all_files:
        active_file = all_files[0]

    if active_file:
        try:
            df = _load_dataframe(active_file)
            summary = DataService.get_summary(df)

            # Análisis descriptivo automático
            col_names = summary['column_names']
            numeric_cols = summary['recommended_metrics']
            physical_numeric_cols = summary['numeric_cols']
            text_cols = [c for c in col_names if c not in physical_numeric_cols]

            analysis = {
                'summary': summary,
                'numeric_cols': numeric_cols,
                'identifier_cols': summary['identifier_cols'],
                'optional_numeric_cols': summary['optional_numeric_cols'],
                'text_cols': text_cols,
                'insights': AIService.generate_display_insights(df, summary)
            }
            metric_layout = DashboardService.layout_for(active_file, summary)
            metric_cards = DashboardService.cards_for(metric_layout, summary)
        except Exception:
            current_app.logger.exception(
                'No se pudo cargar el análisis del archivo %s', active_file.id
            )
            flash(
                'No pudimos abrir los datos del archivo activo. Puedes seleccionar '
                'otro archivo o volver a cargarlo.',
                'warning',
            )

    return render_template('files/insights.html',
        active_file=active_file,
        all_files=all_files,
        analysis=analysis,
        metric_layout=metric_layout if analysis else [],
        metric_cards=metric_cards if analysis else [],
    )
