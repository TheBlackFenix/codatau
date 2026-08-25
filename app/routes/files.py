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
    session,
    url_for,
)
from flask_login import login_required, current_user
from app.extensions import db
from app.models.file_upload import FileUpload
from app.models.ai_insight import AIInsight
from app.forms.file_forms import UploadForm
from app.services.data_service import DataService
from app.services.validation_service import ValidationService
from app.services.ai_service import AIService
from app.services.dataset_pipeline import DatasetPipeline
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
    return _pipeline().load_dataframe_or_source(record.filename, source_path)


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
        filepath = storage.save(file, unique_name)

        try:
            # Leer archivo
            df = DataService.read_file(filepath)

            # Validar
            errors, warnings = ValidationService.validate_file(df)

            if errors:
                storage.delete(unique_name)
                for e in errors:
                    flash(e, 'danger')
                return redirect(url_for('files.upload'))

            # Limpiar
            df = DataService.clean_dataframe(df)

            # Crear artefacto analítico portable y perfil compacto para IA.
            artifact = pipeline.ingest_dataframe(df, unique_name, filepath)

            # Resumen
            summary = DataService.get_summary(df)

            # Guardar registro en BD
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

            # Flash de warnings de validación
            for w in warnings:
                flash(w, 'warning')

            flash(f'Archivo procesado correctamente: {summary["rows"]} filas, {summary["columns"]} columnas.', 'success')
            return redirect(url_for('files.results', file_id=upload_record.id))

        except Exception as e:
            db.session.rollback()
            storage.delete(unique_name)
            pipeline.remove_artifacts(unique_name)
            current_app.logger.exception(
                'No se pudo procesar el archivo %s: %s', original_name, e
            )
            flash(
                'No fue posible procesar el archivo. Verifica que el formato y el contenido sean válidos.',
                'danger',
            )
            return redirect(url_for('files.upload'))

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
    record = FileUpload.query.filter_by(
        id=file_id, user_id=current_user.id
    ).first_or_404()
    data_profile = _pipeline().load_profile(record.filename)
    if data_profile is None:
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

    # Limpiar sesión si era el archivo activo
    if session.get('active_file_id') == file_id:
        session.pop('active_file_id', None)

    db.session.delete(record)
    db.session.commit()

    # El registro es la fuente de verdad; un fallo físico no debe restaurarlo.
    try:
        storage.delete(record.filename)
        pipeline.remove_artifacts(record.filename)
    except OSError:
        current_app.logger.exception(
            'No se pudieron eliminar todos los artefactos del archivo %s',
            record.filename,
        )

    flash(f'Archivo "{record.original_name}" eliminado correctamente.', 'success')
    return redirect(url_for('files.upload'))


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
