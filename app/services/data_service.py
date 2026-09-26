import csv
import math
import re
import unicodedata
from pathlib import Path
from zipfile import BadZipFile

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException
from pandas.errors import EmptyDataError, ParserError
from pandas.api.types import is_object_dtype, is_string_dtype
from xlrd.biffh import XLRDError


CSV_ENCODINGS = ('utf-8-sig', 'cp1252', 'latin-1')
CSV_DELIMITERS = ',;\t|'

MEASURE_HINTS = {
    'amount',
    'alto',
    'ancho',
    'cantidad',
    'coste',
    'costo',
    'cost',
    'descuento',
    'discount',
    'duracion',
    'duration',
    'egreso',
    'flete',
    'gasto',
    'gastos',
    'importe',
    'impuesto',
    'income',
    'ingreso',
    'height',
    'largo',
    'length',
    'margen',
    'margin',
    'monto',
    'peso',
    'price',
    'pieza',
    'piezas',
    'precio',
    'profit',
    'qty',
    'quantity',
    'saldo',
    'seguro',
    'tarifa',
    'tiempo',
    'time',
    'total',
    'unidad',
    'unidades',
    'utilidad',
    'valor',
    'venta',
    'ventas',
    'volumen',
    'volume',
    'weight',
    'width',
}

IDENTIFIER_HINTS = {
    'cedula',
    'celular',
    'codigo',
    'documento',
    'guia',
    'id',
    'identificacion',
    'nit',
    'phone',
    'postal',
    'sku',
    'telefono',
    'zip',
}

NUMBER_IDENTIFIER_CONTEXT = {
    'contrato',
    'convenio',
    'cuenta',
    'factura',
    'guia',
    'identificacion',
    'orden',
}


def _column_tokens(column_name):
    separated = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', str(column_name))
    normalized = unicodedata.normalize('NFKD', separated)
    ascii_name = ''.join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return set(filter(None, re.split(r'[^a-z0-9]+', ascii_name.lower())))


def classify_numeric_column(column_name):
    """Classify a physical number without assuming it is a business measure."""
    tokens = _column_tokens(column_name)
    if tokens & IDENTIFIER_HINTS:
        return 'identifier'
    if 'numero' in tokens and tokens & NUMBER_IDENTIFIER_CONTEXT:
        return 'identifier'
    if tokens & MEASURE_HINTS:
        return 'measure'
    return 'optional'


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
        numeric_cols = list(df.select_dtypes(include='number').columns)
        numeric_roles = {
            column: classify_numeric_column(column)
            for column in numeric_cols
        }
        recommended_metrics = [
            column
            for column in numeric_cols
            if numeric_roles[column] == 'measure'
        ]
        identifier_cols = [
            column
            for column in numeric_cols
            if numeric_roles[column] == 'identifier'
        ]
        optional_numeric_cols = [
            column
            for column in numeric_cols
            if numeric_roles[column] == 'optional'
        ]
        summary = {
            'rows':        len(df),
            'columns':     len(df.columns),
            'column_names': list(df.columns),
            'numeric_cols': numeric_cols,
            'recommended_metrics': recommended_metrics,
            'default_metrics': recommended_metrics[:6],
            'identifier_cols': identifier_cols,
            'optional_numeric_cols': optional_numeric_cols,
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
                    'non_null_count': int(numeric_df[col].notna().sum()),
                    'missing_count': int(numeric_df[col].isna().sum()),
                    'unique_count': int(numeric_df[col].nunique(dropna=True)),
                    'unique_percentage': round(
                        numeric_df[col].nunique(dropna=True) / len(df) * 100,
                        1,
                    ) if len(df) else 0.0,
                    'completeness': round(
                        numeric_df[col].notna().sum() / len(df) * 100,
                        1,
                    ) if len(df) else 0.0,
                    'role': numeric_roles[col],
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
