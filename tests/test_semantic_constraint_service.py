import pandas as pd

from app.services.cleaning_executor import (
    CleaningExecutor,
    select_configured_operations,
)
from app.services.dataset_pipeline import DatasetPipeline
from app.services.semantic_constraint_service import SemanticConstraintService


def _artifact(tmp_path):
    dataframe = pd.DataFrame({
        'alto': [-10.0, 20.0, 30.0],
        'peso': [0.0, 4.0, 5.0],
        'estado': ['nuevo', 'nuevo', 'entregado'],
        'referencia': ['A-1', None, 'A-3'],
        'correo': ['ana@example.com', 'correo-invalido', 'leo@example.org'],
    })
    source = tmp_path / 'envios.csv'
    dataframe.to_csv(source, index=False)
    return DatasetPipeline(tmp_path / 'artifacts').ingest_dataframe(
        dataframe,
        'envios.csv',
        source,
    )


def _context():
    return {
        'domain': 'Envíos y mensajería',
        'description': 'Paquetes con dimensiones físicas.',
        'confidence': 0.95,
        'column_roles': [
            {
                'column': 'alto',
                'role': 'dimension',
                'description': 'Altura del paquete.',
                'aggregate': True,
                'suggested_constraints': ['positive'],
            },
            {
                'column': 'peso',
                'role': 'measure',
                'description': 'Peso del paquete.',
                'aggregate': True,
                'suggested_constraints': ['non_negative'],
            },
            {
                'column': 'estado',
                'role': 'status',
                'description': 'Estado operativo.',
                'aggregate': False,
                'suggested_constraints': ['positive'],
            },
            {
                'column': 'referencia',
                'role': 'identifier',
                'description': 'Referencia del envío.',
                'aggregate': False,
                'suggested_constraints': ['not_empty'],
            },
            {
                'column': 'correo',
                'role': 'contact',
                'description': 'Correo de contacto.',
                'aggregate': False,
                'suggested_constraints': ['valid_email'],
            },
        ],
    }


def test_context_constraints_become_review_rules_only_when_violated(tmp_path):
    artifact = _artifact(tmp_path)

    enriched = SemanticConstraintService.enrich_plan(
        artifact.profile,
        _context(),
        artifact.parquet_path,
    )
    operations = {
        operation['id']: operation
        for operation in enriched['cleaning_plan']['operations']
    }

    assert operations['alto:validate_range:positive']['affected_rows'] == 1
    assert operations['alto:validate_range:positive']['decision'] == 'user_review'
    assert operations['alto:validate_range:positive']['examples'] == ['-10.0']
    assert 'peso:validate_range:non_negative' not in operations
    assert 'estado:validate_range:positive' not in operations
    assert 'En el contexto “Envíos y mensajería”' in operations[
        'referencia:handle_missing'
    ]['context_reason']
    assert operations['referencia:handle_missing']['examples'] == ['<vacío>']
    assert operations['correo:validate_email']['examples'] == ['correo-invalido']


def test_semantic_range_rule_can_null_the_cell_and_keep_the_row(tmp_path):
    artifact = _artifact(tmp_path)
    enriched = SemanticConstraintService.enrich_plan(
        artifact.profile,
        _context(),
        artifact.parquet_path,
    )
    selected = select_configured_operations(
        enriched['cleaning_plan'],
        ['alto:validate_range:positive'],
        {
            'alto:validate_range:positive': {
                'invalid_action': 'set_null',
            }
        },
    )

    preview = CleaningExecutor().preview(artifact.parquet_path, selected)

    assert preview.metrics['after_rows'] == 3
    assert preview.metrics['quarantined_rows'] == 0
    assert preview.metrics['changed_rows'] == 1
    assert preview.after[0]['alto'] is None
