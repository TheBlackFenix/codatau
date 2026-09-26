from copy import deepcopy

import duckdb

from app.services.semantic_profiler import NUMERIC_TYPE_PREFIXES


def _quoted_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class SemanticConstraintService:
    """Turns validated context suggestions into allow-listed review rules."""

    SUPPORTED_CONSTRAINTS = {'positive', 'non_negative'}

    @classmethod
    def enrich_plan(cls, profile, dataset_context, source_parquet):
        enriched = deepcopy(profile)
        if not isinstance(dataset_context, dict):
            return enriched

        columns = {
            column.get('name'): column
            for column in enriched.get('columns') or []
        }
        existing_ids = {
            operation.get('id')
            for operation in enriched['cleaning_plan']['operations']
        }
        proposed = []
        connection = duckdb.connect()
        try:
            relation = connection.read_parquet(str(source_parquet))
            relation.create_view('_codatau_semantic_source', replace=True)
            for role in dataset_context.get('column_roles') or []:
                column_name = role.get('column')
                column = columns.get(column_name)
                if (
                    role.get('role') not in {'measure', 'dimension'}
                    or not column
                    or not str(column.get('type', '')).upper().startswith(
                        NUMERIC_TYPE_PREFIXES
                    )
                ):
                    continue
                for constraint in role.get('suggested_constraints') or []:
                    if constraint not in cls.SUPPORTED_CONSTRAINTS:
                        continue
                    operation_id = (
                        f'{column_name}:validate_range:{constraint}'
                    )
                    if operation_id in existing_ids:
                        continue
                    identifier = _quoted_identifier(column_name)
                    comparison = '<= 0' if constraint == 'positive' else '< 0'
                    affected_rows = connection.execute(
                        'SELECT count(*) FROM _codatau_semantic_source '
                        f'WHERE {identifier} IS NOT NULL '
                        f'AND {identifier} {comparison}'
                    ).fetchone()[0]
                    if not affected_rows:
                        continue
                    label = (
                        'mayor que cero'
                        if constraint == 'positive'
                        else 'igual o mayor que cero'
                    )
                    proposed.append({
                        'id': operation_id,
                        'column': column_name,
                        'operation': 'validate_range',
                        'decision': 'user_review',
                        'affected_rows': int(affected_rows),
                        'confidence': dataset_context.get('confidence', 0),
                        'parameters': {
                            'constraint': constraint,
                            'threshold': 0,
                            'invalid_action': 'quarantine_rows',
                            'physical_type': column.get('type'),
                        },
                        'reason': (
                            f'El contexto “{dataset_context.get("domain", "general")}” '
                            f'sugiere que {column_name} debe ser {label}.'
                        ),
                    })
                    existing_ids.add(operation_id)
        finally:
            connection.close()

        enriched['cleaning_plan']['operations'].extend(proposed)
        enriched['cleaning_plan']['summary']['user_review'] += len(proposed)
        return enriched
