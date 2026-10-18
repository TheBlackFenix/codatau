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
from app.services.ai_cleaning_service import AICleaningService
from app.services.ai_providers import AIProviderError, AIProviderFactory
from app.services.dataset_context_service import DatasetContextService
from app.services.dataset_pipeline import DatasetPipeline
from app.services.semantic_constraint_service import SemanticConstraintService
from app.services.quality_insight_service import QualityInsightService
from app.services.cleaning_executor import (
    CleaningExecutor,
    CleaningPlanError,
    select_configured_operations,
)
from app.services.cleaning_decision_service import CleaningDecisionService
from app.services.custom_cleaning_rule_service import CustomCleaningRuleService
from app.services.cleaning_export_service import CleaningExportService
from app.services.dashboard_service import DashboardService
from app.services.storage_service import LocalStorageService

files_bp = Blueprint('files', __name__, url_prefix='/files')


@files_bp.route('/cleaning/<int:file_id>/columns', methods=['GET', 'POST'])
@login_required
def configure_columns(file_id):
    from app.models.cleaning_configuration import CleaningConfiguration
    from app.services.column_type_service import ColumnTypeService
    from sqlalchemy.orm.exc import StaleDataError
    from sqlalchemy.exc import IntegrityError
    record = _record_for_user(file_id)
    _, version, _, profile = _cleaning_context(record)
    configuration = CleaningConfiguration.query.filter_by(file_id=record.id, user_id=current_user.id).first()
    stale = configuration is not None and configuration.source_version != version
    saved = configuration.columns if configuration else {}
    cards = ColumnTypeService.cards(profile, _latest_dataset_context(record, profile), saved)
    form = CleaningActionForm()
    if request.method == 'POST':
        if not form.validate_on_submit():
            abort(400)
        revision = str(configuration.revision if configuration else 0)
        if request.form.get('revision') != revision or request.form.get('source_version') != version:
            flash('El archivo o la configuración cambió en otra ventana. Recarga antes de guardar.', 'warning')
            return redirect(url_for('files.configure_columns', file_id=file_id))
        try:
            choices = ColumnTypeService.parse(request.form, cards)
            dictionaries_reset = configuration is not None and any(
                previous.get('category_dictionary') and not choices.get(name, {}).get('category_dictionary')
                for name, previous in configuration.columns.items())
            if configuration is None:
                configuration = CleaningConfiguration(file_id=record.id, user_id=current_user.id)
                db.session.add(configuration)
            if configuration.columns != choices or configuration.source_version != version:
                configuration.duplicates = {}
                configuration.treatments = {}
                configuration.ai_review = {}
            configuration.columns = choices
            configuration.source_version = version
            db.session.commit()
            if dictionaries_reset:
                flash('Cambiaste el tipo, las reglas de texto o los marcadores de una columna. Revisa de nuevo sus equivalencias.', 'warning')
            flash('Tipos guardados. No se modificó ningún dato. Puedes regresar y cambiar tus decisiones.', 'success')
            return redirect(url_for('files.configure_duplicates' if request.form.get('next') == 'duplicates' else 'files.configure_columns', file_id=file_id))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'warning')
            # Preserve valid submitted selections when another column has an error.
            for card in cards:
                key = f'column_{card["index"]}'
                if request.form.get(key) in ColumnTypeService.TYPES:
                    previous_dictionary = card['choice'].get('category_dictionary')
                    card['choice'] = {'type': request.form[key], 'decimal_separator': request.form.get(key+'_separator', '.'), 'date_format': request.form.get(key+'_date', 'iso'), 'date_source': request.form.get(key+'_date_source', request.form.get(key+'_date', 'iso')), 'negative_policy': request.form.get(key+'_negative', 'unknown')}
                    if previous_dictionary:
                        card['choice']['category_dictionary'] = previous_dictionary
                    card['choice'].update({field: request.form.get(key+'_'+field, '')[:64] for field in ('minimum','maximum')})
                    card['choice'].update(numeric_rules=request.form.get(key+'_numeric_rules', ''), number_source=request.form.get(key+'_number_source', 'auto'))
                    card['choice']['text_rules'] = {field: request.form.get(key+'_text_'+field) == '1' for field in ('trim','collapse_spaces','clean_controls','nfc')}
                    card['choice']['text_rules']['case'] = request.form.get(key+'_text_case', 'keep')
                    card['choice']['missing_values'] = {'values': request.form.get(key+'_missing_markers', '')[:2020].splitlines(), 'ignore_case': request.form.get(key+'_missing_ignore_case') == '1'}
        except (StaleDataError, IntegrityError):
            db.session.rollback()
            flash('Otra ventana guardó cambios. Recarga la configuración antes de continuar.', 'warning')
            return redirect(url_for('files.configure_columns', file_id=file_id))
    return render_template('files/column_types.html', record=record, cards=cards, form=form,
                           configuration=configuration, source_version=version, stale=stale,
                           type_labels=ColumnTypeService.TYPES, date_formats=ColumnTypeService.DATES)

