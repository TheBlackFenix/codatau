import re
import unicodedata


STRING_TYPE_PREFIXES = ('VARCHAR', 'CHAR', 'TEXT', 'STRING', 'ENUM')
NUMERIC_TYPE_PREFIXES = (
    'TINYINT',
    'SMALLINT',
    'INTEGER',
    'BIGINT',
    'HUGEINT',
    'UTINYINT',
    'USMALLINT',
    'UINTEGER',
    'UBIGINT',
    'UHUGEINT',
    'FLOAT',
    'DOUBLE',
    'REAL',
    'DECIMAL',
)
DATE_TYPE_PREFIXES = ('DATE', 'TIMESTAMP', 'TIME')

EMAIL_PATTERN = r'^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'
INTEGER_PATTERN = r'^[+-]?[0-9]+$'
NUMBER_PATTERN = r'^[+-]?([0-9]+([.][0-9]+)?|[.][0-9]+)$'
DECIMAL_COMMA_PATTERN = r'^[+-]?([0-9]+(,[0-9]+)?|,[0-9]+)$'
PHONE_PATTERN = r'^[+]?[0-9 ()-]{7,20}$'
LEADING_ZERO_PATTERN = r'^0[0-9]+$'

BOOLEAN_VALUES = ('true', 'false', 'yes', 'no', 'y', 'n', 'si', 'sí', '1', '0')

OPERATION_CATALOG = frozenset(
    {
        'blank_to_null',
        'cast_type',
        'handle_missing',
        'normalize_boolean',
        'normalize_case',
        'normalize_phone',
        'parse_date',
        'remove_exact_duplicates',
        'review_invalid_values',
        'trim_text',
        'validate_email',
    }
)

NAME_HINTS = {
    'email': {'email', 'correo', 'mail', 'e_mail'},
    'phone': {'phone', 'telefono', 'celular', 'movil', 'whatsapp', 'mobile'},
    'identifier': {
        'id',
        'codigo',
        'code',
        'sku',
        'nit',
        'cedula',
        'documento',
        'postal',
        'zip',
    },
    'date': {'fecha', 'date', 'nacimiento', 'birth', 'timestamp'},
    'boolean': {
        'activo',
        'active',
        'enabled',
        'habilitado',
        'flag',
        'es',
        'is',
    },
}


def _quoted_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _normalized_tokens(value):
    normalized = unicodedata.normalize('NFKD', value)
    ascii_name = ''.join(char for char in normalized if not unicodedata.combining(char))
    return set(filter(None, re.split(r'[^a-z0-9]+', ascii_name.lower())))


def _has_hint(column_name, semantic_type):
    tokens = _normalized_tokens(column_name)
    return bool(tokens & NAME_HINTS[semantic_type])


def _ratio(value, total):
    return round(value / total, 4) if total else 0.0


