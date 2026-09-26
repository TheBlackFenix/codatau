class QualityInsightService:
    """Connects descriptive insights to persisted cleaning decisions."""

    STATUS_LABELS = {
        'pending': 'Pendiente de decisión',
        'partial': 'Parcialmente gestionado',
        'managed': 'Gestionado',
        'kept': 'Conservado por tu decisión',
        'informational': 'Informativo',
    }

    @classmethod
    def decorate(cls, insights, profile, decisions):
        operations = profile.get('cleaning_plan', {}).get('operations', [])
        decision_map = {
            decision.operation_id: decision
            for decision in decisions
            if decision.is_active
        }
        decorated = []
        for insight in insights:
            item = dict(insight)
            operation_ids = cls._operation_ids(item, operations)
            item['operation_ids'] = operation_ids
            item['actionable'] = bool(operation_ids)
            if not operation_ids:
                status = 'informational'
            else:
                resolved = [
                    decision_map[operation_id]
                    for operation_id in operation_ids
                    if operation_id in decision_map
                ]
                if not resolved:
                    status = 'pending'
                elif len(resolved) < len(operation_ids):
                    status = 'partial'
                elif all(decision.choice == 'keep' for decision in resolved):
                    status = 'kept'
                else:
                    status = 'managed'
                item['resolved_count'] = len(resolved)
                item['operation_count'] = len(operation_ids)
            item['status'] = status
            item['status_label'] = cls.STATUS_LABELS[status]
            decorated.append(item)
        return decorated

    @staticmethod
    def _operation_ids(insight, operations):
        code = insight.get('code')
        column = insight.get('column')
        if code == 'null_values':
            return [
                operation['id']
                for operation in operations
                if operation.get('operation') == 'handle_missing'
            ]
        if code == 'duplicate_rows':
            return [
                operation['id']
                for operation in operations
                if operation.get('operation') == 'remove_exact_duplicates'
            ]
        if code == 'negative_values' and column:
            return [
                operation['id']
                for operation in operations
                if operation.get('operation') == 'validate_range'
                and operation.get('column') == column
            ]
        return []
