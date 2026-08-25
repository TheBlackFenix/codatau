import os
import uuid
from flask import Blueprint, render_template, flash, redirect, url_for, current_app, session
from flask_login import login_required, current_user
from app.extensions import db
from app.models.file_upload import FileUpload
from app.models.ai_insight import AIInsight
from app.forms.file_forms import UploadForm
from app.services.data_service import DataService
from app.services.validation_service import ValidationService
from app.services.ai_service import AIService
from datetime import datetime, timezone

files_bp = Blueprint('files', __name__, url_prefix='/files')


@files_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    form = UploadForm()

    if form.validate_on_submit():
        file = form.file.data
        original_name = file.filename
        ext = os.path.splitext(original_name)[1].lower().lstrip('.')

        # Nombre único para evitar colisiones
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
        file.save(filepath)

        try:
            # Leer archivo
            df = DataService.read_file(filepath)

            # Validar
            errors, warnings = ValidationService.validate_file(df)

            if errors:
                os.remove(filepath)
                for e in errors:
                    flash(e, 'danger')
                return redirect(url_for('files.upload'))

            # Limpiar
            df = DataService.clean_dataframe(df)

            # Resumen
            summary = DataService.get_summary(df)

            # Guardar registro en BD
            upload_record = FileUpload(
                user_id=current_user.id,
                filename=unique_name,
                original_name=original_name,
                file_type=ext,
                file_size=os.path.getsize(filepath),
                row_count=summary['rows'],
                column_count=summary['columns'],
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
            if os.path.exists(filepath):
                os.remove(filepath)
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

    filepath = os.path.join(
        current_app.config['UPLOAD_FOLDER'],
        upload_record.filename
    )

    summary = None
    if os.path.exists(filepath):
        df = DataService.read_file(filepath)
        df = DataService.clean_dataframe(df)
        summary = DataService.get_summary(df)

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

@files_bp.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete(file_id):
    record = FileUpload.query.filter_by(
        id=file_id, user_id=current_user.id
    ).first_or_404()

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], record.filename)

    # Limpiar sesión si era el archivo activo
    if session.get('active_file_id') == file_id:
        session.pop('active_file_id', None)

    db.session.delete(record)
    db.session.commit()

    # El registro es la fuente de verdad; un fallo físico no debe restaurarlo.
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError:
        current_app.logger.exception('No se pudo eliminar el archivo físico %s', filepath)

    flash(f'Archivo "{record.original_name}" eliminado correctamente.', 'success')
    return redirect(url_for('files.upload'))

@files_bp.route('/insights')
@login_required
def insights_ia():
    from flask import session
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
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], active_file.filename)
        if os.path.exists(filepath):
            df = DataService.read_file(filepath)
            df = DataService.clean_dataframe(df)
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

    return render_template('files/insights.html',
        active_file=active_file,
        all_files=all_files,
        analysis=analysis
    )