@files_bp.route('/cleaning/<int:file_id>/categories/<int:column_index>', methods=['GET', 'POST'])
@login_required
def configure_categories(file_id, column_index):
    from copy import deepcopy
    from sqlalchemy.orm.exc import StaleDataError
    from app.models.cleaning_configuration import CleaningConfiguration
    from app.services.category_dictionary_service import CategoryDictionaryService as Categories
    record = _record_for_user(file_id)
    config = CleaningConfiguration.query.filter_by(file_id=file_id, user_id=current_user.id).first()
    if config is None or config.source_version != record.active_stored_filename:
        flash('Guarda primero los tipos de la versión actual para configurar equivalencias.', 'warning')
        return redirect(url_for('files.configure_columns', file_id=file_id))
    profile = _cleaning_context(record)[3]
    if column_index >= len(profile['columns']): abort(404)
    column = profile['columns'][column_index]['name']
    preference = config.columns.get(column, {})
    if preference.get('type') != 'text': abort(404)
    available, excluded = Categories.values(_load_dataframe(record)[column], preference)
    dictionary = preference.get('category_dictionary', {})
    form = CleaningActionForm()
    selected = request.form.getlist('selected') if request.method == 'POST' else []
    target = request.form.get('target', '')[:200] if request.method == 'POST' else ''
    if request.method == 'POST':
        if not form.validate_on_submit(): abort(400)
        if request.form.get('revision') != str(config.revision) or request.form.get('source_version') != record.active_stored_filename:
            flash('La configuración o versión cambió. Recarga antes de guardar equivalencias.', 'warning')
            return redirect(url_for('files.configure_categories', file_id=file_id, column_index=column_index))
        try:
            action = request.form.get('action')
            if action == 'add':
                updated = Categories.add(dictionary, selected, request.form.get('target', ''), available, preference)
            elif action == 'remove':
                mapping = dictionary.get('mapping', {})
                targets = {Categories.key(value): value for value in mapping.values()}
                group = targets.get(request.form.get('group'))
                if group is None: raise ValueError('El grupo ya no existe. Recarga el diccionario.')
                updated = Categories.validate({key: value for key, value in mapping.items() if value != group}, preference)
            else:
                raise ValueError('Elige si deseas guardar o quitar un grupo de equivalencias.')
            columns = deepcopy(config.columns)
            if updated['mapping']:
                columns[column]['category_dictionary'] = updated
            else:
                columns[column].pop('category_dictionary', None)
            if columns != config.columns:
                config.columns = columns
                config.duplicates = {}; config.treatments = {}; config.ai_review = {}
            db.session.commit()
            flash('Diccionario guardado. No se modificó ningún dato. Revisa los pasos siguientes para aplicarlo.', 'success')
            return redirect(url_for('files.configure_categories', file_id=file_id, column_index=column_index))
        except ValueError as error:
            db.session.rollback(); flash(str(error), 'warning')
        except StaleDataError:
            db.session.rollback()
            flash('Otra ventana guardó cambios. Recarga el diccionario.', 'warning')
            return redirect(url_for('files.configure_categories', file_id=file_id, column_index=column_index))
    query = request.args.get('q', '')[:200]
    filtered = [item for item in available if query.casefold() in item['value'].casefold()]
    pages = max(1, (len(filtered)+49)//50)
    page = min(pages, max(1, request.args.get('page', 1, type=int)))
    counts = {item['value']: item['count'] for item in available}
    groups = {}
    for source, destination in dictionary.get('mapping', {}).items():
        group = groups.setdefault(destination, {'id': Categories.key(destination), 'target': destination, 'sources': [], 'count': 0})
        group['sources'].append(source); group['count'] += counts.get(source, 0)
    return render_template('files/category_dictionary.html', record=record, configuration=config, column=column,
        column_index=column_index, form=form, values=filtered[(page-1)*50:page*50], total=len(available),
        filtered_count=len(filtered), excluded=excluded, query=query, page=page, pages=pages,
        groups=groups.values(), selected=selected, target=target)


@files_bp.route('/cleaning/<int:file_id>/duplicates', methods=['GET', 'POST'])
@login_required
def configure_duplicates(file_id):
    from app.models.cleaning_configuration import CleaningConfiguration
    from app.services.duplicate_configuration_service import DuplicateConfigurationService as Duplicates
    from sqlalchemy.orm.exc import StaleDataError
    record = _record_for_user(file_id)
    config = CleaningConfiguration.query.filter_by(file_id=file_id, user_id=current_user.id).first()
    if config is None or config.source_version != record.active_stored_filename:
        flash('Guarda primero los tipos de columnas de la versión activa.', 'warning')
        return redirect(url_for('files.configure_columns', file_id=file_id))
    dataframe = _load_dataframe(record)
    form = CleaningActionForm()
    settings = config.duplicates or {'mode': 'none'}
    preview = None
    if request.method == 'POST':
        if not form.validate_on_submit():
            abort(400)
        if request.form.get('revision') != str(config.revision) or request.form.get('source_version') != record.active_stored_filename:
            flash('La configuración cambió en otra ventana. Recarga antes de guardar.', 'warning')
            return redirect(url_for('files.configure_duplicates', file_id=file_id))
        try:
            settings = Duplicates.parse(request.form, list(dataframe.columns), config.columns)
            Duplicates.preview(dataframe, settings, config.columns)
            if config.duplicates != settings:
                config.treatments = {}
                config.ai_review = {}
            config.duplicates = settings
            db.session.commit()
            flash('Comparación guardada. Todavía no se eliminó ninguna fila.', 'success')
            return redirect(url_for('files.configure_treatments' if request.form.get('next') == 'treatments' else 'files.configure_duplicates', file_id=file_id))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'warning')
            settings = config.duplicates or {'mode': 'none'}
        except StaleDataError:
            db.session.rollback()
            flash('Otra ventana guardó cambios. Revisa la configuración actual.', 'warning')
            return redirect(url_for('files.configure_duplicates', file_id=file_id))
    if config.duplicates:
        preview = Duplicates.preview(dataframe, settings, config.columns)
    return render_template('files/duplicates.html', record=record, configuration=config, form=form,
                           settings=settings, preview=preview, columns=list(dataframe.columns),
                           date_columns=[name for name,item in config.columns.items() if item['type'] == 'date'])