class SemanticProfiler:
    """Infers semantic types using full-column statistics calculated by DuckDB."""

    def __init__(self, connection, parquet_path):
        self.connection = connection
        self.parquet_path = str(parquet_path)

    def analyze(self, column_name, physical_type, row_count):
        type_name = physical_type.upper()
        if type_name.startswith(NUMERIC_TYPE_PREFIXES):
            semantic_type = (
                'identifier' if _has_hint(column_name, 'identifier') else 'number'
            )
            return {
                'type': semantic_type,
                'confidence': 0.98,
                'source': 'physical_type_and_name',
                'needs_ai': False,
                'evidence': {'physical_type': physical_type},
            }, []

        if type_name.startswith(DATE_TYPE_PREFIXES):
            return {
                'type': 'date',
                'confidence': 1.0,
                'source': 'physical_type',
                'needs_ai': False,
                'evidence': {'physical_type': physical_type},
            }, []

        if type_name == 'BOOLEAN':
            return {
                'type': 'boolean',
                'confidence': 1.0,
                'source': 'physical_type',
                'needs_ai': False,
                'evidence': {'physical_type': physical_type},
            }, []

        if not type_name.startswith(STRING_TYPE_PREFIXES):
            return {
                'type': 'unknown',
                'confidence': 0.5,
                'source': 'physical_type',
                'needs_ai': True,
                'evidence': {'physical_type': physical_type},
            }, []

        stats = self._string_statistics(column_name)
        semantic = self._infer_string_semantic(column_name, stats)
        operations = self._column_operations(column_name, physical_type, semantic, stats)
        semantic['evidence']['row_count'] = int(row_count)
        return semantic, operations

    def _string_statistics(self, column_name):
        identifier = _quoted_identifier(column_name)
        boolean_values = ', '.join(f"'{value}'" for value in BOOLEAN_VALUES)
        query = f"""
            WITH values AS (
                SELECT
                    CAST({identifier} AS VARCHAR) AS raw_value,
                    trim(CAST({identifier} AS VARCHAR)) AS value
                FROM read_parquet(?)
                WHERE {identifier} IS NOT NULL
            )
            SELECT
                count(*) AS non_null_count,
                count(*) FILTER (WHERE value = '') AS blank_count,
                count(*) FILTER (WHERE raw_value != value) AS whitespace_count,
                count(DISTINCT value) FILTER (WHERE value != '') AS distinct_count,
                count(DISTINCT lower(value)) FILTER (WHERE value != '')
                    AS casefold_distinct_count,
                count(*) FILTER (
                    WHERE value != '' AND regexp_full_match(value, '{EMAIL_PATTERN}')
                ) AS email_count,
                count(*) FILTER (
                    WHERE value != '' AND regexp_full_match(value, '{INTEGER_PATTERN}')
                ) AS integer_count,
                count(*) FILTER (
                    WHERE value != '' AND regexp_full_match(value, '{NUMBER_PATTERN}')
                ) AS number_count,
                count(*) FILTER (
                    WHERE value != '' AND regexp_full_match(value, '{DECIMAL_COMMA_PATTERN}')
                ) AS decimal_comma_count,
                count(*) FILTER (
                    WHERE value != ''
                    AND regexp_full_match(value, '{PHONE_PATTERN}')
                    AND length(regexp_replace(value, '[^0-9]', '', 'g')) BETWEEN 7 AND 15
                ) AS phone_count,
                count(*) FILTER (
                    WHERE value != '' AND regexp_full_match(value, '{LEADING_ZERO_PATTERN}')
                ) AS leading_zero_count,
                count(*) FILTER (
                    WHERE value != '' AND lower(value) IN ({boolean_values})
                ) AS boolean_count,
                count(*) FILTER (
                    WHERE value != '' AND try_strptime(value, '%Y-%m-%d') IS NOT NULL
                ) AS iso_date_count,
                count(*) FILTER (
                    WHERE value != '' AND try_strptime(value, '%d/%m/%Y') IS NOT NULL
                ) AS dmy_date_count,
                count(*) FILTER (
                    WHERE value != '' AND try_strptime(value, '%m/%d/%Y') IS NOT NULL
                ) AS mdy_date_count,
                count(*) FILTER (
                    WHERE value != ''
                    AND (
                        try_strptime(value, '%Y-%m-%d') IS NOT NULL
                        OR try_strptime(value, '%d/%m/%Y') IS NOT NULL
                        OR try_strptime(value, '%m/%d/%Y') IS NOT NULL
                    )
                ) AS any_date_count,
                count(*) FILTER (
                    WHERE value != ''
                    AND try_strptime(value, '%d/%m/%Y') IS NOT NULL
                    AND try_strptime(value, '%m/%d/%Y') IS NOT NULL
                ) AS ambiguous_date_count
            FROM values
        """
        row = self.connection.execute(query, [self.parquet_path]).fetchone()
        names = [description[0] for description in self.connection.description]
        return {name: int(value or 0) for name, value in zip(names, row)}

    @staticmethod
    def _infer_string_semantic(column_name, stats):
        non_blank = stats['non_null_count'] - stats['blank_count']
        ratios = {
            'email': _ratio(stats['email_count'], non_blank),
            'integer': _ratio(stats['integer_count'], non_blank),
            'number': _ratio(stats['number_count'], non_blank),
            'decimal_comma': _ratio(stats['decimal_comma_count'], non_blank),
            'phone': _ratio(stats['phone_count'], non_blank),
            'boolean': _ratio(stats['boolean_count'], non_blank),
            'date': _ratio(stats['any_date_count'], non_blank),
        }
        evidence = {
            'non_blank_count': non_blank,
            'blank_count': stats['blank_count'],
            'whitespace_count': stats['whitespace_count'],
            'distinct_count': stats['distinct_count'],
            'casefold_distinct_count': stats['casefold_distinct_count'],
            'leading_zero_count': stats['leading_zero_count'],
            'ambiguous_date_count': stats['ambiguous_date_count'],
            'match_ratios': ratios,
            'date_formats': {
                'iso': stats['iso_date_count'],
                'day_first': stats['dmy_date_count'],
                'month_first': stats['mdy_date_count'],
            },
        }

        if non_blank == 0:
            return {
                'type': 'empty',
                'confidence': 1.0,
                'source': 'rules',
                'needs_ai': False,
                'evidence': evidence,
            }

        if _has_hint(column_name, 'email') or ratios['email'] >= 0.8:
            confidence = ratios['email'] if ratios['email'] else 0.6
            semantic_type = 'email'
        elif _has_hint(column_name, 'phone'):
            confidence = max(ratios['phone'], 0.7)
            semantic_type = 'phone'
        elif _has_hint(column_name, 'identifier') or (
            stats['leading_zero_count'] > 0 and ratios['integer'] >= 0.8
        ):
            confidence = 0.98 if stats['leading_zero_count'] else 0.9
            semantic_type = 'identifier'
        elif _has_hint(column_name, 'date') or ratios['date'] >= 0.8:
            confidence = ratios['date'] if ratios['date'] else 0.6
            semantic_type = 'date'
        elif _has_hint(column_name, 'boolean') and ratios['boolean'] >= 0.8:
            confidence = ratios['boolean']
            semantic_type = 'boolean'
        elif ratios['boolean'] == 1 and ratios['number'] < 1:
            confidence = 0.95
            semantic_type = 'boolean'
        elif max(ratios['number'], ratios['decimal_comma']) >= 0.8:
            confidence = max(ratios['number'], ratios['decimal_comma'])
            semantic_type = 'number'
        else:
            confidence = 0.85
            semantic_type = 'text'

        needs_ai = False
        if semantic_type == 'email' and ratios['email'] < 1:
            needs_ai = True
        elif semantic_type == 'phone' and ratios['phone'] < 1:
            needs_ai = True
        elif semantic_type in {'number', 'date', 'boolean'} and confidence < 1:
            needs_ai = True
        elif semantic_type == 'date' and stats['ambiguous_date_count'] > 0:
            needs_ai = True

        return {
            'type': semantic_type,
            'confidence': round(confidence, 4),
            'source': 'rules_and_patterns',
            'needs_ai': needs_ai,
            'evidence': evidence,
        }

    @staticmethod
    def _column_operations(column_name, physical_type, semantic, stats):
        operations = []
        evidence = semantic['evidence']
        ratios = evidence['match_ratios']
        non_blank = evidence['non_blank_count']

        def add(operation, decision, affected_rows, reason, parameters=None):
            if operation not in OPERATION_CATALOG:
                raise ValueError(f'Operación de limpieza no permitida: {operation}')
            operations.append(
                {
                    'id': f'{column_name}:{operation}',
                    'column': column_name,
                    'operation': operation,
                    'decision': decision,
                    'affected_rows': int(affected_rows),
                    'confidence': semantic['confidence'],
                    'parameters': parameters or {},
                    'reason': reason,
                }
            )

        if stats['whitespace_count']:
            add(
                'trim_text',
                'automatic',
                stats['whitespace_count'],
                'Eliminar espacios externos no cambia el contenido del valor.',
            )
        if stats['blank_count']:
            add(
                'blank_to_null',
                'automatic',
                stats['blank_count'],
                'Los textos vacíos se representan de forma consistente como nulos.',
            )

        semantic_type = semantic['type']
        if semantic_type == 'identifier':
            return operations

        if semantic_type == 'number':
            best_ratio = max(ratios['number'], ratios['decimal_comma'])
            uses_decimal_comma = ratios['decimal_comma'] > ratios['number']
            if best_ratio < 1:
                decision = 'ai_analysis'
            elif uses_decimal_comma:
                decision = 'user_review'
            else:
                decision = 'automatic'
            parameters = {
                'target_type': (
                    'BIGINT' if ratios['integer'] == 1 else 'DOUBLE'
                ),
                'decimal_separator': ',' if uses_decimal_comma else '.',
                'on_error': 'quarantine',
            }
            add(
                'cast_type',
                decision,
                non_blank,
                'Los valores siguen un patrón numérico inequívoco.'
                if decision == 'automatic'
                else (
                    'Se debe confirmar si la coma representa decimales o miles.'
                    if decision == 'user_review'
                    else 'Algunos valores rompen el patrón numérico dominante.'
                ),
                parameters,
            )
        elif semantic_type == 'email':
            invalid_count = non_blank - stats['email_count']
            add(
                'validate_email',
                'automatic',
                non_blank,
                'Validar el formato no modifica los correos originales.',
                {'invalid_action': 'flag'},
            )
            if invalid_count:
                add(
                    'review_invalid_values',
                    'ai_analysis',
                    invalid_count,
                    'Los correos inválidos requieren revisión; no se deben inventar valores.',
                    {'semantic_type': 'email'},
                )
        elif semantic_type == 'date':
            ambiguous = stats['ambiguous_date_count']
            iso_only = stats['iso_date_count'] == non_blank
            decision = 'automatic' if iso_only and not ambiguous else 'user_review'
            if semantic['needs_ai']:
                decision = 'ai_analysis'
            add(
                'parse_date',
                decision,
                non_blank,
                'El formato ISO se puede convertir sin ambigüedad.'
                if decision == 'automatic'
                else 'Se debe confirmar el formato regional o revisar valores inconsistentes.',
                {
                    'accepted_formats': ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'],
                    'ambiguous_rows': ambiguous,
                    'on_error': 'quarantine',
                },
            )
        elif semantic_type == 'boolean':
            decision = 'automatic' if ratios['boolean'] == 1 else 'ai_analysis'
            add(
                'normalize_boolean',
                decision,
                non_blank,
                'Los valores pertenecen al vocabulario booleano conocido.'
                if decision == 'automatic'
                else 'Hay valores fuera del vocabulario booleano conocido.',
                {'on_error': 'quarantine'},
            )
        elif semantic_type == 'phone':
            invalid_count = non_blank - stats['phone_count']
            add(
                'normalize_phone',
                'user_review' if not invalid_count else 'ai_analysis',
                non_blank,
                'El país y el tratamiento de extensiones deben confirmarse.',
                {'invalid_rows': invalid_count, 'preserve_as_text': True},
            )
        elif (
            semantic_type == 'text'
            and stats['distinct_count'] > stats['casefold_distinct_count']
        ):
            add(
                'normalize_case',
                'user_review',
                non_blank,
                'Hay categorías que solo difieren en mayúsculas y minúsculas.',
            )

        return operations


def build_cleaning_plan(column_operations, duplicate_rows):
    operations = [operation for group in column_operations for operation in group]
    if duplicate_rows:
        operations.append(
            {
                'id': 'dataset:remove_exact_duplicates',
                'column': None,
                'operation': 'remove_exact_duplicates',
                'decision': 'user_review',
                'affected_rows': int(duplicate_rows),
                'confidence': 1.0,
                'parameters': {},
                'reason': 'Las filas son idénticas, pero podrían representar eventos válidos.',
            }
        )

    counts = {
        decision: sum(1 for operation in operations if operation['decision'] == decision)
        for decision in ('automatic', 'user_review', 'ai_analysis')
    }
    return {
        'plan_version': '1.0',
        'operation_catalog_version': '1.0',
        'status': 'proposed',
        'operations': operations,
        'summary': counts,
        'ai_candidate_columns': sorted(
            {
                operation['column']
                for operation in operations
                if operation['decision'] == 'ai_analysis' and operation['column']
            }
        ),
    }
