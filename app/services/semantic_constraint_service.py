from copy import deepcopy

import duckdb

from app.services.semantic_profiler import EMAIL_PATTERN, NUMERIC_TYPE_PREFIXES


def _quoted_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class SemanticConstraintService:
    """Turns validated context suggestions into allow-listed review rules."""

    SUPPORTED_CONSTRAINTS = {
        'positive',
        'non_negative',
        'not_empty',
        'valid_email',
    }

    @classmethod
    def enrich_plan(cls, profile, dataset_context, source_parquet):
        enriched = deepcopy(profile)
        if not isinstance(dataset_context, dict):
            return enriched

        columns = {
            column.get('name'): column
            for column in enriched.get('columns') or []
        }
        existing_operations = {
            operation.get('id'): operation
            for operation in enriched['cleaning_plan']['operations']
        }
        existing_ids = set(existing_operations)
        proposed = []
        connection = duckdb.connect()
        try:
            relation = connection.read_parquet(str(source_parquet))
            relation.create_view('_codatau_semantic_source', replace=True)
            for role in dataset_context.get('column_roles') or []:
                column_name = role.get('column')
                column = columns.get(column_name)
                if not column:
                    continue
                for constraint in role.get('suggested_constraints') or []:
                    if constraint not in cls.SUPPORTED_CONSTRAINTS:
                        continue
                    candidate = cls._candidate(
                        column_name,
                        column,
                        role,
                        constraint,
                        dataset_context,
                    )
                    if not candidate:
                        continue
                    operation_id = candidate['id']
                    identifier = _quoted_identifier(column_name)
                    predicate = cls._invalid_predicate(
                        identifier,
                        constraint,
                    )
                    affected_rows = connection.execute(
                        'SELECT count(*) FROM _codatau_semantic_source '
                        f'WHERE {predicate}'
                    ).fetchone()[0]
                    if not affected_rows:
                        continue
                    rows = connection.execute(
                        'SELECT DISTINCT CAST('
                        f'{identifier} AS VARCHAR) FROM _codatau_semantic_source '
                        f'WHERE {predicate} LIMIT 3'
                    ).fetchall()
                    examples = [
                        '<vacío>' if row[0] is None or not row[0].strip()
                        else row[0][:80]
                        for row in rows
                    ]
                    if operation_id in existing_ids:
                        existing = existing_operations[operation_id]
                        if existing.get('operation') != candidate['operation']:
                            continue
                        existing['context_reason'] = candidate['reason']
                        existing['examples'] = examples
                        continue
                    candidate['affected_rows'] = int(affected_rows)
                    candidate['examples'] = examples
                    proposed.append(candidate)
                    existing_operations[operation_id] = candidate
                    existing_ids.add(operation_id)
        finally:
            connection.close()

        enriched['cleaning_plan']['operations'].extend(proposed)
        enriched['cleaning_plan']['summary']['user_review'] += len(proposed)
        return enriched

    @staticmethod
    def _candidate(column_name, column, role, constraint, dataset_context):
        physical_type = str(column.get('type', '')).upper()
        domain = dataset_context.get('domain', 'general')
        description = role.get('description') or column_name
        common = {
            'column': column_name,
            'decision': 'user_review',
            'affected_rows': 0,
            'confidence': dataset_context.get('confidence', 0),
        }
        if constraint in {'positive', 'non_negative'}:
            if (
                role.get('role') not in {'measure', 'dimension'}
                or not physical_type.startswith(NUMERIC_TYPE_PREFIXES)
            ):
                return None
            label = (
                'mayor que cero'
                if constraint == 'positive'
                else 'igual o mayor que cero'
            )
            return {
                **common,
                'id': f'{column_name}:validate_range:{constraint}',
                'operation': 'validate_range',
                'parameters': {
                    'constraint': constraint,
                    'threshold': 0,
                    'invalid_action': 'quarantine_rows',
                    'physical_type': column.get('type'),
                    'source': 'dataset_context',
                },
                'reason': (
                    f'En el contexto “{domain}”, {description} debe ser {label}.'
                ),
            }
        if constraint == 'not_empty':
            return {
                **common,
                'id': f'{column_name}:handle_missing',
                'operation': 'handle_missing',
                'parameters': {
                    'invalid_action': 'quarantine_rows',
                    'physical_type': column.get('type'),
                    'source': 'dataset_context',
                },
                'reason': (
                    f'En el contexto “{domain}”, {description} no debería estar vacío.'
                ),
            }
        if constraint == 'valid_email' and physical_type.startswith('VARCHAR'):
            return {
                **common,
                'id': f'{column_name}:validate_email',
                'operation': 'validate_email',
                'parameters': {
                    'invalid_action': 'quarantine_rows',
                    'physical_type': column.get('type'),
                    'source': 'dataset_context',
                },
                'reason': (
                    f'En el contexto “{domain}”, {description} debe tener formato de correo.'
                ),
            }
        return None

    @staticmethod
    def _invalid_predicate(identifier, constraint):
        if constraint == 'positive':
            return f'{identifier} IS NOT NULL AND {identifier} <= 0'
        if constraint == 'non_negative':
            return f'{identifier} IS NOT NULL AND {identifier} < 0'
        if constraint == 'not_empty':
            return (
                f'{identifier} IS NULL OR '
                f"trim(CAST({identifier} AS VARCHAR)) = ''"
            )
        escaped_pattern = EMAIL_PATTERN.replace("'", "''")
        return (
            f'{identifier} IS NOT NULL AND '
            f"trim(CAST({identifier} AS VARCHAR)) != '' AND NOT "
            f"regexp_full_match(trim(CAST({identifier} AS VARCHAR)), '{escaped_pattern}')"
        )
