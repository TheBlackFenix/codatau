import pandas as pd
import os


class DataService:

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
                    'mean':  round(float(numeric_df[col].mean()), 2),
                    'max':   round(float(numeric_df[col].max()), 2),
                    'min':   round(float(numeric_df[col].min()), 2),
                    'sum':   round(float(numeric_df[col].sum()), 2),
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
        # Eliminar filas completamente vacías
        df = df.dropna(how='all')
        # Eliminar columnas completamente vacías
        df = df.dropna(axis=1, how='all')
        # Strip en columnas de texto
        for col in df.select_dtypes(include='object').columns:
            df[col] = df[col].str.strip()
        return df