@files_bp.route('/cleaning/<int:file_id>/treatments', methods=['GET', 'POST'])
@login_required
def configure_treatments(file_id):
    from app.models.cleaning_configuration import CleaningConfiguration
    from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
    from app.services.date_normalization_service import DateNormalizationService as Dates
    from app.services.numeric_normalization_service import NumericNormalizationService as Numbers
    from app.services.text_normalization_service import TextNormalizationService as Texts
    from app.services.category_dictionary_service import CategoryDictionaryService as Categories
    from sqlalchemy.orm.exc import StaleDataError
    record = _record_for_user(file_id)
    config = CleaningConfiguration.query.filter_by(file_id=file_id, user_id=current_user.id).first()
    if config is None or config.source_version != record.active_stored_filename or not config.duplicates:
        flash('Guarda primero los tipos y la comparación de duplicados de esta versión.', 'warning')
        return redirect(url_for('files.configure_duplicates', file_id=file_id))
    _, _, _, profile = _cleaning_context(record)
    dataframe = _load_dataframe(record)
    issues = Treatments.issues(dataframe, config.columns, _latest_dataset_context(record, profile))
    form = CleaningActionForm()
    decisions = (config.treatments or {}).get('decisions', {})
    if request.method == 'POST':
        if not form.validate_on_submit(): abort(400)
        if request.form.get('revision') != str(config.revision) or request.form.get('source_version') != record.active_stored_filename:
            flash('La configuración cambió. Recarga la pantalla antes de guardar.', 'warning')
            return redirect(url_for('files.configure_treatments', file_id=file_id))
        try:
            treatments = Treatments.parse(request.form, issues)
            if config.treatments != treatments:
                config.ai_review = {}
            config.treatments = treatments
            db.session.commit()
            flash('Tratamientos guardados. No se modificó ningún dato ni se envió información a la IA.', 'success')
            return redirect(url_for('files.review_values' if request.form.get('next') == 'ai' else 'files.configure_treatments', file_id=file_id))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'warning')
            decisions = {issue['id']: {'action': request.form.get('action_'+issue['id'], 'keep'), 'replacement': request.form.get('replacement_'+issue['id'], '')[:200]} for issue in issues}
        except StaleDataError:
            db.session.rollback()
            flash('Otra ventana guardó cambios. Revisa las decisiones actuales.', 'warning')
            return redirect(url_for('files.configure_treatments', file_id=file_id))
    return render_template('files/treatments.html', record=record, configuration=config, form=form,
                           issues=issues, decisions=decisions, actions=Treatments.ACTIONS,
                           date_summaries=Dates.summaries(dataframe, config.columns),
                           numeric_summaries=Numbers.summaries(dataframe, config.columns),
                           text_summaries=Texts.summaries(dataframe, config.columns),
                           category_summaries=Categories.summaries(dataframe, config.columns),
                           treatment_review_needed=bool(config.treatments) and set(decisions) != {issue['id'] for issue in issues})


