from unittest.mock import patch

import pandas as pd

from app.services.ai_service import AIService
from app.services.data_service import DataService
from app.services.validation_service import ValidationService


def test_clean_dataframe_preserves_non_string_values():
    source = pd.DataFrame(
        {
            'mixed': ['  texto  ', 7, None],
            'empty': [None, None, None],
            'number': [1, 2, 3],
        }
    )

    cleaned = DataService.clean_dataframe(source)

    assert cleaned['mixed'].tolist()[0:2] == ['texto', 7]
    assert 'empty' not in cleaned.columns
    assert source.loc[0, 'mixed'] == '  texto  '


def test_summary_and_validation_report_quality_issues():
    frame = pd.DataFrame({'category': ['A', 'A', None], 'amount': [10, 10, 30]})

    errors, warnings = ValidationService.validate_file(frame)
    summary = DataService.get_summary(frame)

    assert errors == []
    assert any('duplicada' in warning for warning in warnings)
    assert any('nulo' in warning for warning in warnings)
    assert summary['rows'] == 3
    assert summary['columns'] == 2
    assert summary['null_total'] == 1
    assert summary['duplicates'] == 1
    assert summary['numeric_summary']['amount']['mean'] == 16.67


def test_ai_service_generates_rule_based_insights():
    frame = pd.DataFrame({'amount': [1, 1, 1, 1, 1, 100, None]})
    summary = DataService.get_summary(frame)

    insights = AIService.generate_insights(frame, summary)
    messages = [insight['message'] for insight in insights]

    assert any('valores nulos' in message for message in messages)
    assert any('valores extremos' in message for message in messages)
    assert any('muy pocas filas' in message for message in messages)


def test_xls_extension_uses_excel_reader(tmp_path):
    filepath = tmp_path / 'sample.xls'
    filepath.write_bytes(b'placeholder')
    expected = pd.DataFrame({'value': [1]})

    with patch('app.services.data_service.pd.read_excel', return_value=expected) as reader:
        result = DataService.read_file(str(filepath))

    reader.assert_called_once_with(str(filepath))
    assert result.equals(expected)
