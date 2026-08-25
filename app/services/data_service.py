import csv
import os
import math
from pathlib import Path
from zipfile import BadZipFile

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException
from pandas.errors import EmptyDataError, ParserError
from pandas.api.types import is_object_dtype, is_string_dtype
from xlrd.biffh import XLRDError


CSV_ENCODINGS = ('utf-8-sig', 'cp1252', 'latin-1')
CSV_DELIMITERS = ',;\t|'


class FileReadError(ValueError):
    """Expected input error with a safe, actionable message for the user."""

    def __init__(self, code, message, hint):
        super().__init__(message)
        self.code = code
        self.user_message = message
        self.hint = hint


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
        path = Path(filepath)
        ext = path.suffix.lower()
        if not path.exists() or path.stat().st_size == 0:
            raise FileReadError(
                'empty_file',
                'El archivo está vacío.',
                'Abre el archivo y confirma que tenga encabezados y al menos una fila.',
            )
        if ext == '.csv':
            return DataService._read_csv(path)
        elif ext in ['.xlsx', '.xls']:
            return DataService._read_excel(path, ext)
        raise ValueError(f'Formato no soportado: {ext}')

    @staticmethod
    def _read_csv(path):
        with path.open('rb') as source_file:
            signature = source_file.read(4)
            source_file.seek(0)
            sample_bytes = source_file.read(65536)

        if signature.startswith(b'PK'):
            raise FileReadError(
                'extension_mismatch',
                'El contenido parece ser un archivo Excel, pero la extensión es .csv.',
                'Guárdalo como CSV real o cambia la extensión a .xlsx antes de subirlo.',
            )

        decoded_sample = None
        encoding = None
        for candidate in CSV_ENCODINGS:
            try:
                decoded_sample = sample_bytes.decode(candidate)
                encoding = candidate
                break
            except UnicodeDecodeError:
                continue

        if decoded_sample is None:
            raise FileReadError(
                'unsupported_encoding',
                'No fue posible reconocer la codificación del archivo CSV.',
                'Guárdalo como CSV UTF-8 e intenta nuevamente.',
            )

        try:
            dialect = csv.Sniffer().sniff(decoded_sample, delimiters=CSV_DELIMITERS)
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ','

        try:
            return pd.read_csv(path, encoding=encoding, sep=delimiter)
        except EmptyDataError as exc:
            raise FileReadError(
                'empty_file',
                'El archivo está vacío o no contiene encabezados reconocibles.',
                'La primera fila debe contener los nombres de las columnas.',
            ) from exc
        except ParserError as exc:
            raise FileReadError(
                'malformed_csv',
                'El CSV tiene filas con diferente número de columnas o comillas sin cerrar.',
                'Revísalo en un editor de texto o vuelve a exportarlo como CSV UTF-8.',
            ) from exc

    @staticmethod
    def _read_excel(path, extension):
        try:
            return pd.read_excel(str(path))
        except (BadZipFile, InvalidFileException, XLRDError, ValueError, KeyError) as exc:
            expected_format = 'Excel moderno (.xlsx)' if extension == '.xlsx' else 'Excel 97-2003 (.xls)'
            raise FileReadError(
                'invalid_excel',
                f'El contenido no corresponde a un archivo {expected_format} válido.',
                'Ábrelo en Excel o LibreOffice y usa “Guardar como” antes de volver a subirlo.',
            ) from exc

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
