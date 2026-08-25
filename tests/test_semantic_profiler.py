import pandas as pd

from app.services.dataset_pipeline import DatasetPipeline
from app.services.semantic_profiler import OPERATION_CATALOG


def _profile(tmp_path, dataframe):
    source = tmp_path / 'source.csv'
    dataframe.to_csv(source, index=False)
    return DatasetPipeline(tmp_path / 'artifacts').ingest_dataframe(
        dataframe,
        'dataset.csv',
        source,
    ).profile


def _columns(profile):
    return {column['name']: column for column in profile['columns']}


def _operations(profile):
    return {
        operation['id']: operation
        for operation in profile['cleaning_plan']['operations']
    }


def test_semantic_profile_distinguishes_numbers_identifiers_and_booleans(tmp_path):
    profile = _profile(
        tmp_path,
        pd.DataFrame(
            {
                'amount': ['10', '20.5', '30'],
                'customer_code': ['001', '002', '003'],
                'active': ['yes', 'no', 'yes'],
            }
        ),
    )
    columns = _columns(profile)
    operations = _operations(profile)

    assert columns['amount']['semantic']['type'] == 'number'
    assert operations['amount:cast_type']['decision'] == 'automatic'
    assert columns['customer_code']['semantic']['type'] == 'identifier'
    assert 'customer_code:cast_type' not in operations
    assert columns['active']['semantic']['type'] == 'boolean'
    assert operations['active:normalize_boolean']['decision'] == 'automatic'


def test_semantic_profile_escalates_invalid_email_and_mixed_number(tmp_path):
    profile = _profile(
        tmp_path,
        pd.DataFrame(
            {
                'email': [
                    'ana@example.com',
                    'correo-invalido',
                    'leo@example.org',
                    'mia@example.net',
                    'sol@example.co',
                ],
                'amount': ['10', '20', 'error', '30', '40'],
            }
        ),
    )
    columns = _columns(profile)
    operations = _operations(profile)

    assert columns['email']['semantic']['type'] == 'email'
    assert columns['email']['semantic']['needs_ai'] is True
    assert operations['email:validate_email']['decision'] == 'automatic'
    assert operations['email:review_invalid_values']['affected_rows'] == 1
    assert operations['email:review_invalid_values']['decision'] == 'ai_analysis'

    assert columns['amount']['semantic']['type'] == 'number'
    assert columns['amount']['semantic']['confidence'] == 0.8
    assert operations['amount:cast_type']['decision'] == 'ai_analysis'
    assert profile['cleaning_plan']['ai_candidate_columns'] == ['amount', 'email']


def test_semantic_profile_requires_review_for_ambiguous_dates_and_categories(tmp_path):
    profile = _profile(
        tmp_path,
        pd.DataFrame(
            {
                'date': ['03/04/2025', '04/05/2025'],
                'city': ['Bogota', 'BOGOTA'],
            }
        ),
    )
    columns = _columns(profile)
    operations = _operations(profile)

    assert columns['date']['semantic']['type'] == 'date'
    assert columns['date']['semantic']['evidence']['ambiguous_date_count'] == 2
    assert operations['date:parse_date']['decision'] == 'ai_analysis'
    assert operations['city:normalize_case']['decision'] == 'user_review'


def test_semantic_profile_auto_parses_iso_dates_but_reviews_decimal_commas(tmp_path):
    profile = _profile(
        tmp_path,
        pd.DataFrame(
            {
                'created_date': ['2025-01-03', '2025-02-04'],
                'price': ['10,50', '20,75'],
            }
        ),
    )
    operations = _operations(profile)

    assert operations['created_date:parse_date']['decision'] == 'automatic'
    assert operations['price:cast_type']['decision'] == 'user_review'
    assert operations['price:cast_type']['parameters']['decimal_separator'] == ','


def test_cleaning_plan_only_contains_catalogued_operations(tmp_path):
    profile = _profile(
        tmp_path,
        pd.DataFrame({'email': [' valid@example.com ', 'invalid']}),
    )
    operations = profile['cleaning_plan']['operations']

    assert profile['cleaning_plan']['operation_catalog_version'] == '1.0'
    assert {operation['operation'] for operation in operations} <= OPERATION_CATALOG
    assert all(operation['decision'] in {
        'automatic', 'user_review', 'ai_analysis'
    } for operation in operations)
