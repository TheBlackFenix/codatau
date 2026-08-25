import hashlib

import pandas as pd
import pytest

from app.services.dataset_pipeline import DatasetPipeline


def test_pipeline_creates_parquet_and_compact_profile(tmp_path):
    source_path = tmp_path / 'original.csv'
    source_bytes = b'category,amount\nA,10\nA,10\n,20\nB,30\n'
    source_path.write_bytes(source_bytes)
    dataframe = pd.DataFrame(
        {
            'category': ['A', 'A', None, 'B'],
            'amount': [10, 10, 20, 30],
        }
    )
    pipeline = DatasetPipeline(tmp_path / 'artifacts', sample_size=3)

    artifact = pipeline.ingest_dataframe(dataframe, 'dataset.csv', source_path)

    assert artifact.parquet_path.exists()
    assert artifact.profile_path.exists()
    assert artifact.profile['profile_version'] == '1.1'
    assert artifact.profile['source_sha256'] == hashlib.sha256(source_bytes).hexdigest()
    assert artifact.profile['row_count'] == 4
    assert artifact.profile['column_count'] == 2
    assert artifact.profile['null_count'] == 1
    assert artifact.profile['duplicate_rows'] == 1
    assert len(artifact.profile['sample']) == 3

    columns = {column['name']: column for column in artifact.profile['columns']}
    assert columns['category']['null_count'] == 1
    assert columns['category']['semantic']['type'] == 'text'
    assert columns['amount']['numeric']['mean'] == 17.5
    assert columns['amount']['semantic']['type'] == 'number'
    assert artifact.profile['cleaning_plan']['status'] == 'proposed'
    duplicate_operation = next(
        operation
        for operation in artifact.profile['cleaning_plan']['operations']
        if operation['operation'] == 'remove_exact_duplicates'
    )
    assert duplicate_operation['decision'] == 'user_review'

    restored = pipeline.load_dataframe('dataset.csv')
    assert restored['amount'].tolist() == [10, 10, 20, 30]
    assert pipeline.load_profile('dataset.csv') == artifact.profile


def test_pipeline_removes_all_artifacts(tmp_path):
    source_path = tmp_path / 'original.csv'
    source_path.write_bytes(b'value\n1\n')
    pipeline = DatasetPipeline(tmp_path / 'artifacts')
    artifact = pipeline.ingest_dataframe(
        pd.DataFrame({'value': [1]}),
        'dataset.csv',
        source_path,
    )

    pipeline.remove_artifacts('dataset.csv')

    assert not artifact.parquet_path.exists()
    assert not artifact.profile_path.exists()


def test_pipeline_rejects_empty_dataframes(tmp_path):
    pipeline = DatasetPipeline(tmp_path / 'artifacts')
    source_path = tmp_path / 'empty.csv'
    source_path.write_bytes(b'')

    with pytest.raises(ValueError, match='No hay datos utilizables'):
        pipeline.ingest_dataframe(pd.DataFrame(), 'empty.csv', source_path)