@files_bp.route('/cleaning/<int:file_id>/value-proposals', methods=['GET', 'POST'])
@login_required
def review_values(file_id):
    from copy import deepcopy
    from app.models.cleaning_configuration import CleaningConfiguration
    from app.services.value_proposal_service import ValueProposalService as Proposals
    from sqlalchemy.orm.exc import StaleDataError
    record = _record_for_user(file_id)
    config = CleaningConfiguration.query.filter_by(file_id=file_id, user_id=current_user.id).first()
    if config is None or config.source_version != record.active_stored_filename or not config.treatments:
        flash('Guarda primero los tratamientos de esta versión.', 'warning')
        return redirect(url_for('files.configure_treatments', file_id=file_id))
    _, _, _, profile = _cleaning_context(record)
    context = _latest_dataset_context(record, profile)
    try:
        candidates = Proposals.candidates(_load_dataframe(record), config, context)
    except ValueError as error:
        flash(str(error), 'warning')
        return redirect(url_for('files.configure_treatments', file_id=file_id))
    fingerprint = Proposals.fingerprint(record, config, context, current_app.config)
    review = deepcopy(config.ai_review or {})
    if review.get('fingerprint') != fingerprint:
        review = {'fingerprint':fingerprint, 'proposals':{}, 'decisions':{}}
    form = CleaningActionForm()
    if request.method == 'POST':
        if not form.validate_on_submit(): abort(400)
        if request.form.get('revision') != str(config.revision) or request.form.get('fingerprint') != fingerprint:
            flash('El archivo, el modelo o tus decisiones cambiaron. Recarga antes de continuar.', 'warning')
            return redirect(url_for('files.review_values', file_id=file_id))
        try:
            if request.form.get('action') == 'analyze':
                review = Proposals.analyze(record, config, candidates, context, current_app.config)
                flash('Propuestas preparadas para tu revisión. No se modificaron los datos.', 'success')
            elif request.form.get('action') in {'decide', 'decide_continue'}:
                for item in candidates:
                    proposal = review['proposals'].get(item['id'])
                    if not proposal: continue
                    choice = request.form.get('decision_'+item['id'])
                    if choice not in {None, '', 'accept', 'reject'}: raise ValueError('Elige aceptar o conservar para cada propuesta.')
                    if choice == 'accept' and proposal['status'] != 'replace': raise ValueError('Esta propuesta requiere una decisión manual; no se puede aplicar.')
                    if choice:
                        review['decisions'][item['id']] = choice
                    elif choice == '':
                        review['decisions'].pop(item['id'], None)
                flash('Decisiones guardadas. Se aplicarán únicamente al confirmar la limpieza completa.', 'success')
            else: abort(400)
            # A different tab may have changed the configuration while the provider was responding.
            db.session.refresh(config)
            db.session.expire(record, ['versions'])
            if str(config.revision) != request.form['revision'] or record.active_stored_filename != config.source_version:
                db.session.commit()  # Preserve the provider audit, not stale choices.
                flash('La configuración cambió durante el análisis. Vuelve a revisar las propuestas.', 'warning')
                return redirect(url_for('files.review_values', file_id=file_id))
            config.ai_review = review
            db.session.commit()
            if request.form.get('action') == 'decide_continue':
                return redirect(url_for('files.wizard_preview', file_id=file_id))
            return redirect(url_for('files.review_values', file_id=file_id))
        except AIProviderError as error:
            db.session.commit()
            flash(error.user_message + ' Tus datos y decisiones anteriores siguen intactos.', 'warning')
        except ValueError as error:
            db.session.rollback(); flash(str(error), 'warning')
        except StaleDataError:
            db.session.rollback(); flash('Otra ventana guardó cambios. Recarga esta pantalla.', 'warning')
            return redirect(url_for('files.review_values', file_id=file_id))
    remaining = sum(item['id'] not in review['proposals'] for item in candidates)
    undecided = sum(item['id'] not in review['decisions'] for item in candidates)
    return render_template('files/value_proposals.html', record=record, configuration=config,
                           form=form, review=review, candidates=candidates, remaining=remaining, undecided=undecided,
                           ai_configured=AIProviderFactory.is_configured(current_app.config), fingerprint=fingerprint)


def _wizard_ready(record):
    from app.models.cleaning_configuration import CleaningConfiguration
    config=CleaningConfiguration.query.filter_by(file_id=record.id,user_id=current_user.id).first()
    if config is None or config.source_version!=record.active_stored_filename or not config.treatments:
        raise ValueError('Guarda la configuración completa de la versión activa antes de revisar y aplicar.')
    return config


