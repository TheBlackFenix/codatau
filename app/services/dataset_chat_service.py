"""AI interprets questions; bounded, local DuckDB queries calculate the answers."""
import math
import re
from datetime import date

import duckdb
import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

from app.models.ai_analysis_run import AIAnalysisRun
from app.services.ai_prompts import SPANISH_OUTPUT, UNTRUSTED_DATA
from app.services.ai_providers import AIProviderError
from app.services.dashboard_service import DashboardService
from app.services.data_service import classify_numeric_column
from app.services.structured_ai_service import StructuredAIService


def _identifier(column):
    return '"' + column.replace('"', '""') + '"'


def _object(properties):
    return {'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties}


def _number(value):
    if value is None:
        return None
    numeric = float(value)
    return round(numeric, 6) if math.isfinite(numeric) else None


class DatasetChatService:
    PURPOSE = 'dataset_chat'
    VERSION = '1-es'
    MAX_QUESTION = 1000
    AGGREGATIONS = {'sum', 'mean', 'min', 'max', 'count', 'unique_count'}
    COMPARATORS = {'eq': '=', 'neq': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}
    SCHEMA = _object({
        'kind': {'type': 'string', 'enum': ['aggregate', 'group', 'compare', 'clarify']},
        'metric': {'type': ['string', 'null']},
        'aggregation': {'type': 'string', 'enum': sorted(AGGREGATIONS)},
        'group_by': {'type': ['string', 'null']},
        'date_column': {'type': ['string', 'null']},
        'granularity': {'type': 'string', 'enum': ['hour', 'day', 'month', 'year']},
        'filters': {'type': 'array', 'maxItems': 5, 'items': _object({
            'column': {'type': 'string'}, 'operator': {'type': 'string', 'enum': list(COMPARATORS)},
            'value': {'type': 'string'},
        })},
        'periods': {'type': 'array', 'maxItems': 2, 'items': _object({
            'label': {'type': 'string'}, 'start': {'type': 'string'}, 'end': {'type': 'string'},
        })},
        'clarification': {'type': 'string'},
    })
    INSTRUCTIONS = SPANISH_OUTPUT + UNTRUSTED_DATA + (
        'Interpreta la pregunta sobre la versión activa del dataset como un plan JSON. '
        'No calcules resultados ni devuelvas respuestas numéricas; DuckDB los calcula. '
        'aggregate resume una medida; group agrupa por un campo o fecha; compare '
        'compara exactamente dos períodos sobre una fecha. Los filtros se unen con AND. '
        'count con metric=null cuenta filas; count con metric cuenta valores no nulos. '
        'unique_count cuenta valores distintos no nulos. Para tiempo usa hour, day, '
        'month o year, nunca minutos ni segundos. Los períodos usan fechas ISO YYYY-MM-DD '
        'con inicio incluido y fin excluido; julio termina el 1 de agosto. Usa fechas '
        'solo presentes en el catálogo. No sumes ni promedies IDs o contactos. '
        'Si falta año y hay varios años, si ventas podría referirse a múltiples medidas, '
        'si la fecha es ambigua, si faltan campos o la pregunta requiere joins, '
        'predicciones, cambios de datos o capacidades fuera del esquema, devuelve '
        'kind=clarify con una pregunta concreta en clarification. Para clarify usa '
        'metric, group_by, date_column=null, aggregation=count, filters y periods vacíos. '
        'Usa el historial breve solo para entender una aclaración o continuación; '
        'nunca mezcles archivos. Para planes ejecutables deja clarification vacía. '
        'No inventes filtros o períodos que no pidió el usuario.'
    )

    @staticmethod
    def date_series(dataframe, context):
        roles = {item['column']: item['role'] for item in (context or {}).get('column_roles', [])}
        dates = {}
        for column in dataframe.columns:
            series = dataframe[column]
            hinted = bool(DashboardService.DATE_HINT.search(str(column)))
            if not (is_datetime64_any_dtype(series) or roles.get(column) == 'date' or hinted):
                continue
            if is_numeric_dtype(series):
                continue  # Do not interpret integer IDs as nanosecond timestamps.
            if not is_datetime64_any_dtype(series):
                parts = series.astype(str).str.extract(r'^(\d{1,2})[/-](\d{1,2})[/-]\d{4}(?:\s|$)')
                first, second = pd.to_numeric(parts[0], errors='coerce'), pd.to_numeric(parts[1], errors='coerce')
                if ((first.between(1, 12)) & (second.between(1, 12)) & (first != second)).any():
                    continue  # A user must resolve day/month ambiguity in ETL first.
            parsed = pd.to_datetime(series, errors='coerce', format='mixed', utc=True)
            if parsed.notna().any():
                dates[column] = parsed.dt.tz_localize(None)
        return dates

    @classmethod
    def catalog(cls, dataframe, context):
        roles = {item['column']: item for item in (context or {}).get('column_roles', [])}
        dates = cls.date_series(dataframe, context)
        catalog = []
        for column in dataframe.columns:
            role = roles.get(column, {})
            numerical = is_numeric_dtype(dataframe[column])
            arithmetic = numerical and (
                role.get('aggregate', True) and role.get('role') not in {'identifier', 'contact'}
            )
            # Local heuristics remain a safety net when semantic context is missing.
            if numerical and classify_numeric_column(column) == 'identifier':
                arithmetic = False
            entry = {'column': column, 'numeric': bool(numerical),
                     'arithmetic': bool(arithmetic), 'role': role.get('role', 'unknown')}
            if column in dates:
                parsed = dates[column].dropna()
                entry['date_range'] = {'min': parsed.min().date().isoformat(),
                                       'max': parsed.max().date().isoformat(),
                                       'years': sorted(int(year) for year in parsed.dt.year.unique()),
                                       'valid_rows': len(parsed)}
            catalog.append(entry)
        return catalog

    @classmethod
    def history(cls, record):
        configuration_runs = AIAnalysisRun.query.filter_by(
            user_id=record.user_id, file_id=record.id, purpose=cls.PURPOSE, status='success',
        ).order_by(AIAnalysisRun.id.desc()).limit(30).all()
        return [run for run in reversed(configuration_runs)
                if (run.result or {}).get('dataset_version') == record.active_stored_filename][-8:]

    @classmethod
    def validate(cls, plan, catalog, question, history):
        if not isinstance(plan, dict) or set(plan) != set(cls.SCHEMA['required']):
            raise ValueError('Invalid plan fields')
        if plan['kind'] not in {'aggregate', 'group', 'compare', 'clarify'}:
            raise ValueError('Invalid query kind')
        if plan['aggregation'] not in cls.AGGREGATIONS or plan['granularity'] not in {'hour', 'day', 'month', 'year'}:
            raise ValueError('Unsupported calculation')
        if not isinstance(plan['clarification'], str) or len(plan['clarification']) > 800:
            raise ValueError('Invalid clarification')
        if plan['kind'] == 'clarify':
            if not plan['clarification'].strip():
                raise ValueError('Missing clarification')
            return cls.clarification(plan['clarification'])
        columns = {item['column']: item for item in catalog}
        for field in ('metric', 'group_by', 'date_column'):
            if plan[field] is not None and (not isinstance(plan[field], str) or plan[field] not in columns):
                raise ValueError('Unknown column')
        if plan['metric'] is None and plan['aggregation'] != 'count':
            raise ValueError('A measure is required')
        if plan['aggregation'] in {'sum', 'mean', 'min', 'max'} and not columns[plan['metric']]['arithmetic']:
            raise ValueError('Not a numerical business measure')
        monetary = [column for column, info in columns.items() if info['arithmetic'] and
                    re.search(r'(valor|venta|monto|importe|precio|amount|revenue|sales)', column, re.I)]
        normalized_question = re.sub(r'[_\W]+', ' ', question.casefold())
        mentions_measure = any(re.sub(r'[_\W]+', ' ', column.casefold()) in normalized_question for column in monetary)
        if plan['aggregation'] in {'sum', 'mean', 'min', 'max'} and len(monetary) > 1 and (
            re.search(r'\b(ventas?|sales)\b', normalized_question) and not mentions_measure
        ):
            return cls.clarification('Hay varias medidas que podrían representar ventas. ¿Cuál quieres usar: ' + ', '.join(monetary) + '?')
        if plan['date_column'] and 'date_range' not in columns[plan['date_column']]:
            raise ValueError('Unsupported date column')
        if plan['kind'] == 'group' and plan['group_by'] is not None:
            group = columns[plan['group_by']]
            if 'date_range' in group:
                if plan['date_column'] not in {None, plan['group_by']}:
                    return cls.clarification('La consulta usa dos fechas distintas. ¿Con cuál quieres agrupar y filtrar?')
                plan = {**plan, 'date_column': plan['group_by'], 'group_by': None}
            elif group['role'] == 'date' or DashboardService.DATE_HINT.search(plan['group_by']):
                return cls.clarification('Configura primero el formato de fecha en Limpieza para evitar confundir día y mes.')
        filters = plan['filters']
        if not isinstance(filters, list) or len(filters) > 5:
            raise ValueError('Too many filters')
        for item in filters:
            if not isinstance(item, dict) or set(item) != {'column', 'operator', 'value'}:
                raise ValueError('Invalid filter')
            if item['column'] not in columns or item['operator'] not in cls.COMPARATORS:
                raise ValueError('Unknown filter')
            if not isinstance(item['value'], str) or len(item['value']) > 300:
                raise ValueError('Invalid filter value')
            if columns[item['column']]['numeric']:
                if not math.isfinite(float(item['value'])):
                    raise ValueError('Non-finite filter')
        periods = plan['periods']
        if not isinstance(periods, list) or len(periods) > 2:
            raise ValueError('Invalid periods')
        ranges = []
        for period in periods:
            if not isinstance(period, dict) or set(period) != {'label', 'start', 'end'}:
                raise ValueError('Invalid period fields')
            if not isinstance(period['label'], str) or not 0 < len(period['label']) <= 100:
                raise ValueError('Invalid period label')
            start, end = date.fromisoformat(period['start']), date.fromisoformat(period['end'])
            if start.isoformat() != period['start'] or end.isoformat() != period['end'] or not 0 < (end - start).days <= 3660:
                raise ValueError('Invalid date interval')
            ranges.append((start, end))
        if periods and not plan['date_column']:
            raise ValueError('Date column required')
        if plan['date_column'] and not periods and not (plan['kind'] == 'group' and plan['group_by'] is None):
            raise ValueError('Unused date column')
        if plan['kind'] == 'compare':
            if len(periods) != 2 or plan['group_by'] is not None:
                raise ValueError('Comparison requires two periods without groups')
            if max(ranges[0][0], ranges[1][0]) < min(ranges[0][1], ranges[1][1]):
                raise ValueError('Overlapping comparison periods')
            years = columns[plan['date_column']]['date_range']['years']
            if len(years) > 1 and not re.search(r'\b(?:19|20)\d{2}\b', question):
                return cls.clarification('El archivo contiene varios años. ¿Qué año quieres comparar?')
        elif len(periods) > 1:
            raise ValueError('Only comparisons accept two periods')
        if plan['kind'] == 'group' and plan['group_by'] is None and plan['date_column'] is None:
            raise ValueError('Missing grouping column')
        if plan['kind'] == 'aggregate' and plan['group_by'] is not None:
            raise ValueError('Unexpected grouping')
        return plan

    @staticmethod
    def clarification(message):
        return {'kind': 'clarify', 'metric': None, 'aggregation': 'count', 'group_by': None,
                'date_column': None, 'granularity': 'month', 'filters': [], 'periods': [],
                'clarification': message.strip()}

    @classmethod
    def execute(cls, dataframe, context, plan):
        if plan['kind'] == 'clarify':
            return {'answer': plan['clarification'], 'evidence': None}
        working = dataframe.copy()
        for column in working.select_dtypes(include='number').columns:
            working[column] = working[column].replace([float('inf'), float('-inf')], float('nan'))
        dates = cls.date_series(working, context)
        for column, parsed in dates.items():
            working[column] = parsed
        metric = plan['metric']
        value_expression = _identifier(metric) if metric is not None else '*'
        aggregations = {'sum': 'sum', 'mean': 'avg', 'min': 'min', 'max': 'max', 'count': 'count', 'unique_count': 'count'}
        if plan['aggregation'] == 'unique_count':
            value_expression = 'DISTINCT ' + value_expression
        expression = f"{aggregations[plan['aggregation']]}({value_expression})"
        conditions, arguments = [], []
        for item in plan['filters']:
            conditions.append(f"{_identifier(item['column'])} {cls.COMPARATORS[item['operator']]} ?")
            value = float(item['value']) if is_numeric_dtype(dataframe[item['column']]) else item['value']
            arguments.append(value)
        def query(period=None, grouped=False):
            predicates, params = list(conditions), list(arguments)
            if grouped and plan['group_by'] is None:
                predicates.append(f"{_identifier(plan['date_column'])} IS NOT NULL")
            if period:
                date_field = _identifier(plan['date_column'])
                predicates.extend([f'{date_field} >= CAST(? AS TIMESTAMP)', f'{date_field} < CAST(? AS TIMESTAMP)'])
                params.extend([period['start'], period['end']])
            where = ' WHERE ' + ' AND '.join(predicates) if predicates else ''
            valid_count = 'count(*)' if metric is None else f'count({_identifier(metric)})'
            selection = f'{expression} AS value, count(*) AS rows, {valid_count} AS values_used'
            if grouped:
                group = _identifier(plan['group_by']) if plan['group_by'] else f"date_trunc('{plan['granularity']}', {_identifier(plan['date_column'])})"
                return connection.execute(
                    f'SELECT {group} AS bucket, {selection} FROM dataset{where} GROUP BY bucket ORDER BY value DESC NULLS LAST, bucket LIMIT 20', params,
                ).fetchall()
            return connection.execute(f'SELECT {selection} FROM dataset{where}', params).fetchone()
        connection = duckdb.connect(config={'enable_external_access': 'false', 'threads': '2', 'memory_limit': '256MB'})
        try:
            connection.register('dataset', working)
            rows = []
            if plan['kind'] == 'compare':
                for period in plan['periods']:
                    value, count, values_used = query(period)
                    rows.append({'label': period['label'], 'value': _number(value), 'rows': count, 'values_used': values_used})
            elif plan['kind'] == 'group':
                for bucket, value, count, values_used in query(plan['periods'][0] if plan['periods'] else None, True):
                    rows.append({'label': str(bucket) if bucket is not None else 'Sin dato',
                                 'value': _number(value), 'rows': count, 'values_used': values_used})
            else:
                value, count, values_used = query(plan['periods'][0] if plan['periods'] else None)
                rows.append({'label': 'Resultado', 'value': _number(value), 'rows': count, 'values_used': values_used})
        finally:
            connection.close()
        label = f"{DashboardService.AGGREGATION_LABELS.get(plan['aggregation'], 'Cantidad')} de {metric or 'registros'}"
        def display(value):
            return 'sin datos utilizables' if value is None else f'{value:,.2f}'
        variation, percentage = None, None
        if plan['kind'] == 'compare':
            first, second = rows
            if first['rows'] and second['rows'] and first['value'] is not None and second['value'] is not None:
                variation = _number(second['value'] - first['value'])
                percentage = _number(variation / abs(first['value']) * 100) if first['value'] else None
                answer = f"{label}: {first['label']} = {display(first['value'])}; {second['label']} = {display(second['value'])}. Variación absoluta: {display(variation)}. "
                answer += f'Variación porcentual: {display(percentage)}%.' if percentage is not None else 'No se calcula porcentaje porque la base es cero.'
            else:
                answer = 'No hay datos suficientes en ambos períodos para calcular la variación. Revisa las fechas y filtros.'
        elif plan['kind'] == 'group':
            answer = f'{label} por {plan["group_by"] or plan["date_column"]}: se muestran como máximo los 20 grupos con mayor valor.' if rows else 'No hay registros para estos filtros.'
        else:
            answer = f"{label}: {display(rows[0]['value'])}." if rows[0]['rows'] else 'No hay registros para estos filtros.'
        return {'answer': answer, 'evidence': {
            'metric': metric, 'aggregation': plan['aggregation'], 'rows': rows,
            'dataset_rows': len(dataframe), 'filters': plan['filters'], 'periods': plan['periods'],
            'group_by': plan['group_by'], 'date_column': plan['date_column'],
            'variation': variation, 'variation_percentage': percentage,
            'date_rows_excluded': int(dates[plan['date_column']].isna().sum()) if plan['date_column'] else 0,
        }}

    @classmethod
    def ask(cls, record, dataframe, context, question, config, provider=None):
        if not isinstance(question, str) or not 0 < len(question.strip()) <= cls.MAX_QUESTION:
            raise ValueError('Escribe una pregunta de hasta 1000 caracteres.')
        question = question.strip()
        catalog = cls.catalog(dataframe, context)
        history = [
            {'question': run.result['question'], 'plan': run.result['plan']}
            for run in cls.history(record) if run.result['question'] != question
        ][-3:]
        payload = {'question': question, 'rows': len(dataframe), 'columns': catalog,
                   'context': {'domain': (context or {}).get('domain'), 'description': (context or {}).get('description')},
                   'history': history}
        def calculate(data):
            plan = cls.validate(data, catalog, question, history)
            try:
                response = cls.execute(dataframe, context, plan)
            except duckdb.Error as error:
                raise AIProviderError('query_execution_error', 'No pudimos calcular esa consulta. Prueba una pregunta más simple.') from error
            return {'question': question, 'dataset_version': record.active_stored_filename,
                    'plan': plan, **response}
        try:
            return StructuredAIService.request(record, config, cls.PURPOSE, cls.VERSION,
                                               cls.INSTRUCTIONS, payload, cls.SCHEMA, calculate, provider)
        except AIProviderError as error:
            if error.code == 'invalid_analysis_output':
                raise AIProviderError(error.code, 'No pudimos interpretar esa pregunta de forma segura. Indica el campo y las fechas o reformula la consulta.') from error
            raise
