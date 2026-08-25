import pandas as pd
import pytest

from app.services.cleaning_executor import (
    CleaningExecutor,
    CleaningPlanError,
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
