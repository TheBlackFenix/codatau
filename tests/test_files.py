from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.extensions import db
from app.models.file_upload import FileUpload
from app.models.dataset_version import DatasetVersion
from app.models.cleaning_decision import CleaningDecision
from app.models.dashboard_configuration import DashboardConfiguration
from app.models.ai_analysis_run import AIAnalysisRun
from app.services.ai_providers import AIProviderResult


def _login(auth):
    auth.register()
    auth.login()


def _csv_bytes():
    return b'category,amount\nA,10\nB,20\nA,30\n'


def _cleaning_csv_bytes():
    return (
        b'email,amount\n'
        b'ana@example.com,10\n'
        b'invalid-email,20\n'
        b'leo@example.org,30\n'
        b'leo@example.org,30\n'
    )


def _configurable_cleaning_csv_bytes():
    return (
        'price,date,city,phone\n'
        '"10,50",31/12/2025,Bogota,+57 300 123 4567\n'
        '"20,75",15/01/2026,BOGOTA,(601) 555-1234\n'
    ).encode()


class _RouteAIProvider:
    def __init__(self):
        self.calls = 0

    def generate_json(self, _instructions, _payload, _schema):
        self.calls += 1
        return AIProviderResult(
            data={
                'suggestions': [{
                    'operation_id': 'email:review_invalid_values',
                    'recommendation': 'user_review',
                    'confidence': 0.93,
                    'rationale': 'El valor no permite reconstruir el correo con certeza.',
                    'parameters': [],
                }]
            },
            input_tokens=50,
            output_tokens=15,
        )


def test_csv_flow_from_upload_to_download(app, client, auth):
    _login(auth)

    upload = client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    assert upload.status_code == 302
    assert upload.headers['Location'].endswith('/files/results/1')
    with client.session_transaction() as browser_session:
        assert browser_session['active_file_id'] == 1
    results = client.get('/files/results/1')
    dashboard = client.get('/dashboard')
    insights = client.get('/files/insights')
    assert results.status_code == 200
    assert dashboard.status_code == 200
    assert insights.status_code == 200
    assert b'M\xc3\xa9tricas de negocio' in results.data
    assert b'M\xc3\xa9tricas de negocio' in dashboard.data
    assert b'Agregar m\xc3\xa9trica' in insights.data
    assert b'Columnas num\xc3\xa9ricas:' not in insights.data
    assert client.get('/reports/').status_code == 200
    assert client.get('/files/cleaning').headers['Location'].endswith('/files/cleaning/1')

    download = client.get('/reports/download/1')
    assert download.status_code == 200
    assert download.mimetype == 'text/csv'
    assert b'category,amount' in download.data

    profile = client.get('/files/profile/1')
    assert profile.status_code == 200
    assert profile.json['row_count'] == 3
    assert profile.json['column_count'] == 2
    assert len(profile.json['source_sha256']) == 64
    assert profile.json['profile_version'] == '1.3'
    assert profile.json['cleaning_plan']['status'] == 'proposed'

    with app.app_context():
        record = FileUpload.query.one()
        assert record.row_count == 3
        assert record.column_count == 2


def test_cleaning_navigation_without_files_explains_next_step(client, auth):
    _login(auth)

    response = client.get('/files/cleaning', follow_redirects=True)

    assert response.status_code == 200
    assert 'Carga un archivo antes de iniciar una limpieza'.encode() in response.data