def _wizard_token(record, config, context):
    from app.services.value_proposal_service import ValueProposalService
    from app.services.wizard_execution_service import WizardExecutionService
    return {'file':record.id,'user':current_user.id,'source':record.active_stored_filename,
            'rule_version':WizardExecutionService.RULE_VERSION,
            'revision':config.revision,'fingerprint':ValueProposalService.fingerprint(record,config,context,current_app.config),
            'source_hash':_pipeline()._sha256(_pipeline().paths_for(record.active_stored_filename)[0])}


@files_bp.route('/cleaning/<int:file_id>/wizard-preview',methods=['GET','POST'])
@login_required
def wizard_preview(file_id):
    from itsdangerous import URLSafeTimedSerializer, BadSignature
    from werkzeug.exceptions import HTTPException
    from sqlalchemy import update, func
    from app.models.cleaning_configuration import CleaningConfiguration
    from app.services.wizard_execution_service import WizardExecutionService
    record=_record_for_user(file_id)
    form=CleaningActionForm()
    serializer=URLSafeTimedSerializer(current_app.secret_key,salt='cleaning-wizard-confirmation')
    filename=None
    try:
        config=_wizard_ready(record)
        pipeline,_,_,profile=_cleaning_context(record)
        context=_latest_dataset_context(record,profile)
        expected=_wizard_token(record,config,context)
        if request.method=='POST':
            if not form.validate_on_submit():abort(400)
            try:submitted=serializer.loads(request.form.get('preview_token',''),max_age=1800)
            except BadSignature:raise ValueError('La vista previa venció o no es válida. Revísala de nuevo.')
            if submitted!=expected:raise ValueError('El archivo o tus decisiones cambiaron. Genera otra vista previa.')
            # Consume the draft revision atomically before writing any artifacts.
            locked=db.session.execute(update(CleaningConfiguration).where(CleaningConfiguration.id==config.id,CleaningConfiguration.revision==expected['revision']).values(revision=expected['revision']+1))
            if locked.rowcount!=1:raise ValueError('La limpieza ya se confirmó o cambió en otra ventana.')
            db.session.expire(record,['versions'])
            if record.active_stored_filename!=expected['source']:raise ValueError('La versión activa cambió. Revisa de nuevo la limpieza.')
        result=WizardExecutionService.build(_load_dataframe(record),record,config,context,current_app.config)
        if request.method=='POST':
            import duckdb
            number=(db.session.query(func.max(DatasetVersion.version_number)).filter_by(file_id=record.id).scalar() or 0)+1
            filename=f'{Path(record.filename).stem}.wizard-{uuid.uuid4().hex}.csv'
            parquet,_=pipeline.paths_for(filename)
            connection=duckdb.connect()
            try:
                connection.from_df(result.cleaned).write_parquet(str(parquet),compression='zstd')
                if not result.quarantine.empty:
                    connection.from_df(result.quarantine).write_parquet(str(pipeline.quarantine_path_for(filename)),compression='zstd')
            finally:connection.close()
            artifact=pipeline.profile_existing(filename,_storage().path_for(record.filename))
            for old in record.versions:old.is_active=False
            version=DatasetVersion(file_id=record.id,created_by=current_user.id,version_number=number,
                stored_filename=filename,is_active=True,
                quarantine_filename=pipeline.quarantine_path_for(filename).name if not result.quarantine.empty else None,
                metrics=result.metrics,operations=[{'operation':'configured_cleaning','source_version':expected['source'],
                'rule_version':WizardExecutionService.RULE_VERSION,
                'columns':config.columns,'duplicates':config.duplicates,'treatments':config.treatments,
                'ai_review':config.ai_review,'changes':result.changes,'duplicate_source_rows':result.duplicate_rows,
                'mixed_columns':result.mixed_columns}])
            db.session.add(version)
            record.row_count=artifact.profile['row_count'];record.column_count=artifact.profile['column_count']
            db.session.commit()
            flash(f'Limpieza confirmada. Se creó la versión {number}; el original sigue intacto.','success')
            return redirect(url_for('files.wizard_result',file_id=file_id,version_id=version.id))
        return render_template('files/wizard_preview.html',record=record,configuration=config,form=form,
                               result=result,preview_token=serializer.dumps(expected))
    except ValueError as error:
        db.session.rollback()
        if filename:_pipeline().remove_artifacts(filename)
        flash(str(error),'warning')
        return redirect(url_for('files.review_values',file_id=file_id))
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        if filename:_pipeline().remove_artifacts(filename)
        current_app.logger.exception('No se pudo completar el asistente del archivo %s',record.id)
        flash('No pudimos crear la versión. No se aplicaron cambios; puedes intentarlo de nuevo.','danger')
        return redirect(url_for('files.review_values',file_id=file_id))


@files_bp.route('/cleaning/<int:file_id>/wizard-result/<int:version_id>')
@login_required
def wizard_result(file_id,version_id):
    record=_record_for_user(file_id)
    version=DatasetVersion.query.filter_by(id=version_id,file_id=file_id).first_or_404()
    return render_template('files/wizard_result.html',record=record,version=version,form=CleaningActionForm())


