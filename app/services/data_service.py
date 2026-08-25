import pandas as pd
import os
import math
from pandas.api.types import is_object_dtype, is_string_dtype


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if math.isfinite(number) else None


class DataService:

    @staticmethod
    def text_columns(df):
        return [
            col for col in df.columns
            if is_object_dtype(df[col].dtype) or is_string_dtype(df[col].dtype)
        ]

    @staticmethod
    def read_file(filepath):
        ext = os.path.splitext(filepath)[1].lower()
        if ext == '.csv':
            try:
                return pd.read_csv(filepath, encoding='utf-8')
            except UnicodeDecodeError:
                return pd.read_csv(filepath, encoding='latin-1')
        elif ext in ['.xlsx', '.xls']:
            return pd.read_excel(filepath)
        raise ValueError(f'Formato no soportado: {ext}')

    @staticmethod
    def get_summary(df):
        summary = {
            'rows':        len(df),
            'columns':     len(df.columns),
            'column_names': list(df.columns),
            'numeric_cols': list(df.select_dtypes(include='number').columns),
            'null_total':  int(df.isnull().sum().sum()),
            'duplicates':  int(df.duplicated().sum()),
        }

        # Métricas de columnas numéricas
        numeric_df = df.select_dtypes(include='number')
        if not numeric_df.empty:
            summary['numeric_summary'] = {
                col: {
                    'mean': _finite_number(numeric_df[col].mean()),
                    'max': _finite_number(numeric_df[col].max()),
                    'min': _finite_number(numeric_df[col].min()),
                    'sum': _finite_number(numeric_df[col].sum()),
                }
                for col in numeric_df.columns
            }
        else:
            summary['numeric_summary'] = {}

        # Preview: primeras 10 filas como lista de dicts
        summary['preview'] = df.head(10).fillna('').to_dict(orient='records')

        return summary

    @staticmethod
    def clean_dataframe(df):
        df = df.copy()
        # Eliminar filas completamente vacías
        df = df.dropna(how='all')
        # Eliminar columnas completamente vacías
        df = df.dropna(axis=1, how='all')
        # Limpiar solo valores de texto sin alterar tipos mixtos.
        for col in DataService.text_columns(df):
            df[col] = df[col].map(
                lambda value: value.strip() if isinstance(value, str) else value
            )
        return df