def test_ai_cleaning_analysis_is_advisory_visible_and_cached(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )
    app.config.update(
        AI_PROVIDER='openai_compatible',
        AI_MODEL='test-model',
        AI_BASE_URL='http://provider.test/v1',
        AI_API_KEY='test-key',
    )
    provider = _RouteAIProvider()

    with patch(
        'app.services.ai_cleaning_service.AIProviderFactory.create',
        return_value=provider,
    ):
        first = client.post(
            '/files/cleaning/1/ai-analysis',
            follow_redirects=True,
        )
        second = client.post(
            '/files/cleaning/1/ai-analysis',
            follow_redirects=True,
        )

    assert first.status_code == 200
    assert 'Recomendación IA'.encode() in first.data
    assert 'Revisión humana'.encode() in first.data
    assert b'93% confianza' in first.data
    assert 'ningún dato se modifica automáticamente'.encode() in first.data
    assert 'reutilizó el análisis IA existente'.encode() in second.data
    assert provider.calls == 1
    with app.app_context():
        assert AIAnalysisRun.query.count() == 1
        assert DatasetVersion.query.count() == 0
        assert CleaningDecision.query.count() == 0


def test_ai_cleaning_analysis_disabled_has_safe_message(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )

    response = client.post(
        '/files/cleaning/1/ai-analysis',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'integración con IA todavía no está configurada'.encode() in response.data
    with app.app_context():
        assert AIAnalysisRun.query.count() == 0


def test_upload_page_supports_real_drag_and_drop(client, auth):
    _login(auth)

    response = client.get('/files/upload')

    assert response.status_code == 200
    assert b'id="uploadZone"' in response.data
    assert b"addEventListener('dragover'" in response.data
    assert b"addEventListener('drop'" in response.data
    assert b'new DataTransfer()' in response.data
    assert b'id="selectedFileName"' in response.data


def test_metric_explorer_excludes_ids_but_allows_manual_selection(client, auth):
    _login(auth)
    source = (
        b'Numero_de_Guia,IdServicio,Peso,Valor_Total,EstadoNumerico\n'
        b'7001,1,2.5,10000,1\n'
        b'7002,2,3.0,20000,2\n'
    )
    client.post(
        '/files/upload',
        data={'file': (BytesIO(source), 'envios.csv')},
        content_type='multipart/form-data',
    )

    results = client.get('/files/results/1')
    insights = client.get('/files/insights')

    assert results.status_code == 200
    assert insights.status_code == 200
    assert b'<div class="metric-card-label">Peso</div>' in results.data
    assert b'<div class="metric-card-label">Valor_Total</div>' in results.data
    assert b'<div class="metric-card-label">Numero_de_Guia</div>' not in results.data
    assert b'<option value="Numero_de_Guia" data-role="identifier">' in results.data
    assert b'Valores \xc3\xbanicos / total de filas' in results.data
    assert b'2</strong><span>Identificadores excluidos</span>' in insights.data


def test_metric_layout_add_remove_and_order_are_persistent(app, client, auth):
    _login(auth)
    source = (
        b'Fecha,Numero_de_Guia,Peso,Valor_Total\n'
        b'2026-08-01 10:15:03,7001,2.5,10000\n'
        b'2026-08-01 11:47:59,7002,3.0,20000\n'
    )
    client.post(
        '/files/upload',
        data={'file': (BytesIO(source), 'envios.csv')},
        content_type='multipart/form-data',
    )

    layout = [
        {'column': 'Valor_Total', 'aggregation': 'mean'},
        {'column': 'Numero_de_Guia', 'aggregation': 'unique_count'},
        {'column': 'Peso', 'aggregation': 'sum'},
    ]
    saved = client.post('/dashboard/files/1/metrics', json={'metrics': layout})

    assert saved.status_code == 200
    assert saved.json['metrics'] == layout
    with app.app_context():
        assert DashboardConfiguration.query.one().metrics == layout

    for path in ('/dashboard', '/files/results/1', '/files/insights'):
        page = client.get(path)
        assert page.status_code == 200
        html = page.data.decode()
        assert html.index('data-card-column="Valor_Total"') < html.index(
            'data-card-column="Numero_de_Guia"'
        ) < html.index('data-card-column="Peso"')
        assert 'draggable="true"' in html

    reduced = [layout[1]]
    assert client.post(
        '/dashboard/files/1/metrics',
        json={'metrics': reduced},
    ).status_code == 200
    refreshed = client.get('/dashboard').data.decode()
    assert 'data-card-column="Numero_de_Guia"' in refreshed
    assert 'data-card-column="Valor_Total"' not in refreshed
    assert 'data-card-column="Peso"' not in refreshed

    assert client.post(
        '/dashboard/files/1/metrics',
        json={'metrics': []},
    ).status_code == 200
    empty = client.get('/dashboard').data
    assert b'No hay m\xc3\xa9tricas visibles' in empty
    assert b'data-card-column=' not in empty


def test_user_cannot_change_another_users_dashboard(client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )
    auth.logout()
    auth.register(username='other', email='other@example.com')
    auth.login(email='other@example.com')

    response = client.post('/dashboard/files/1/metrics', json={'metrics': []})

    assert response.status_code == 404


def test_xlsx_upload(app, client, auth):
    _login(auth)
    workbook = BytesIO()
    pd.DataFrame({'category': ['A', 'B'], 'amount': [1, 2]}).to_excel(
        workbook,
        index=False,
    )
    workbook.seek(0)

    response = client.post(
        '/files/upload',
        data={'file': (workbook, 'sales.xlsx')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 302
    with app.app_context():
        assert FileUpload.query.one().file_type == 'xlsx'


def test_semicolon_csv_upload_is_detected(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'category;amount\nA;10\nB;20\n'), 'sales.csv')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 302
    with app.app_context():
        record = FileUpload.query.one()
        assert record.row_count == 2
        assert record.column_count == 2


def test_invalid_excel_explains_how_to_fix_it(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'not an excel workbook'), 'broken.xlsx')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'no corresponde a un archivo Excel moderno'.encode() in response.data
    assert 'Guardar como'.encode() in response.data


def test_malformed_csv_explains_the_structure_problem(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'name,amount\n"unterminated,10\n'), 'broken.csv')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'comillas sin cerrar'.encode() in response.data
    assert 'CSV UTF-8'.encode() in response.data


