from unittest.mock import patch

import pandas as pd
import pytest

from app.services.ai_service import AIService
from app.services.data_service import DataService, FileReadError
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


def test_summary_distinguishes_measures_from_numeric_identifiers():
    frame = pd.DataFrame(
        {
            'Numero_de_Guia': [7001, 7002, 7003],
            'IdAdmMensajeria': [101, 102, 103],
            'Telefono_del_remitente': [3001112233, 3001112244, None],
            'Peso': [1.5, 2.0, None],
            'Valor_Total': [10000, 25000, 5000],
            'Piezas': [1, 2, 1],
            'EstadoNumerico': [1, 2, 1],
        }
    )

    summary = DataService.get_summary(frame)

    assert summary['identifier_cols'] == [
        'Numero_de_Guia',
        'IdAdmMensajeria',
        'Telefono_del_remitente',
    ]
    assert summary['recommended_metrics'] == ['Peso', 'Valor_Total', 'Piezas']
    assert summary['optional_numeric_cols'] == ['EstadoNumerico']
    assert summary['default_metrics'] == ['Peso', 'Valor_Total', 'Piezas']
    assert summary['numeric_summary']['Peso']['completeness'] == 66.7
    assert summary['numeric_summary']['Numero_de_Guia']['unique_count'] == 3
    assert summary['numeric_summary']['Numero_de_Guia']['unique_percentage'] == 100.0


def test_ai_service_generates_rule_based_insights():
    frame = pd.DataFrame({'amount': [1, 1, 1, 1, 1, 100, None]})
    summary = DataService.get_summary(frame)

    insights = AIService.generate_insights(frame, summary)
    messages = [insight['message'] for insight in insights]

    assert any('valores nulos' in message for message in messages)
    assert any('valores extremos' in message for message in messages)
    assert any('muy pocas filas' in message for message in messages)


def test_ai_service_does_not_flag_identifier_values_as_outliers():
    frame = pd.DataFrame(
        {
            'IdServicio': [1, 1, 1, 1, 1, 100],
            'Valor_Total': [10, 10, 10, 10, 10, 1000],
        }
    )

    insights = AIService.generate_insights(frame, DataService.get_summary(frame))
    messages = [insight['message'] for insight in insights]

    assert not any('IdServicio' in message and 'extremos' in message for message in messages)
    assert any('Valor_Total' in message and 'extremos' in message for message in messages)
    display_insights = AIService.generate_display_insights(
        frame,
        DataService.get_summary(frame),
    )
    assert all('insight_type' in insight for insight in display_insights)


def test_xls_extension_uses_excel_reader(tmp_path):
    filepath = tmp_path / 'sample.xls'
    filepath.write_bytes(b'placeholder')
    expected = pd.DataFrame({'value': [1]})

    with patch('app.services.data_service.pd.read_excel', return_value=expected) as reader:
        result = DataService.read_file(str(filepath))

    reader.assert_called_once_with(str(filepath))
    assert result.equals(expected)


def test_csv_content_with_excel_signature_reports_extension_mismatch(tmp_path):
    filepath = tmp_path / 'renamed.csv'
    filepath.write_bytes(b'PK\x03\x04placeholder')

    with pytest.raises(FileReadError) as error:
        DataService.read_file(filepath)

    assert error.value.code == 'extension_mismatch'
    assert '.xlsx' in error.value.hint