CLEANING_PARAMETER_FIELDS = {
    'cast_type': ('decimal_separator',),
    'handle_missing': ('invalid_action', 'replacement_value'),
    'parse_date': ('date_format',),
    'normalize_case': ('case_style',),
    'normalize_phone': ('phone_style',),
    'validate_email': ('invalid_action', 'replacement_value'),
    'validate_range': ('invalid_action', 'replacement_value'),
}

INVALID_ACTION_OPERATIONS = {
    'handle_missing',
    'validate_email',
    'validate_range',
}

CLEANING_OPERATION_LABELS = {
    'blank_to_null': 'Convertir textos vacíos en valores nulos',
    'cast_type': 'Convertir a número',
    'handle_missing': 'Resolver valores vacíos',
    'normalize_boolean': 'Normalizar valores Sí/No',
    'normalize_case': 'Unificar mayúsculas y minúsculas',
    'normalize_phone': 'Normalizar teléfonos',
    'parse_date': 'Convertir a fecha',
    'remove_exact_duplicates': 'Eliminar filas duplicadas exactas',
    'review_invalid_values': 'Revisar valores inválidos con IA',
    'trim_text': 'Eliminar espacios externos',
    'validate_email': 'Resolver correos inválidos',
    'validate_range': 'Resolver valores fuera del rango esperado',
}

QUARANTINE_EFFECTS = {
    'handle_missing': 'Las filas con valores vacíos se separarán del resultado.',
    'validate_email': 'Las filas con correos inválidos se separarán del resultado.',
    'validate_range': 'Las filas con valores fuera del rango se separarán del resultado.',
    'cast_type': 'Los valores que no puedan convertirse a número se separarán del resultado.',
    'parse_date': 'Las fechas que no coincidan con el formato se separarán del resultado.',
    'normalize_boolean': 'Los valores que no representen Sí o No se separarán del resultado.',
    'normalize_phone': 'Los teléfonos con una longitud inválida se separarán del resultado.',
}

QUARANTINE_REASON_LABELS = {
    'handle_missing': 'Valor faltante',
    'validate_email': 'Correo inválido',
    'validate_range': 'Valor fuera del rango esperado',
    'cast_type': 'Valor no convertible a número',
    'parse_date': 'Fecha no reconocida',
    'normalize_boolean': 'Valor Sí/No no reconocido',
    'normalize_phone': 'Teléfono con longitud inválida',
}


def _quarantine_reason_labels(decisions):
    labels = {}
    for decision in decisions:
        operation = decision['operation']
        label = QUARANTINE_REASON_LABELS.get(operation)
        if not label:
            continue
        column = decision.get('column')
        if column:
            label = f'{label} · columna {column}'
        labels[decision['operation_id']] = label
    return labels


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
    context_outcome = DatasetContextService.latest(
        record,
        profile_data,
        current_user.id,
        current_app.config,
    )
    if context_outcome:
        profile_data = SemanticConstraintService.enrich_plan(
            profile_data,
            context_outcome.context,
            source_parquet,
        )
    profile_data = CustomCleaningRuleService.enrich_plan(
        profile_data,
        CleaningDecision.query.filter_by(file_id=record.id).all(),
        source_parquet,
    )
    return pipeline, stored_filename, source_parquet, profile_data


def _latest_dataset_context(record, profile_data):
    outcome = DatasetContextService.latest(
        record,
        profile_data,
        current_user.id,
        current_app.config,
    )
    return outcome.context if outcome else None


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


