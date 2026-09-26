import pandas as pd
import pytest

from app.services.cleaning_executor import (
    CleaningExecutor,
    CleaningPlanError,
    select_configured_operations,
    select_executable_operations,
)
from app.services.dataset_pipeline import DatasetPipeline


def _dataset(tmp_path):
    dataframe = pd.DataFrame(
        {
            'email': [
                'ana@example.com',
                'invalid-email',
                'leo@example.org',
                'leo@example.org',
            ],
            'amount': ['10', '20', '30', '30'],
            'active': ['yes', 'no', 'yes', 'yes'],
        }
    )
    source = tmp_path / 'source.csv'
    dataframe.to_csv(source, index=False)
    pipeline = DatasetPipeline(tmp_path / 'artifacts')
    artifact = pipeline.ingest_dataframe(dataframe, 'dataset.csv', source)
    return pipeline, artifact, source


def test_executor_previews_changes_quarantine_and_duplicates(tmp_path):
    _, artifact, _ = _dataset(tmp_path)
    selected = select_executable_operations(
        artifact.profile['cleaning_plan'],
        [
            'email:validate_email',
            'amount:cast_type',
            'active:normalize_boolean',
            'dataset:remove_exact_duplicates',
        ],
    )

    preview = CleaningExecutor().preview(artifact.parquet_path, selected)

    assert preview.metrics == {
        'before_rows': 4,
        'after_rows': 2,
        'changed_rows': 3,
        'quarantined_rows': 1,
        'duplicates_removed': 1,
    }
    assert preview.quarantine[0]['email'] == 'invalid-email'
    assert 'email:validate_email' in preview.quarantine[0]['__quarantine_reason']
    assert sorted(row['amount'] for row in preview.after) == [10, 30]
    assert all(row['active'] is True for row in preview.after)


def test_executor_materializes_version_and_quarantine(tmp_path):
    pipeline, artifact, source = _dataset(tmp_path)
    selected = select_executable_operations(
        artifact.profile['cleaning_plan'],
        ['email:validate_email', 'amount:cast_type'],
    )
    version_key = 'dataset.v1.csv'
    output, _ = pipeline.paths_for(version_key)
    quarantine = pipeline.quarantine_path_for(version_key)

    metrics = CleaningExecutor().apply(
        artifact.parquet_path,
        selected,
        output,
        quarantine,
    )
    version_artifact = pipeline.profile_existing(version_key, source)

    assert metrics['quarantined_rows'] == 1
    assert output.exists()
    assert quarantine.exists()
    assert version_artifact.profile['row_count'] == 3
    restored = pipeline.load_dataframe(version_key)
    assert restored['amount'].tolist() == [10, 30, 30]

    pipeline.remove_artifacts(version_key)
    assert not output.exists()
    assert not quarantine.exists()


def test_executor_rejects_unknown_or_non_executable_operations(tmp_path):
    _, artifact, _ = _dataset(tmp_path)
    plan = artifact.profile['cleaning_plan']

    with pytest.raises(CleaningPlanError, match='desconocidas'):
        select_executable_operations(plan, ['unknown'])

    with pytest.raises(CleaningPlanError, match='análisis adicional'):
        select_executable_operations(plan, ['email:review_invalid_values'])


def test_executor_can_quarantine_rows_with_missing_values(tmp_path):
    dataframe = pd.DataFrame({'category': ['A', None, 'B'], 'amount': [10, 20, 30]})
    source = tmp_path / 'missing.csv'
    dataframe.to_csv(source, index=False)
    artifact = DatasetPipeline(tmp_path / 'artifacts').ingest_dataframe(
        dataframe,
        'missing.csv',
        source,
    )
    selected = select_executable_operations(
        artifact.profile['cleaning_plan'],
        ['category:handle_missing'],
    )

    preview = CleaningExecutor().preview(artifact.parquet_path, selected)

    assert preview.metrics['after_rows'] == 2
    assert preview.metrics['quarantined_rows'] == 1
    assert preview.quarantine[0]['amount'] == 20


def test_executor_applies_user_configured_regional_and_text_rules(tmp_path):
    dataframe = pd.DataFrame(
        {
            'price': ['10,50', '20,75'],
            'date': ['31/12/2025', '15/01/2026'],
            'city': ['Bogota', 'BOGOTA'],
            'phone': ['+57 300 123 4567', '(601) 555-1234'],
        }
    )
    source = tmp_path / 'regional.csv'
    dataframe.to_csv(source, index=False)
    artifact = DatasetPipeline(tmp_path / 'artifacts').ingest_dataframe(
        dataframe,
        'regional.csv',
        source,
    )
    selected_ids = [
        'price:cast_type',
        'date:parse_date',
        'city:normalize_case',
        'phone:normalize_phone',
    ]

    selected = select_configured_operations(
        artifact.profile['cleaning_plan'],
        selected_ids,
        {
            'price:cast_type': {'decimal_separator': ','},
            'date:parse_date': {'date_format': '%d/%m/%Y'},
            'city:normalize_case': {'case_style': 'lower'},
            'phone:normalize_phone': {'phone_style': 'keep_plus'},
        },
    )
    preview = CleaningExecutor().preview(artifact.parquet_path, selected)

    assert preview.metrics['after_rows'] == 2
    assert preview.metrics['quarantined_rows'] == 0
    assert [row['price'] for row in preview.after] == [10.5, 20.75]
    assert [row['date'][:10] for row in preview.after] == [
        '2025-12-31',
        '2026-01-15',
    ]
    assert [row['city'] for row in preview.after] == ['bogota', 'bogota']
    assert [row['phone'] for row in preview.after] == [
        '+573001234567',
        '6015551234',
    ]


def test_executor_rejects_missing_user_configuration(tmp_path):
    dataframe = pd.DataFrame({'price': ['10,50', '20,75']})
    source = tmp_path / 'regional.csv'
    dataframe.to_csv(source, index=False)
    artifact = DatasetPipeline(tmp_path / 'artifacts').ingest_dataframe(
        dataframe,
        'regional.csv',
        source,
    )

    with pytest.raises(CleaningPlanError, match='decimales usan punto o coma'):
        select_configured_operations(
            artifact.profile['cleaning_plan'],
            ['price:cast_type'],
        )


def test_date_cleaning_preserves_optional_time_and_fractional_seconds(tmp_path):
    dataframe = pd.DataFrame({
        'fecha_evento': [
            '2026-08-01 10:15:03.125',
            '2026-08-01 11:20:59',
        ]
    })
    source = tmp_path / 'dates.csv'
    dataframe.to_csv(source, index=False)
    artifact = DatasetPipeline(tmp_path / 'artifacts').ingest_dataframe(
        dataframe,
        'dates.csv',
        source,
    )
    operation = next(
        operation
        for operation in artifact.profile['cleaning_plan']['operations']
        if operation['id'] == 'fecha_evento:parse_date'
    )

    assert operation['decision'] == 'automatic'
    assert operation['parameters']['date_format'] == '%Y-%m-%d'
    selected = select_executable_operations(
        artifact.profile['cleaning_plan'],
        ['fecha_evento:parse_date'],
    )
    preview = CleaningExecutor().preview(artifact.parquet_path, selected)

    assert preview.metrics['quarantined_rows'] == 0
    assert preview.after[0]['fecha_evento'].startswith('2026-08-01T10:15:03.125')
    assert preview.after[1]['fecha_evento'].startswith('2026-08-01T11:20:59')
