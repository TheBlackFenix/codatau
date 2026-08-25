class AIService:

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
                'message': 'Los datos están limpios: no se encontraron valores nulos ni duplicados.'
            })

        if null_total > 0:
            total_cells = rows * len(df.columns)
            pct = round((null_total / total_cells) * 100, 1) if total_cells else 0
            insights.append({
                'type': 'warning',
                'message': f'Se encontraron {null_total} valores nulos ({pct}% del total de celdas).'
            })

        if duplicates > 0:
            insights.append({
                'type': 'danger',
                'message': f'Hay {duplicates} filas duplicadas que pueden afectar el análisis.'
            })

        # Insights sobre columnas numéricas
        numeric_summary = summary.get('numeric_summary', {})
        for col, stats in numeric_summary.items():
            mean = stats['mean']
            max_val = stats['max']
            min_val = stats['min']

            if max_val is not None and mean is not None and max_val > 0 and mean > 0:
                if max_val > mean * 5:
                    insights.append({
                        'type': 'warning',
                        'message': f'La columna "{col}" tiene valores extremos: máximo {max_val} vs promedio {mean}.'
                    })

            if min_val is not None and min_val < 0:
                insights.append({
                    'type': 'info',
                    'message': f'La columna "{col}" tiene valores negativos (mínimo: {min_val}).'
                })

        if rows < 10:
            insights.append({
                'type': 'info',
                'message': f'El archivo tiene muy pocas filas ({rows}). Los análisis pueden no ser representativos.'
            })

        if not insights:
            insights.append({
                'type': 'info',
                'message': 'Análisis completado. No se detectaron anomalías significativas.'
            })

        return insights
