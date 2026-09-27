import hashlib
import json
import math

import duckdb

from app.services.cleaning_executor import CleaningPlanError
from app.services.semantic_profiler import EMAIL_PATTERN, NUMERIC_TYPE_PREFIXES


def _quoted_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class CustomCleaningRuleService:
    """Builds a safe, allow-listed rule selected explicitly by the user."""

    RULES = {'missing', 'email', 'range'}
    ACTIONS = {'quarantine_rows', 'replace_value', 'set_null'}

    @classmethod
    def from_form(cls, profile, form_data, source_parquet):
        if form_data.get('custom_rule:enabled') != '1':
            return None

        column_name = (form_data.get('custom_rule:column') or '').strip()
        rule_type = (form_data.get('custom_rule:type') or '').strip()
        action = (form_data.get('custom_rule:invalid_action') or '').strip()
        replacement = (form_data.get('custom_rule:replacement_value') or '').strip()
        columns = {
            column.get('name'): column
            for column in profile.get('columns') or []
        }
        column = columns.get(column_name)
        if not column:
            raise CleaningPlanError('Selecciona una columna válida para la regla manual.')
        if rule_type not in cls.RULES:
            raise CleaningPlanError('Selecciona un tipo de validación válido.')
        if action not in cls.ACTIONS:
            raise CleaningPlanError('Selecciona qué hacer con los valores problemáticos.')
        if rule_type == 'missing' and action == 'set_null':
            raise CleaningPlanError('Un valor vacío ya es nulo; elige conservar, reemplazar o separar.')
        if action == 'replace_value' and not replacement:
            raise CleaningPlanError('Escribe el valor que reemplazará las celdas problemáticas.')
        if len(replacement) > 200:
            raise CleaningPlanError('El valor de reemplazo no puede superar 200 caracteres.')

        parameters = {
            'invalid_action': action,
            'physical_type': column.get('type'),
            'source': 'manual',
        }
        if action == 'replace_value':
            parameters['replacement_value'] = replacement

        if rule_type == 'missing':
            operation = 'handle_missing'
            reason = 'Regla manual para resolver celdas vacías o nulas.'
        elif rule_type == 'email':
            operation = 'validate_email'
            reason = 'Regla manual para validar el formato de correo electrónico.'
        else:
            if not str(column.get('type', '')).upper().startswith(NUMERIC_TYPE_PREFIXES):
                raise CleaningPlanError('Los límites mínimo y máximo requieren una columna numérica.')
            minimum = cls._optional_number(form_data.get('custom_rule:minimum'), 'mínimo')
            maximum = cls._optional_number(form_data.get('custom_rule:maximum'), 'máximo')
            if minimum is None and maximum is None:
                raise CleaningPlanError('Escribe al menos un límite mínimo o máximo.')
            if minimum is not None and maximum is not None and minimum > maximum:
                raise CleaningPlanError('El límite mínimo no puede ser mayor que el máximo.')
            operation = 'validate_range'
            parameters.update({
                'constraint': 'between',
                'minimum': minimum,
                'maximum': maximum,
            })
            pieces = []
            if minimum is not None:
                pieces.append(f'mínimo {minimum:g}')
            if maximum is not None:
                pieces.append(f'máximo {maximum:g}')
            reason = 'Regla manual con ' + ' y '.join(pieces) + '.'

        identity = json.dumps(
            {
                'column': column_name,
                'operation': operation,
                'parameters': parameters,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(',', ':'),
        )
        operation_id = 'manual:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]
        candidate = {
            'id': operation_id,
            'column': column_name,
            'operation': operation,
            'decision': 'user_review',
            'affected_rows': 0,
            'confidence': 1,
            'parameters': parameters,
            'reason': reason,
        }
        candidate['affected_rows'] = cls._affected_rows(source_parquet, candidate)
        return candidate

    @staticmethod
    def _optional_number(raw_value, label):
        value = (raw_value or '').strip().replace(',', '.')
        if not value:
            return None
        try:
            number = float(value)
        except ValueError as error:
            raise CleaningPlanError(f'El límite {label} debe ser numérico.') from error
        if not math.isfinite(number):
            raise CleaningPlanError(f'El límite {label} debe ser un número finito.')
        return number

    @staticmethod
    def _affected_rows(source_parquet, operation):
        column = _quoted_identifier(operation['column'])
        name = operation['operation']
        parameters = operation['parameters']
        if name == 'handle_missing':
            predicate = f"{column} IS NULL OR trim(CAST({column} AS VARCHAR)) = ''"
        elif name == 'validate_email':
            escaped_pattern = EMAIL_PATTERN.replace("'", "''")
            predicate = (
                f"{column} IS NOT NULL AND trim(CAST({column} AS VARCHAR)) != '' "
                f"AND NOT regexp_full_match(trim(CAST({column} AS VARCHAR)), '{escaped_pattern}')"
            )
        else:
            predicates = []
            if parameters.get('minimum') is not None:
                predicates.append(f"{column} < {float(parameters['minimum'])}")
            if parameters.get('maximum') is not None:
                predicates.append(f"{column} > {float(parameters['maximum'])}")
            predicate = f"{column} IS NOT NULL AND ({' OR '.join(predicates)})"

        connection = duckdb.connect()
        try:
            relation = connection.read_parquet(str(source_parquet))
            relation.create_view('_codatau_custom_rule_source', replace=True)
            return int(connection.execute(
                f'SELECT count(*) FROM _codatau_custom_rule_source WHERE {predicate}'
            ).fetchone()[0])
        finally:
            connection.close()
