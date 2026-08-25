import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb

from app.services.semantic_profiler import EMAIL_PATTERN


ROW_ID = '__codatau_row_id'
INVALID = '__codatau_invalid'
REASON = '__quarantine_reason'


class CleaningPlanError(ValueError):
    pass


@dataclass(frozen=True)
class CleaningPreview:
    metrics: dict
    before: list
    after: list
    quarantine: list


def _quoted_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def select_executable_operations(plan, selected_ids):
    selected_ids = list(dict.fromkeys(selected_ids))
    if not selected_ids:
        raise CleaningPlanError('Selecciona al menos una operación para continuar.')

    proposed = {operation['id']: operation for operation in plan['operations']}
    unknown = [operation_id for operation_id in selected_ids if operation_id not in proposed]
    if unknown:
        raise CleaningPlanError('El plan contiene operaciones desconocidas o desactualizadas.')

    operations = [proposed[operation_id] for operation_id in selected_ids]
    for operation in operations:
        if not CleaningExecutor.is_executable(operation):
            raise CleaningPlanError(
                f'La operación “{operation["operation"]}” todavía requiere análisis adicional.'
            )
    return operations


class CleaningExecutor:
    """Previews and materializes allow-listed transformations with DuckDB SQL."""

    SUPPORTED_AUTOMATIC = {
        'trim_text',
        'blank_to_null',
        'cast_type',
        'validate_email',
        'parse_date',
        'normalize_boolean',
    }

    @classmethod
    def is_executable(cls, operation):
        if operation['operation'] == 'remove_exact_duplicates':
            return operation['decision'] == 'user_review'
        return (
            operation['decision'] == 'automatic'
            and operation['operation'] in cls.SUPPORTED_AUTOMATIC
        )

    def preview(self, source_parquet, operations, sample_size=8):
        connection = duckdb.connect()
        try:
            queries = self._queries(connection, source_parquet, operations)
            metrics = self._metrics(connection, queries)
            return CleaningPreview(
                metrics=metrics,
                before=self._records(connection, queries['before'], sample_size),
                after=self._records(connection, queries['clean'], sample_size),
                quarantine=self._records(
                    connection,
                    queries['quarantine'],
                    sample_size,
                ),
            )
        finally:
            connection.close()

    def apply(self, source_parquet, operations, output_parquet, quarantine_parquet):
        output_path = Path(output_parquet)
        quarantine_path = Path(quarantine_parquet)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output_path.with_name(
            f'.{output_path.name}.{uuid.uuid4().hex}.tmp'
        )
        temporary_quarantine = quarantine_path.with_name(
            f'.{quarantine_path.name}.{uuid.uuid4().hex}.tmp'
        )

        connection = duckdb.connect()
        try:
            queries = self._queries(connection, source_parquet, operations)
            metrics = self._metrics(connection, queries)
            connection.sql(queries['clean']).write_parquet(
                str(temporary_output),
                compression='zstd',
            )
            if metrics['quarantined_rows']:
                connection.sql(queries['quarantine']).write_parquet(
                    str(temporary_quarantine),
                    compression='zstd',
                )

            os.replace(temporary_output, output_path)
            if temporary_quarantine.exists():
                os.replace(temporary_quarantine, quarantine_path)
            elif quarantine_path.exists():
                quarantine_path.unlink()
            return metrics
        finally:
            connection.close()
            for temporary in (temporary_output, temporary_quarantine):
                if temporary.exists():
                    temporary.unlink()

    def _queries(self, connection, source_parquet, operations):
        relation = connection.read_parquet(str(source_parquet))
        columns = relation.columns
        relation.create_view('_codatau_source', replace=True)

        base = (
            f'SELECT row_number() OVER () AS {_quoted_identifier(ROW_ID)}, * '
            'FROM _codatau_source'
        )
        expressions = {column: _quoted_identifier(column) for column in columns}
        invalid_predicates = []
        changed_predicates = []
        reason_cases = []
        remove_duplicates = False

        for operation in operations:
            name = operation['operation']
            column = operation.get('column')
            if name == 'remove_exact_duplicates':
                remove_duplicates = True
                continue
            if column not in expressions:
                raise CleaningPlanError(f'La columna “{column}” ya no existe en el dataset.')

            current = expressions[column]
            transformed, invalid, changed = self._operation_sql(operation, current)
            expressions[column] = transformed
            if invalid:
                invalid_predicates.append(invalid)
                reason_cases.append(
                    f'CASE WHEN {invalid} THEN {_sql_literal(operation["id"])} END'
                )
            if changed:
                changed_predicates.append(changed)

        invalid_sql = ' OR '.join(f'({predicate})' for predicate in invalid_predicates)
        invalid_sql = invalid_sql or 'FALSE'
        reason_sql = (
            f"concat_ws('; ', {', '.join(reason_cases)})"
            if reason_cases
            else "''"
        )
        projected = ', '.join(
            f'{expression} AS {_quoted_identifier(column)}'
            for column, expression in expressions.items()
        )
        transformed = (
            f'SELECT {_quoted_identifier(ROW_ID)}, {projected}, '
            f'{invalid_sql} AS {_quoted_identifier(INVALID)}, '
            f'{reason_sql} AS {_quoted_identifier(REASON)} '
            f'FROM ({base}) AS base'
        )
        cleaned = (
            f'SELECT * EXCLUDE ({_quoted_identifier(ROW_ID)}, '
            f'{_quoted_identifier(INVALID)}, {_quoted_identifier(REASON)}) '
            f'FROM ({transformed}) AS transformed '
            f'WHERE NOT {_quoted_identifier(INVALID)}'
        )
        if remove_duplicates:
            cleaned = f'SELECT DISTINCT * FROM ({cleaned}) AS valid_rows'

        original_columns = ', '.join(
            f'base.{_quoted_identifier(column)}' for column in columns
        )
        quarantine = (
            f'SELECT {original_columns}, '
            f'transformed.{_quoted_identifier(REASON)} '
            f'FROM ({base}) AS base JOIN ({transformed}) AS transformed '
            f'USING ({_quoted_identifier(ROW_ID)}) '
            f'WHERE transformed.{_quoted_identifier(INVALID)}'
        )
        before = (
            f'SELECT * EXCLUDE ({_quoted_identifier(ROW_ID)}) '
            f'FROM ({base}) AS base'
        )

        changed = ' OR '.join(
            f'({predicate})' for predicate in changed_predicates
        ) or 'FALSE'
        changed_count = (
            f'SELECT count(*) FROM ({base}) AS base '
            f'WHERE ({changed}) AND NOT ({invalid_sql})'
        )

        return {
            'before': before,
            'clean': cleaned,
            'quarantine': quarantine,
            'transformed': transformed,
            'changed_count': changed_count,
            'remove_duplicates': remove_duplicates,
        }

    @staticmethod
    def _operation_sql(operation, current):
        name = operation['operation']
        parameters = operation.get('parameters') or {}
        text = f'trim(CAST({current} AS VARCHAR))'
        present = f'{current} IS NOT NULL AND {text} != \'\''

        if name == 'trim_text':
            return text, None, f'{current} IS DISTINCT FROM {text}'
        if name == 'blank_to_null':
            transformed = f"nullif({text}, '')"
            return transformed, None, f'{current} IS DISTINCT FROM {transformed}'
        if name == 'cast_type':
            target = parameters.get('target_type')
            if target not in {'BIGINT', 'DOUBLE'}:
                raise CleaningPlanError('El tipo numérico propuesto no es ejecutable.')
            if parameters.get('decimal_separator', '.') != '.':
                raise CleaningPlanError('Confirma el separador decimal antes de convertir.')
            transformed = f'try_cast({text} AS {target})'
            invalid = f'{present} AND {transformed} IS NULL'
            return transformed, invalid, f'{present} AND {transformed} IS NOT NULL'
        if name == 'validate_email':
            valid = f"regexp_full_match({text}, {_sql_literal(EMAIL_PATTERN)})"
            return current, f'{present} AND NOT {valid}', None
        if name == 'parse_date':
            transformed = f"try_strptime({text}, '%Y-%m-%d')::DATE"
            invalid = f'{present} AND {transformed} IS NULL'
            return transformed, invalid, f'{present} AND {transformed} IS NOT NULL'
        if name == 'normalize_boolean':
            lowered = f'lower({text})'
            transformed = (
                f"CASE WHEN {lowered} IN ('true', 'yes', 'y', 'si', 'sí', '1') "
                f"THEN TRUE WHEN {lowered} IN ('false', 'no', 'n', '0') "
                'THEN FALSE END'
            )
            invalid = f'{present} AND {transformed} IS NULL'
            return transformed, invalid, f'{present} AND {transformed} IS NOT NULL'
        raise CleaningPlanError(f'Operación no implementada: {name}')

    @staticmethod
    def _records(connection, query, limit):
        frame = connection.sql(f'SELECT * FROM ({query}) AS sample LIMIT {int(limit)}').df()
        return json.loads(frame.to_json(orient='records', date_format='iso'))

    @staticmethod
    def _metrics(connection, queries):
        before_rows = connection.sql(
            f"SELECT count(*) FROM ({queries['before']}) AS rows"
        ).fetchone()[0]
        after_rows = connection.sql(
            f"SELECT count(*) FROM ({queries['clean']}) AS rows"
        ).fetchone()[0]
        quarantined_rows = connection.sql(
            f"SELECT count(*) FROM ({queries['quarantine']}) AS rows"
        ).fetchone()[0]
        changed_rows = connection.sql(queries['changed_count']).fetchone()[0]
        valid_rows = before_rows - quarantined_rows
        return {
            'before_rows': int(before_rows),
            'after_rows': int(after_rows),
            'changed_rows': int(changed_rows),
            'quarantined_rows': int(quarantined_rows),
            'duplicates_removed': int(valid_rows - after_rows),
        }
