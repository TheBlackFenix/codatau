from datetime import datetime, timezone

from app.extensions import db
from app.models.cleaning_decision import CleaningDecision


class CleaningDecisionService:
    """Persists explicit user outcomes for proposed cleaning operations."""

    @staticmethod
    def backfill_applied(record):
        """Import operations from existing versions without overriding reopenings."""
        existing_ids = {
            decision.operation_id
            for decision in CleaningDecision.query.filter_by(file_id=record.id).all()
        }
        created = 0
        for version in sorted(record.versions, key=lambda item: item.version_number):
            for operation in version.operations or []:
                operation_id = operation.get('id')
                if not operation_id or operation_id in existing_ids:
                    continue
                db.session.add(
                    CleaningDecision(
                        file_id=record.id,
                        decided_by=version.created_by,
                        operation_id=operation_id,
                        operation=operation.get('operation', 'unknown'),
                        column_name=operation.get('column'),
                        choice='apply',
                        parameters=operation.get('parameters') or {},
                        affected_rows=int(operation.get('affected_rows') or 0),
                        reason=operation.get('reason'),
                        applied_version_number=version.version_number,
                        is_active=True,
                    )
                )
                existing_ids.add(operation_id)
                created += 1
        return created

    @staticmethod
    def active_for_file(file_id):
        return (
            CleaningDecision.query
            .filter_by(file_id=file_id, is_active=True)
            .order_by(CleaningDecision.updated_at.desc())
            .all()
        )

    @staticmethod
    def save(record, user_id, decisions, applied_version_number=None):
        existing = {
            decision.operation_id: decision
            for decision in CleaningDecision.query.filter_by(file_id=record.id).all()
        }
        now = datetime.now(timezone.utc)
        for item in decisions:
            decision = existing.get(item['operation_id'])
            if decision is None:
                decision = CleaningDecision(
                    file_id=record.id,
                    operation_id=item['operation_id'],
                )
                db.session.add(decision)
            decision.decided_by = user_id
            decision.operation = item['operation']
            decision.column_name = item.get('column')
            decision.choice = item['choice']
            decision.parameters = item.get('parameters') or {}
            decision.affected_rows = int(item.get('affected_rows') or 0)
            decision.reason = item.get('reason')
            decision.applied_version_number = (
                applied_version_number if item['choice'] == 'apply' else None
            )
            decision.is_active = True
            decision.updated_at = now

    @staticmethod
    def reopen(decision):
        decision.is_active = False
        decision.updated_at = datetime.now(timezone.utc)