def _submitted_cleaning_decisions(profile_data, source_parquet, resolved_ids=None):
    operations = list(profile_data['cleaning_plan']['operations'])
    manual_rule = CustomCleaningRuleService.from_form(
        profile_data,
        request.form,
        source_parquet,
    )
    if manual_rule and manual_rule['id'] not in {
        operation['id'] for operation in operations
    }:
        operations.append(manual_rule)
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
    if manual_rule:
        choices[manual_rule['id']] = 'apply'

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
            {
                **profile_data['cleaning_plan'],
                'operations': operations,
            },
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
    return selected_operations, decisions, manual_rule


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
            # Preserve text so the wizard can propose, rather than pre-apply, trim.
            df = DataService.clean_dataframe(df, trim_text=False)

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

            # El contexto semántico mejora los análisis posteriores, pero nunca
            # debe convertir una falla del proveedor en una carga fallida.
            if AIProviderFactory.is_configured(current_app.config):
                try:
                    DatasetContextService.analyze(
                        upload_record,
                        artifact.profile,
                        current_user.id,
                        current_app.config,
                    )
                    db.session.commit()
                except AIProviderError as error:
                    # El servicio deja trazabilidad del intento fallido cuando
                    # alcanzó a crear una ejecución.
                    db.session.commit()
                    current_app.logger.warning(
                        'No se pudo generar el contexto semántico de %s (%s): %s',
                        upload_record.id,
                        error.code,
                        error,
                    )
                    flash(
                        'El archivo quedó procesado, pero no pudimos generar su '
                        'contexto con IA. Podrás intentarlo nuevamente.',
                        'warning',
                    )
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception(
                        'Falló inesperadamente el contexto semántico del archivo %s',
                        upload_record.id,
                    )
                    flash(
                        'El archivo quedó procesado, pero su contexto semántico '
                        'está pendiente.',
                        'warning',
                    )

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
        _, _, _, profile_data = _cleaning_context(upload_record)
        dataset_context = _latest_dataset_context(upload_record, profile_data)
        insights = QualityInsightService.decorate(
            AIService.generate_display_insights(dataframe, summary),
            profile_data,
            CleaningDecisionService.active_for_file(upload_record.id),
        )
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
        dataset_context=dataset_context,
        context_record=upload_record,
        context_form=CleaningActionForm(),
        ai_configured=AIProviderFactory.is_configured(current_app.config),
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
    pending_profile = AICleaningService.pending_profile(profile_data, resolved_ids)
    pending_operations = pending_profile['cleaning_plan']['operations']
    target_ids = {operation['id'] for operation in pending_operations}
    operations = [
        operation
        for operation in pending_operations
        if not (
            operation['operation'] == 'review_invalid_values'
            and f"{operation['column']}:validate_email" in target_ids
        )
    ]
    operation_groups = {}
    for operation in operations:
        column = operation.get('column') or 'Archivo completo'
        operation_groups.setdefault(column, []).append(operation)
    executable_ids = {
        operation['id']
        for operation in operations
        if CleaningExecutor.is_executable(operation)
    }
    ai_candidate_count = sum(
        operation['decision'] == 'ai_analysis'
        for operation in pending_operations
    )
    ai_configured = AIProviderFactory.is_configured(current_app.config)
    dataset_context = _latest_dataset_context(record, profile_data)
    ai_outcome = AICleaningService.latest(
        record,
        pending_profile,
        current_user.id,
        current_app.config,
        dataset_context=dataset_context,
    )
    ai_suggestions = {
        suggestion['operation_id']: suggestion
        for suggestion in (ai_outcome.suggestions if ai_outcome else [])
        if suggestion['operation_id'] not in resolved_ids
    }
    for operation in operations:
        if operation['operation'] == 'validate_email':
            advisory_id = f"{operation['column']}:review_invalid_values"
            if advisory_id in ai_suggestions:
                ai_suggestions[operation['id']] = ai_suggestions[advisory_id]
    versions = DatasetVersion.query.filter_by(file_id=record.id).order_by(
        DatasetVersion.version_number.desc()
    ).all()
    return render_template(
        'files/cleaning.html',
        record=record,
        profile=profile_data,
        operations=operations,
        operation_groups=operation_groups,
        executable_ids=executable_ids,
        automatic_ids={
            operation['id']
            for operation in operations
            if operation['decision'] == 'automatic'
            and operation['id'] in executable_ids
        },
        operation_labels=CLEANING_OPERATION_LABELS,
        quarantine_effects=QUARANTINE_EFFECTS,
        invalid_action_operations=INVALID_ACTION_OPERATIONS,
        versions=versions,
        resolved_decisions=resolved_decisions,
        current_operation_ids=current_operation_ids,
        ai_candidate_count=ai_candidate_count,
        ai_configured=ai_configured,
        ai_provider=current_app.config.get('AI_PROVIDER'),
        ai_model=AIProviderFactory.configuration_from_app(current_app.config).model,
        ai_outcome=ai_outcome,
        ai_suggestions=ai_suggestions,
        dataset_context=dataset_context,
        context_record=record,
        context_form=CleaningActionForm(),
        form=CleaningActionForm(),
    )


