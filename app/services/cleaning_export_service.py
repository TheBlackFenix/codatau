import csv
import io
import json
from zipfile import ZIP_DEFLATED, ZipFile


class CleaningExportService:
    """Creates a portable quality package for one materialized version."""

    @classmethod
    def build(cls, record, version, cleaned, quarantine, decisions):
        output = io.BytesIO()
        with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                'datos_limpios.csv',
                cleaned.to_csv(index=False).encode('utf-8-sig'),
            )
            if quarantine is not None and not quarantine.empty:
                archive.writestr(
                    'cuarentena.csv',
                    quarantine.to_csv(index=False).encode('utf-8-sig'),
                )
            archive.writestr(
                'decisiones.csv',
                cls._decisions_csv(decisions).encode('utf-8-sig'),
            )
            archive.writestr(
                'reporte_calidad.json',
                json.dumps(
                    cls._report(record, version, decisions),
                    ensure_ascii=False,
                    indent=2,
                ).encode('utf-8'),
            )
            archive.writestr(
                'LEEME.txt',
                (
                    'Paquete de calidad generado por CoDataU.\n'
                    'datos_limpios.csv: resultado de la versión.\n'
                    'cuarentena.csv: filas separadas, si existen.\n'
                    'decisiones.csv: auditoría de reglas aplicadas.\n'
                    'reporte_calidad.json: métricas y configuración reproducible.\n'
                ).encode('utf-8'),
            )
        output.seek(0)
        return output

    @staticmethod
    def _report(record, version, decisions):
        return {
            'report_version': '1.0',
            'file': {
                'id': record.id,
                'original_name': record.original_name,
            },
            'dataset_version': {
                'number': version.version_number,
                'created_at': version.created_at.isoformat(),
                'created_by': version.created_by,
                'is_active': version.is_active,
            },
            'metrics': version.metrics or {},
            'operations': version.operations or [],
            'decisions': [
                {
                    'operation_id': decision.operation_id,
                    'operation': decision.operation,
                    'column': decision.column_name,
                    'choice': decision.choice,
                    'parameters': decision.parameters or {},
                    'affected_rows': decision.affected_rows,
                    'reason': decision.reason,
                    'decided_by': decision.decider.username,
                    'decided_at': decision.updated_at.isoformat(),
                }
                for decision in decisions
            ],
        }

    @staticmethod
    def _decisions_csv(decisions):
        output = io.StringIO(newline='')
        fieldnames = [
            'operation_id',
            'operation',
            'column',
            'choice',
            'parameters',
            'affected_rows',
            'reason',
            'decided_by',
            'decided_at',
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for decision in decisions:
            writer.writerow({
                'operation_id': decision.operation_id,
                'operation': decision.operation,
                'column': decision.column_name or '',
                'choice': decision.choice,
                'parameters': json.dumps(
                    decision.parameters or {},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                'affected_rows': decision.affected_rows,
                'reason': decision.reason or '',
                'decided_by': decision.decider.username,
                'decided_at': decision.updated_at.isoformat(),
            })
        return output.getvalue()
