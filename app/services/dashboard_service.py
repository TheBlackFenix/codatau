import math
import re

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype

from app.extensions import db
from app.models.dashboard_configuration import DashboardConfiguration


class DashboardConfigurationError(ValueError):
    pass


class DashboardService:
    AGGREGATION_LABELS = {
        'sum': 'Suma total',
        'mean': 'Promedio',
        'min': 'Mínimo',
        'max': 'Máximo',
        'non_null_count': 'Registros con dato',
        'completeness': 'Porcentaje con dato',
        'unique_count': 'Valores únicos',
        'unique_percentage': 'Valores únicos / total de filas',
    }
    MAX_METRICS = 24
    DATE_HINT = re.compile(r'(^|[_\W])(fecha|date|timestamp|hora|time)($|[_\W])', re.I)

    @classmethod
    def default_layout(cls, summary):
        return [
            {'column': column, 'aggregation': 'sum'}
            for column in summary['default_metrics']
        ]

    @classmethod
    def validate_layout(cls, layout, summary):
        if not isinstance(layout, list):
            raise DashboardConfigurationError('La configuración de métricas no es válida.')
        if len(layout) > cls.MAX_METRICS:
            raise DashboardConfigurationError(
                f'Puedes mostrar hasta {cls.MAX_METRICS} métricas por archivo.'
            )

        catalog = summary['numeric_summary']
        validated = []
        seen = set()
        for item in layout:
            if not isinstance(item, dict):
                raise DashboardConfigurationError('Una métrica no tiene el formato esperado.')
            column = item.get('column')
            aggregation = item.get('aggregation')
            if column not in catalog:
                raise DashboardConfigurationError(
                    f'La columna “{column}” ya no está disponible como métrica.'
                )
            if aggregation not in cls.AGGREGATION_LABELS:
                raise DashboardConfigurationError('El cálculo seleccionado no es válido.')
            key = (column, aggregation)
            if key in seen:
                continue
            seen.add(key)
            validated.append({'column': column, 'aggregation': aggregation})
        return validated

    @classmethod
    def layout_for(cls, record, summary):
        configuration = DashboardConfiguration.query.filter_by(
            user_id=record.user_id,
            file_id=record.id,
        ).first()
        if configuration is None:
            return cls.default_layout(summary)

        # A cleaned version can remove or change columns. Ignore stale cards while
        # preserving the user's explicit choice (including an empty dashboard).
        catalog = summary['numeric_summary']
        return [
            item
            for item in (configuration.metrics or [])
            if isinstance(item, dict)
            and item.get('column') in catalog
            and item.get('aggregation') in cls.AGGREGATION_LABELS
        ]

    @classmethod
    def cards_for(cls, layout, summary):
        catalog = summary['numeric_summary']
        cards = []
        for item in layout:
            column = item['column']
            aggregation = item['aggregation']
            stats = catalog[column]
            cards.append({
                'column': column,
                'aggregation': aggregation,
                'label': cls.AGGREGATION_LABELS[aggregation],
                'value': stats.get(aggregation),
                'completeness': stats.get('completeness'),
                'role': stats.get('role'),
            })
        return cards

    @classmethod
    def save_layout(cls, record, user_id, layout, summary):
        validated = cls.validate_layout(layout, summary)
        configuration = DashboardConfiguration.query.filter_by(
            user_id=user_id,
            file_id=record.id,
        ).first()
        if configuration is None:
            configuration = DashboardConfiguration(
                user_id=user_id,
                file_id=record.id,
            )
            db.session.add(configuration)
        configuration.metrics = validated
        return validated

    @classmethod
    def build_charts(cls, dataframe, summary, layout):
        charts = {}
        preferred = cls._preferred_measure(dataframe, layout)
        date_column, dates = cls._date_series(dataframe)

        if date_column is not None:
            timeline = cls._timeline_chart(dataframe, date_column, dates, preferred)
            if timeline:
                charts['timeline'] = timeline

        category = cls._category_chart(
            dataframe,
            preferred,
            excluded={date_column} if date_column else set(),
        )
        if category:
            charts['category'] = category

        nulls = sorted(
            (
                (str(column), int(count))
                for column, count in dataframe.isnull().sum().items()
                if count > 0
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )[:10]
        if nulls:
            charts['nulls'] = {
                'labels': [label for label, _ in nulls],
                'datos': [value for _, value in nulls],
            }
        return charts

    @staticmethod
    def _preferred_measure(dataframe, layout):
        for item in layout:
            column = item['column']
            aggregation = item['aggregation']
            if column in dataframe and aggregation in {'sum', 'mean', 'min', 'max'}:
                return column, aggregation
        return None

    @classmethod
    def _date_series(cls, dataframe):
        candidates = []
        for column in dataframe.columns:
            series = dataframe[column]
            normalized_name = re.sub(
                r'([a-z0-9])([A-Z])',
                r'\1_\2',
                str(column),
            ).replace('-', '_')
            hinted = bool(cls.DATE_HINT.search(normalized_name))
            if is_datetime64_any_dtype(series.dtype):
                parsed = pd.to_datetime(series, errors='coerce')
            elif hinted:
                try:
                    parsed = pd.to_datetime(series, errors='coerce', format='mixed')
                except (TypeError, ValueError):
                    parsed = pd.to_datetime(series, errors='coerce')
            else:
                continue
            valid = int(parsed.notna().sum())
            if valid >= 2 and valid / max(len(series), 1) >= 0.6:
                candidates.append((valid, column, parsed))
        if not candidates:
            return None, None
        _, column, parsed = max(candidates, key=lambda item: item[0])
        return column, parsed

    @classmethod
    def _timeline_chart(cls, dataframe, date_column, dates, preferred):
        working = pd.DataFrame({'_date': dates}).dropna()
        if working.empty:
            return None
        span = working['_date'].max() - working['_date'].min()
        if span >= pd.Timedelta(days=90):
            working['_bucket'] = working['_date'].dt.to_period('M').dt.start_time
            period_label = 'mes'
            label_format = '%Y-%m'
        elif span >= pd.Timedelta(days=45):
            working['_bucket'] = working['_date'].dt.to_period('W').dt.start_time
            period_label = 'semana'
            label_format = '%d/%m/%Y'
        elif span >= pd.Timedelta(days=1):
            working['_bucket'] = working['_date'].dt.floor('D')
            period_label = 'día'
            label_format = '%d/%m/%Y'
        else:
            working['_bucket'] = working['_date'].dt.floor('h')
            period_label = 'hora'
            label_format = '%d/%m/%Y %H:00'

        value_label = 'Registros'
        if preferred:
            column, aggregation = preferred
            working['_value'] = pd.to_numeric(
                dataframe.loc[working.index, column],
                errors='coerce',
            )
            grouped = working.groupby('_bucket')['_value'].agg(aggregation).dropna()
            value_label = f'{cls.AGGREGATION_LABELS[aggregation]} de {column}'
        else:
            grouped = working.groupby('_bucket').size()

        pairs = [
            (bucket.strftime(label_format), round(float(value), 2))
            for bucket, value in grouped.sort_index().items()
            if math.isfinite(float(value))
        ]
        if not pairs:
            return None
        return {
            'labels': [label for label, _ in pairs],
            'datos': [value for _, value in pairs],
            'date_col': str(date_column),
            'period': period_label,
            'value_label': value_label,
        }

    @classmethod
    def _category_chart(cls, dataframe, preferred, excluded):
        candidates = []
        row_count = max(len(dataframe), 1)
        for column in dataframe.columns:
            if column in excluded:
                continue
            series = dataframe[column]
            if not (series.dtype == 'object' or pd.api.types.is_string_dtype(series.dtype)):
                continue
            unique = int(series.nunique(dropna=True))
            if 2 <= unique <= min(20, max(2, row_count // 2)):
                candidates.append((unique, column))
        if not candidates:
            return None
        _, group_column = min(candidates, key=lambda item: item[0])

        working = dataframe.dropna(subset=[group_column]).copy()
        value_label = 'Registros'
        if preferred:
            metric_column, aggregation = preferred
            working[metric_column] = pd.to_numeric(working[metric_column], errors='coerce')
            grouped = (
                working.groupby(group_column, dropna=True)[metric_column]
                .agg(aggregation)
                .dropna()
            )
            value_label = f'{cls.AGGREGATION_LABELS[aggregation]} de {metric_column}'
        else:
            grouped = working.groupby(group_column, dropna=True).size()
        grouped = grouped.sort_values(ascending=False).head(10)
        pairs = [
            (str(label), round(float(value), 2))
            for label, value in grouped.items()
            if math.isfinite(float(value))
        ]
        if not pairs:
            return None
        return {
            'labels': [label for label, _ in pairs],
            'datos': [value for _, value in pairs],
            'group_col': str(group_column),
            'value_label': value_label,
        }
