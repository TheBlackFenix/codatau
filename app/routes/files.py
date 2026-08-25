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
from app.forms.file_forms import CleaningActionForm, UploadForm
from app.services.data_service import DataService, FileReadError
from app.services.validation_service import ValidationService
from app.services.ai_service import AIService
from app.services.dataset_pipeline import DatasetPipeline
from app.services.cleaning_executor import (
    CleaningExecutor,
    CleaningPlanError,
    select_executable_operations,
)
from app.services.storage_service import LocalStorageService

files_bp = Blueprint('files', __name__, url_prefix='/files')


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

    insights = AIInsight.query.filter_by(file_id=file_id).all()

    try:
        summary = DataService.get_summary(_load_dataframe(upload_record))
    except (OSError, ValueError):
        abort(404)

    return render_template('files/results.html',
        record=upload_record,
        insights=insights,
        summary=summary
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
    except OSError:
        return jsonify({'error': 'El perfil analítico no está disponible.'}), 404
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
    _, _, _, profile_data = _cleaning_context(record)
    operations = profile_data['cleaning_plan']['operations']
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
        versions=versions,
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
        operations = select_executable_operations(
            profile_data['cleaning_plan'],
            request.form.getlist('operation_ids'),
        )
        preview = CleaningExecutor().preview(source_parquet, operations)
    except CleaningPlanError as error:
        flash(str(error), 'warning')
        return redirect(url_for('files.cleaning', file_id=record.id))

    return render_template(
        'files/cleaning_preview.html',
        record=record,
        operations=operations,
        preview=preview,
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
        operations = select_executable_operations(
            profile_data['cleaning_plan'],
            request.form.getlist('operation_ids'),
        )
    except CleaningPlanError as error:
        flash(str(error), 'warning')
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
        f'y {metrics["quarantined_rows"]} en cuarentena.',
        'success',
    )
    return redirect(url_for('files.results', file_id=record.id))


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
    dataframe = _pipeline().load_quarantine_dataframe(version.stored_filename)
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
            numeric_cols = summary['numeric_cols']
            text_cols = [c for c in col_names if c not in numeric_cols]

            analysis = {
                'summary': summary,
                'numeric_cols': numeric_cols,
                'text_cols': text_cols,
                'insights': AIInsight.query.filter_by(file_id=active_file.id).all()
            }
        except (OSError, ValueError):
            current_app.logger.exception(
                'No se pudo cargar el análisis del archivo %s', active_file.id
            )

    return render_template('files/insights.html',
        active_file=active_file,
        all_files=all_files,
        analysis=analysis
    )
