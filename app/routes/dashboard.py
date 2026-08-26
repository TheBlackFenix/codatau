import math
import os

from flask import Blueprint, current_app, render_template, session
from flask_login import login_required, current_user

from app.models.file_upload import FileUpload
from app.models.ai_insight import AIInsight
from app.services.data_service import DataService
from app.services.dataset_pipeline import DatasetPipeline
from app.services.ai_service import AIService

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

            numeric_cols = [
                column
                for column in summary['default_metrics']
                if df[column].notna().sum() > 0
            ]
            col_names = list(df.columns)

            # Gráfica 1: promedio por columna numérica
            if numeric_cols:
                bar_labels = []
                bar_data = []
                for c in numeric_cols:
                    val = df[c].mean()
                    if val == val and math.isfinite(float(val)):
                        bar_labels.append(str(c))
                        bar_data.append(round(float(val), 2))
                if bar_labels:
                    chart_data['bar'] = {
                        'labels': bar_labels,
                        'datos': bar_data,
                    }

            # Gráfica 2: nulos por columna
            null_dict = {}
            for col in col_names:
                n = int(df[col].isnull().sum())
                if n > 0:
                    null_dict[str(col)] = n
            if null_dict:
                chart_data['nulls'] = {
                    'labels': list(null_dict.keys()),
                    'datos': list(null_dict.values()),
                }

            # Gráfica 3: columna texto agrupada por columna numérica
            text_cols = DataService.text_columns(df)
            if text_cols and numeric_cols:
                group_col = text_cols[0]
                num_col = numeric_cols[0]
                try:
                    grouped = (
                        df.groupby(group_col)[num_col]
                        .sum()
                        .dropna()
                        .head(10)
                    )
                    pairs = [
                        (str(label), round(float(value), 2))
                        for label, value in grouped.items()
                        if math.isfinite(float(value))
                    ]
                    g_labels = [label for label, _ in pairs]
                    g_datos = [value for _, value in pairs]
                    if g_labels:
                        chart_data['grouped'] = {
                            'labels': g_labels,
                            'datos': g_datos,
                            'group_col': str(group_col),
                            'num_col': str(num_col),
                        }
                except (TypeError, ValueError):
                    current_app.logger.warning(
                        'No se pudo construir la gráfica agrupada para %s y %s',
                        group_col,
                        num_col,
                    )

        except Exception:
            current_app.logger.exception(
                'No se pudo construir el dashboard para el archivo %s',
                active_file.id,
            )
            summary = None
            chart_data = {}

    total_files = len(all_files)
    total_rows = sum(f.row_count or 0 for f in all_files)
    total_insights = AIInsight.query.filter_by(user_id=current_user.id).count()

    return render_template('dashboard/index.html',
        all_files=all_files,
        active_file=active_file,
        summary=summary,
        chart_data=chart_data,
        insights=insights,
        total_files=total_files,
        total_rows=total_rows,
        total_insights=total_insights,
    )
