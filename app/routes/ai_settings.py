from flask import Blueprint, abort, current_app, flash, redirect, request, session, url_for
from flask_login import current_user, login_required

from app.forms.file_forms import CleaningActionForm
from app.services.ai_providers import AIProviderFactory
from app.services.google_model_catalog import GoogleModelCatalog

ai_settings_bp = Blueprint('ai_settings', __name__)


def install_ai_settings(app):
    app.extensions['google_model_catalog'] = GoogleModelCatalog()
    app.register_blueprint(ai_settings_bp)

    @app.context_processor
    def model_selector():
        if not current_user.is_authenticated or not app.config.get('AI_MODEL_SELECTOR_ENABLED'):
            return {}
        models, failed = app.extensions['google_model_catalog'].get(app.config)
        selected = AIProviderFactory.configuration_from_app(app.config).model
        if selected and selected not in {item['id'] for item in models}:
            models = [{'id': selected, 'label': selected + ' (configurado)'}, *models]
        return {'ai_models': models, 'ai_selected_model': selected,
                'ai_catalog_failed': failed, 'ai_model_form': CleaningActionForm()}


@ai_settings_bp.route('/settings/ai-model', methods=['POST'])
@login_required
def select_model():
    if not current_app.config.get('AI_MODEL_SELECTOR_ENABLED'):
        abort(404)
    if not CleaningActionForm().validate_on_submit():
        return 'Formulario inválido.', 400
    catalog = current_app.extensions['google_model_catalog']
    if request.form.get('action') == 'refresh':
        _, failed = catalog.get(current_app.config, refresh=True)
        flash('No pudimos actualizar la lista. Puedes seguir usando el modelo actual.' if failed else 'Lista de modelos actualizada.', 'warning' if failed else 'success')
    else:
        models, _ = catalog.get(current_app.config)
        chosen = request.form.get('model')
        if chosen not in {item['id'] for item in models}:
            return 'El modelo elegido no está disponible. Actualiza la lista.', 400
        session['ai_model_choice'] = {'user_id': current_user.id, 'model': chosen}
        flash('Modelo actualizado para tus próximas consultas. Tus datos y análisis guardados no cambian.', 'success')
    # Only local absolute paths, never external redirects supplied by a form.
    target = request.form.get('return_to', '')
    if not target.startswith('/') or target.startswith('//') or '\\' in target or any(ord(c) < 32 for c in target):
        target = url_for('dashboard.index')
    return redirect(target)
