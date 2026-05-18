import pandas as pd


class ValidationService:

    @staticmethod
    def validate_file(df):
        errors = []
        warnings = []

        # Archivo vacío
        if df.empty:
            errors.append('El archivo está vacío.')
            return errors, warnings

        # Filas completamente vacías
        empty_rows = df.isnull().all(axis=1).sum()
        if empty_rows > 0:
            warnings.append(f'Se encontraron {empty_rows} fila(s) completamente vacías.')

        # Columnas con todos los valores nulos
        null_cols = [col for col in df.columns if df[col].isnull().all()]
        if null_cols:
            warnings.append(f'Las columnas {null_cols} están completamente vacías.')

        # Duplicados
        duplicates = df.duplicated().sum()
        if duplicates > 0:
            warnings.append(f'Se encontraron {duplicates} fila(s) duplicada(s).')

        # Valores nulos por columna
        null_counts = df.isnull().sum()
        for col, count in null_counts.items():
            if 0 < count < len(df):
                warnings.append(f'La columna "{col}" tiene {count} valor(es) nulo(s).')

        return errors, warnings