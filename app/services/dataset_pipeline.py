import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from app.services.semantic_profiler import (
    NUMERIC_TYPE_PREFIXES,
    SemanticProfiler,
    build_cleaning_plan,
)


PROFILE_VERSION = '1.1'


@dataclass(frozen=True)
class DatasetArtifact:
    parquet_path: Path
    profile_path: Path
    profile: dict


def _quoted_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, 'item'):
        return _json_value(value.item())
    return value


class DatasetPipeline:
    """Creates portable analytical artifacts and compact AI-ready profiles."""

    def __init__(self, analytics_folder, sample_size=12):
        self.analytics_folder = Path(analytics_folder).resolve()
        self.analytics_folder.mkdir(parents=True, exist_ok=True)
        self.sample_size = max(1, int(sample_size))

    def paths_for(self, stored_filename):
        safe_name = Path(stored_filename).name
        if safe_name != stored_filename:
            raise ValueError('El nombre almacenado no puede contener rutas.')
        stem = Path(safe_name).stem
        return (
            self.analytics_folder / f'{stem}.parquet',
            self.analytics_folder / f'{stem}.profile.json',
        )

    def quarantine_path_for(self, stored_filename):
        parquet_path, _ = self.paths_for(stored_filename)
        return parquet_path.with_name(f'{parquet_path.stem}.quarantine.parquet')

    def ingest_dataframe(self, dataframe, stored_filename, source_path):
        if dataframe.empty or len(dataframe.columns) == 0:
            raise ValueError('No hay datos utilizables para crear el artefacto analítico.')

        parquet_path, profile_path = self.paths_for(stored_filename)
        temporary_parquet = parquet_path.with_name(
            f'.{parquet_path.name}.{uuid.uuid4().hex}.tmp'
        )

        try:
            connection = duckdb.connect()
            try:
                relation = connection.from_df(dataframe)
                relation.write_parquet(
                    str(temporary_parquet),
                    compression='zstd',
                )
            finally:
                connection.close()

            os.replace(temporary_parquet, parquet_path)
            profile = self._build_profile(parquet_path, source_path)
            self._write_json_atomic(profile_path, profile)
            return DatasetArtifact(parquet_path, profile_path, profile)
        finally:
            if temporary_parquet.exists():
                temporary_parquet.unlink()

    def load_dataframe(self, stored_filename):
        parquet_path, _ = self.paths_for(stored_filename)
        if not parquet_path.exists():
            return None
        connection = duckdb.connect()
        try:
            return connection.read_parquet(str(parquet_path)).df()
        finally:
            connection.close()

    def load_quarantine_dataframe(self, stored_filename):
        quarantine_path = self.quarantine_path_for(stored_filename)
        if not quarantine_path.exists():
            return None
        connection = duckdb.connect()
        try:
            return connection.read_parquet(str(quarantine_path)).df()
        finally:
            connection.close()

    def load_dataframe_or_source(self, stored_filename, source_path):
        dataframe = self.load_dataframe(stored_filename)
        if dataframe is not None:
            return dataframe

        from app.services.data_service import DataService

        dataframe = DataService.read_file(source_path)
        return DataService.clean_dataframe(dataframe)

    def load_profile(self, stored_filename):
        _, profile_path = self.paths_for(stored_filename)
        if not profile_path.exists():
            return None
        with profile_path.open('r', encoding='utf-8') as profile_file:
            return json.load(profile_file)

    def profile_existing(self, stored_filename, source_path):
        parquet_path, profile_path = self.paths_for(stored_filename)
        if not parquet_path.exists():
            raise FileNotFoundError(parquet_path)
        profile = self._build_profile(parquet_path, source_path)
        self._write_json_atomic(profile_path, profile)
        return DatasetArtifact(parquet_path, profile_path, profile)

    def remove_artifacts(self, stored_filename):
        for path in self.paths_for(stored_filename):
            if path.exists():
                path.unlink()
        quarantine_path = self.quarantine_path_for(stored_filename)
        if quarantine_path.exists():
            quarantine_path.unlink()

    def _build_profile(self, parquet_path, source_path):
        connection = duckdb.connect()
        try:
            relation = connection.read_parquet(str(parquet_path))
            column_names = relation.columns
            column_types = [str(column_type) for column_type in relation.types]
            row_count = relation.aggregate('count(*) AS row_count').fetchone()[0]

            aggregate_parts = []
            for index, name in enumerate(column_names):
                identifier = _quoted_identifier(name)
                aggregate_parts.extend(
                    [
                        f'count(*) - count({identifier}) AS nulls_{index}',
                        f'approx_count_distinct({identifier}) AS unique_{index}',
                    ]
                )
            aggregate_values = (
                relation.aggregate(', '.join(aggregate_parts)).fetchone()
                if aggregate_parts
                else ()
            )

            semantic_profiler = SemanticProfiler(connection, parquet_path)
            columns = []
            column_operations = []
            total_nulls = 0
            for index, (name, type_name) in enumerate(zip(column_names, column_types)):
                null_count = int(aggregate_values[index * 2] or 0)
                unique_count = int(aggregate_values[index * 2 + 1] or 0)
                total_nulls += null_count
                column_profile = {
                    'name': name,
                    'type': type_name,
                    'null_count': null_count,
                    'null_ratio': round(null_count / row_count, 4) if row_count else 0,
                    'approx_unique': unique_count,
                }
                if type_name.startswith(NUMERIC_TYPE_PREFIXES):
                    identifier = _quoted_identifier(name)
                    minimum, maximum, mean = relation.aggregate(
                        f'min({identifier}), max({identifier}), avg({identifier})'
                    ).fetchone()
                    column_profile['numeric'] = {
                        'min': _json_value(minimum),
                        'max': _json_value(maximum),
                        'mean': _json_value(mean),
                    }
                semantic, operations = semantic_profiler.analyze(
                    name,
                    type_name,
                    row_count,
                )
                column_profile['semantic'] = semantic
                column_operations.append(operations)
                columns.append(column_profile)

            distinct_rows = connection.execute(
                'SELECT count(*) FROM (SELECT DISTINCT * FROM read_parquet(?))',
                [str(parquet_path)],
            ).fetchone()[0]
            sample = self._sample(connection, relation, parquet_path, row_count)

            duplicate_rows = int(row_count - distinct_rows)
            return {
                'profile_version': PROFILE_VERSION,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'source_sha256': self._sha256(source_path),
                'row_count': int(row_count),
                'column_count': len(column_names),
                'null_count': total_nulls,
                'duplicate_rows': duplicate_rows,
                'columns': columns,
                'sample': sample,
                'cleaning_plan': build_cleaning_plan(
                    column_operations,
                    duplicate_rows,
                ),
            }
        finally:
            connection.close()

    def _sample(self, connection, relation, parquet_path, row_count):
        if row_count <= self.sample_size:
            sample_frame = relation.limit(self.sample_size).df()
        else:
            sample_frame = connection.execute(
                'SELECT * FROM read_parquet(?) '
                f'USING SAMPLE reservoir({self.sample_size} ROWS) REPEATABLE (42)',
                [str(parquet_path)],
            ).df()
        return json.loads(sample_frame.to_json(orient='records', date_format='iso'))

    @staticmethod
    def _sha256(source_path):
        digest = hashlib.sha256()
        with Path(source_path).open('rb') as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _write_json_atomic(destination, payload):
        temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