def test_unexpected_processing_error_reports_stage_and_reference(app, client, auth):
    _login(auth)

    with patch(
        'app.services.dataset_pipeline.DatasetPipeline.ingest_dataframe',
        side_effect=RuntimeError('internal detail'),
    ):
        response = client.post(
            '/files/upload',
            data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert 'creación del perfil analítico'.encode() in response.data
    assert 'Referencia:'.encode() in response.data
    assert b'internal detail' not in response.data


def test_preview_failure_returns_to_plan_without_server_error(client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )

    with patch(
        'app.services.cleaning_executor.CleaningExecutor.preview',
        side_effect=RuntimeError('duckdb detail'),
    ):
        response = client.post(
            '/files/cleaning/1/preview',
            data={'operation_ids': ['email:validate_email']},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b'El archivo no fue modificado' in response.data
    assert b'Referencia:' in response.data
    assert b'duckdb detail' not in response.data


def test_download_uses_parquet_when_original_is_unavailable(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    with app.app_context():
        record = FileUpload.query.one()
        Path(app.config['UPLOAD_FOLDER'], record.filename).unlink()

    response = client.get('/reports/download/1')

    assert response.status_code == 200
    assert b'category,amount' in response.data


def test_missing_dataset_artifacts_do_not_break_navigation(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    with app.app_context():
        record = FileUpload.query.one()
        Path(app.config['UPLOAD_FOLDER'], record.filename).unlink()
        for artifact in Path(app.config['ANALYTICS_FOLDER']).iterdir():
            artifact.unlink()

    assert client.get('/dashboard').status_code == 200
    assert client.get('/files/insights').status_code == 200
    assert client.get('/reports/').status_code == 200
    assert client.get('/files/results/1').status_code == 302
    assert client.get('/reports/download/1').status_code == 302
    assert client.get('/files/cleaning/1').status_code == 404


def test_empty_csv_is_rejected(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'category,amount\n'), 'empty.csv')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'El archivo está vacío'.encode() in response.data
    with app.app_context():
        assert FileUpload.query.count() == 0


def test_upload_respects_configured_size_limit(app, client, auth):
    _login(auth)
    app.config['MAX_CONTENT_LENGTH'] = 128

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'x' * 1024), 'large.csv')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/files/upload')


def test_user_cannot_access_another_users_file(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )
    auth.logout()
    auth.register(username='other', email='other@example.com')
    auth.login(email='other@example.com')

    assert client.get('/files/results/1').status_code == 404
    assert client.get('/reports/download/1').status_code == 404
    assert client.get('/files/profile/1').status_code == 404
    assert client.get('/files/cleaning/1').status_code == 404


def test_user_cannot_reopen_another_users_cleaning_decision(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )
    client.post(
        '/files/cleaning/1/apply',
        data={'decision:email:validate_email': 'keep'},
    )
    with app.app_context():
        decision_id = CleaningDecision.query.one().id

    auth.logout()
    auth.register(username='other', email='other@example.com')
    auth.login(email='other@example.com')

    response = client.post(
        f'/files/cleaning/1/decisions/{decision_id}/reopen',
    )

    assert response.status_code == 404
    with app.app_context():
        assert CleaningDecision.query.one().is_active is True


def test_cleaning_preview_apply_and_revert_version(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )

    plan_page = client.get('/files/cleaning/1')
    assert plan_page.status_code == 200
    assert b'email:validate_email' in plan_page.data
    assert b'dataset:remove_exact_duplicates' in plan_page.data

    dashboard = client.get('/dashboard')
    insights = client.get('/files/insights')
    files_page = client.get('/files/upload')
    assert b'/files/cleaning/1' in dashboard.data
    assert b'Revisar y limpiar datos' in insights.data
    assert b'title="Limpiar datos"' in files_page.data

    selected = [
        'email:validate_email',
        'dataset:remove_exact_duplicates',
    ]
    preview = client.post(
        '/files/cleaning/1/preview',
        data={'operation_ids': selected},
    )
    assert preview.status_code == 200
    assert b'Vista previa de limpieza' in preview.data
    assert b'invalid-email' in preview.data

    applied = client.post(
        '/files/cleaning/1/apply',
        data={'operation_ids': selected},
        follow_redirects=True,
    )
    assert applied.status_code == 200
    assert 'Versi\u00f3n 1 creada'.encode() in applied.data

    with app.app_context():
        record = FileUpload.query.one()
        version = DatasetVersion.query.one()
        assert record.active_version.id == version.id
        assert record.row_count == 2
        assert version.metrics['quarantined_rows'] == 1
        assert version.metrics['duplicates_removed'] == 1
        assert Path(
            app.config['ANALYTICS_FOLDER'],
            version.quarantine_filename,
        ).exists()
        version_id = version.id

    quarantine_download = client.get(f'/files/cleaning/1/quarantine/{version_id}')
    assert quarantine_download.status_code == 200
    assert b'invalid-email' in quarantine_download.data
    assert b'__quarantine_reason' in quarantine_download.data

    cleaned_download = client.get('/reports/download/1')
    assert cleaned_download.data.count(b'leo@example.org') == 1
    assert b'invalid-email' not in cleaned_download.data

    reverted = client.post(
        '/files/cleaning/1/activate/0',
        follow_redirects=True,
    )
    assert reverted.status_code == 200
    assert 'versi\u00f3n original procesada'.encode() in reverted.data
    baseline_download = client.get('/reports/download/1')
    assert baseline_download.data.count(b'leo@example.org') == 2
    assert b'invalid-email' in baseline_download.data

    client.post(f'/files/cleaning/1/activate/{version_id}')
    with app.app_context():
        assert FileUpload.query.one().active_version.version_number == 1
        analytics_folder = Path(app.config['ANALYTICS_FOLDER'])

    client.post('/files/delete/1')
    with app.app_context():
        assert DatasetVersion.query.count() == 0
    assert list(analytics_folder.iterdir()) == []


def test_user_can_configure_preview_and_apply_review_rules(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={
            'file': (
                BytesIO(_configurable_cleaning_csv_bytes()),
                'regional.csv',
            )
        },
        content_type='multipart/form-data',
    )

    plan_page = client.get('/files/cleaning/1')
    assert plan_page.status_code == 200
    assert 'Requieren configuración'.encode() in plan_page.data
    assert b'parameter:price:cast_type:decimal_separator' in plan_page.data
    assert b'parameter:date:parse_date:date_format' in plan_page.data
    assert b'parameter:city:normalize_case:case_style' in plan_page.data
    assert b'parameter:phone:normalize_phone:phone_style' in plan_page.data

    selected = [
        'price:cast_type',
        'date:parse_date',
        'city:normalize_case',
        'phone:normalize_phone',
    ]
    configured = {
        'operation_ids': selected,
        'parameter:price:cast_type:decimal_separator': ',',
        'parameter:date:parse_date:date_format': '%d/%m/%Y',
        'parameter:city:normalize_case:case_style': 'lower',
        'parameter:phone:normalize_phone:phone_style': 'keep_plus',
    }
    preview = client.post('/files/cleaning/1/preview', data=configured)
    assert preview.status_code == 200
    assert b'Vista previa de limpieza' in preview.data
    assert b'parameter:price:cast_type:decimal_separator' in preview.data

    applied = client.post(
        '/files/cleaning/1/apply',
        data=configured,
        follow_redirects=True,
    )
    assert applied.status_code == 200
    assert 'Versión 1 creada'.encode() in applied.data
    assert b'Historial de versiones' in applied.data

    download = client.get('/reports/download/1')
    assert b'10.5,2025-12-31,bogota,+573001234567' in download.data


def test_ai_flagged_date_can_be_configured_manually(app, client, auth):
    _login(auth)
    source = (
        b'fecha,amount\n'
        b'2026-08-01,10\n'
        b'valor-invalido,20\n'
        b'2026-08-03,30\n'
    )
    client.post(
        '/files/upload',
        data={'file': (BytesIO(source), 'fechas.csv')},
        content_type='multipart/form-data',
    )

    plan = client.get('/files/cleaning/1')

    assert plan.status_code == 200
    assert b'Configuraci\xc3\xb3n manual disponible' in plan.data
    assert b'name="decision:fecha:parse_date" value="apply"' in plan.data
    assert b'name="parameter:fecha:parse_date:date_format"' in plan.data
    configuration = plan.data.split(b'data-operation-configuration', 1)[1].split(b'>', 1)[0]
    assert b'hidden' not in configuration

    applied = client.post(
        '/files/cleaning/1/apply',
        data={
            'decision:fecha:parse_date': 'apply',
            'parameter:fecha:parse_date:date_format': '%Y-%m-%d',
        },
        follow_redirects=True,
    )

    assert applied.status_code == 200
    assert 'Versión 1 creada'.encode() in applied.data
    with app.app_context():
        assert DatasetVersion.query.one().metrics['quarantined_rows'] == 1
    download = client.get('/reports/download/1')
    assert b'valor-invalido' not in download.data


def test_user_can_keep_data_and_resolve_suggestion_without_new_version(
    app,
    client,
    auth,
):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )

    plan = client.get('/files/cleaning/1')
    assert b'name="decision:email:validate_email"' in plan.data
    assert b'S\xc3\xad, aplicar' in plan.data
    assert b'No, conservar' in plan.data

    selection = {'decision:email:validate_email': 'keep'}
    preview = client.post('/files/cleaning/1/preview', data=selection)
    assert preview.status_code == 200
    assert b'No, conservar' in preview.data
    assert b'Guardar decisiones' in preview.data

    saved = client.post(
        '/files/cleaning/1/apply',
        data=selection,
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert b'Los datos se conservaron sin cambios' in saved.data
    assert b'name="decision:email:validate_email"' not in saved.data
    assert b'Decisiones resueltas' in saved.data

    with app.app_context():
        decision = CleaningDecision.query.one()
        assert decision.choice == 'keep'
        assert decision.is_active is True
        assert decision.applied_version_number is None
        assert DatasetVersion.query.count() == 0
        decision_id = decision.id

    reopened = client.post(
        f'/files/cleaning/1/decisions/{decision_id}/reopen',
        follow_redirects=True,
    )
    assert reopened.status_code == 200
    assert b'name="decision:email:validate_email"' in reopened.data
    with app.app_context():
        assert CleaningDecision.query.one().is_active is False

    reapplied = client.post(
        '/files/cleaning/1/apply',
        data={'decision:email:validate_email': 'apply'},
        follow_redirects=True,
    )
    assert reapplied.status_code == 200
    with app.app_context():
        decision = CleaningDecision.query.one()
        assert decision.is_active is True
        assert decision.choice == 'apply'
        assert decision.applied_version_number == 1
        assert DatasetVersion.query.count() == 1


def test_mixed_yes_no_decisions_apply_only_selected_rules(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )

    decisions = {
        'decision:email:validate_email': 'apply',
        'decision:dataset:remove_exact_duplicates': 'keep',
    }
    preview = client.post('/files/cleaning/1/preview', data=decisions)
    assert preview.status_code == 200
    assert b'S\xc3\xad, aplicar' in preview.data
    assert b'No, conservar' in preview.data

    applied = client.post(
        '/files/cleaning/1/apply',
        data=decisions,
        follow_redirects=True,
    )
    assert applied.status_code == 200
    assert b'2 decisi\xc3\xb3n(es) resuelta(s)' in applied.data
    assert b'name="decision:email:validate_email"' not in applied.data
    assert b'name="decision:dataset:remove_exact_duplicates"' not in applied.data

    with app.app_context():
        stored = {
            decision.operation_id: decision
            for decision in CleaningDecision.query.all()
        }
        assert stored['email:validate_email'].choice == 'apply'
        assert stored['email:validate_email'].applied_version_number == 1
        assert stored['dataset:remove_exact_duplicates'].choice == 'keep'
        assert stored['dataset:remove_exact_duplicates'].applied_version_number is None
        assert DatasetVersion.query.one().metrics['duplicates_removed'] == 0

    download = client.get('/reports/download/1')
    assert download.data.count(b'leo@example.org') == 2
    assert b'invalid-email' not in download.data


def test_existing_version_operations_are_backfilled_as_resolved(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )
    client.post(
        '/files/cleaning/1/apply',
        data={'operation_ids': ['email:validate_email']},
    )

    with app.app_context():
        CleaningDecision.query.delete()
        db.session.commit()

    plan = client.get('/files/cleaning/1')

    assert plan.status_code == 200
    assert b'name="decision:email:validate_email"' not in plan.data
    with app.app_context():
        decision = CleaningDecision.query.one()
        assert decision.choice == 'apply'
        assert decision.applied_version_number == 1


def test_delete_removes_database_record_and_file(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    with app.app_context():
        path = app.config['UPLOAD_FOLDER'] + '/' + FileUpload.query.one().filename
        analytics_folder = app.config['ANALYTICS_FOLDER']

    response = client.post('/files/delete/1')

    assert response.status_code == 302
    with app.app_context():
        assert FileUpload.query.count() == 0
        assert CleaningDecision.query.count() == 0
        assert DashboardConfiguration.query.count() == 0
    assert not Path(path).exists()
    assert list(Path(analytics_folder).iterdir()) == []
