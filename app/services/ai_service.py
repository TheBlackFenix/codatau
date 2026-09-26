class AIService:

    @staticmethod
    def generate_display_insights(df, summary):
        """Return current rule results using the shape expected by templates."""
        return [
            {
                'insight_type': insight['type'],
                'message': insight['message'],
                'code': insight.get('code', 'informational'),
                'column': insight.get('column'),
            }
            for insight in AIService.generate_insights(df, summary)
        ]

    @staticmethod
    def generate_insights(df, summary):
        insights = []

        rows = summary['rows']
        null_total = summary['null_total']
        duplicates = summary['duplicates']

        # Insights sobre calidad de datos
        if null_total == 0 and duplicates == 0:
            insights.append({
                'type': 'success',
                'code': 'clean_dataset',
                'message': 'Los datos están limpios: no se encontraron valores nulos ni duplicados.'
            })

        if null_total > 0:
            total_cells = rows * len(df.columns)
            pct = round((null_total / total_cells) * 100, 1) if total_cells else 0
            insights.append({
                'type': 'warning',
                'code': 'null_values',
                'message': f'Se encontraron {null_total} valores nulos ({pct}% del total de celdas).'
            })

        if duplicates > 0:
            insights.append({
                'type': 'danger',
                'code': 'duplicate_rows',
                'message': f'Hay {duplicates} filas duplicadas que pueden afectar el análisis.'
            })

        # Insights sobre columnas numéricas
        numeric_summary = summary.get('numeric_summary', {})
        recommended_metrics = set(
            summary.get('recommended_metrics', numeric_summary)
        )
        for col, stats in numeric_summary.items():
            if col not in recommended_metrics:
                continue
            mean = stats['mean']
            max_val = stats['max']
            min_val = stats['min']

            if max_val is not None and mean is not None and max_val > 0 and mean > 0:
                if max_val > mean * 5:
                    insights.append({
                        'type': 'warning',
                        'code': 'extreme_values',
                        'column': col,
                        'message': f'La columna "{col}" tiene valores extremos: máximo {max_val} vs promedio {mean}.'
                    })

            if min_val is not None and min_val < 0:
                insights.append({
                    'type': 'info',
                    'code': 'negative_values',
                    'column': col,
                    'message': f'La columna "{col}" tiene valores negativos (mínimo: {min_val}).'
                })

        if rows < 10:
            insights.append({
                'type': 'info',
                'code': 'small_dataset',
                'message': f'El archivo tiene muy pocas filas ({rows}). Los análisis pueden no ser representativos.'
            })

        if not insights:
            insights.append({
                'type': 'info',
                'code': 'analysis_complete',
                'message': 'Análisis completado. No se detectaron anomalías significativas.'
            })

        return insights