@files_bp.route('/cleaning/<int:file_id>/ai-analysis', methods=['POST'])
@login_required
def cleaning_ai_analysis(file_id):
    form = CleaningActionForm()
    if not form.validate_on_submit():
        abort(400)
    record = _record_for_user(file_id)
    try:
        _, _, _, profile_data = _cleaning_context(record)
        dataset_context = _latest_dataset_context(record, profile_data)
        pending_profile = AICleaningService.pending_profile(
            profile_data,
            {decision.operation_id for decision in CleaningDecisionService.active_for_file(record.id)},
        )
        outcome = AICleaningService.analyze(
            record,
            pending_profile,
            current_user.id,
            current_app.config,
            dataset_context=dataset_context,
        )
        db.session.commit()
    except AIProviderError as error:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        if error.detail:
            current_app.logger.warning(
                'Falló el análisis IA del archivo %s (%s): %s',
                record.id,
                error.code,
                error.detail,
            )
        flash(error.user_message, 'warning')
        return redirect(url_for('files.cleaning', file_id=record.id))
    except Exception as error:
        db.session.rollback()
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudo guardar el análisis IA del archivo %s: %s',
            reference,
            record.id,
            error,
        )
        flash(
            'No pudimos completar el análisis con IA. Ningún dato fue modificado. '
            f'Referencia: {reference}.',
            'danger',
        )
        return redirect(url_for('files.cleaning', file_id=record.id))

    if outcome.cached:
        flash(
            'Se reutilizó el análisis IA existente para evitar un consumo innecesario.',
            'info',
        )
    else:
        flash(
            f'La IA analizó {outcome.run.candidate_count} caso(s) ambiguo(s). '
            'Revisa sus recomendaciones antes de decidir.',
            'success',
        )
    return redirect(url_for('files.cleaning', file_id=record.id))


@files_bp.route('/context/<int:file_id>/analyze', methods=['POST'])
@login_required
def dataset_context_analyze(file_id):
    form = CleaningActionForm()
    if not form.validate_on_submit():
        abort(400)
    record = _record_for_user(file_id)
    try:
        _, _, _, profile_data = _cleaning_context(record)
        outcome = DatasetContextService.analyze(
            record,
            profile_data,
            current_user.id,
            current_app.config,
            force=request.form.get('force') == '1',
        )
        db.session.commit()
    except AIProviderError as error:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash(error.user_message, 'warning')
        return redirect(url_for('files.results', file_id=record.id))
    except Exception:
        db.session.rollback()
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudo generar el contexto del archivo %s',
            reference,
            record.id,
        )
        flash(
            f'No pudimos generar el contexto semántico. Referencia: {reference}.',
            'danger',
        )
        return redirect(url_for('files.results', file_id=record.id))

    if outcome.cached:
        flash('Se reutilizó el contexto semántico existente.', 'info')
    else:
        flash('El contexto semántico del archivo quedó actualizado.', 'success')
    return redirect(url_for('files.results', file_id=record.id))


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
        operations, decisions, manual_rule = _submitted_cleaning_decisions(
            profile_data,
            source_parquet,
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
        quarantine_effects=QUARANTINE_EFFECTS,
        invalid_action_operations=INVALID_ACTION_OPERATIONS,
        quarantine_reason_labels=_quarantine_reason_labels(decisions),
        manual_rule=manual_rule,
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
        operations, decisions, _ = _submitted_cleaning_decisions(
            profile_data,
            source_parquet,
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


@files_bp.route('/cleaning/<int:file_id>/export/<int:version_id>')
@login_required
def cleaning_export(file_id, version_id):
    record = _record_for_user(file_id)
    version = DatasetVersion.query.filter_by(
        id=version_id,
        file_id=record.id,
    ).first_or_404()
    pipeline = _pipeline()
    try:
        cleaned = pipeline.load_dataframe(version.stored_filename)
        quarantine = pipeline.load_quarantine_dataframe(version.stored_filename)
        decisions = (
            CleaningDecision.query
            .filter_by(
                file_id=record.id,
                applied_version_number=version.version_number,
            )
            .order_by(CleaningDecision.id.asc())
            .all()
        )
        package = CleaningExportService.build(
            record,
            version,
            cleaned,
            quarantine,
            decisions,
        )
    except Exception as error:
        reference = uuid.uuid4().hex[:8].upper()
        current_app.logger.exception(
            '[%s] No se pudo exportar la versión %s del archivo %s: %s',
            reference,
            version.id,
            record.id,
            error,
        )
        flash(
            f'No pudimos generar el paquete de calidad. Referencia: {reference}.',
            'danger',
        )
        return redirect(url_for('files.cleaning', file_id=record.id))

    base_name = secure_filename(Path(record.original_name).stem) or 'archivo'
    return send_file(
        package,
        mimetype='application/zip',
        as_attachment=True,
        download_name=(
            f'calidad_v{version.version_number}_{base_name}.zip'
        ),
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
            _, _, _, profile_data = _cleaning_context(active_file)
            dataset_context = _latest_dataset_context(active_file, profile_data)
            quality_insights = QualityInsightService.decorate(
                AIService.generate_display_insights(df, summary),
                profile_data,
                CleaningDecisionService.active_for_file(active_file.id),
            )

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
                'insights': quality_insights,
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
        dataset_context=dataset_context if analysis else None,
        context_record=active_file,
        context_form=CleaningActionForm(),
        ai_configured=AIProviderFactory.is_configured(current_app.config),
        metric_layout=metric_layout if analysis else [],
        metric_cards=metric_cards if analysis else [],
    )
