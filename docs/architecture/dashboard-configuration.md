# Configuración persistente del dashboard

Las métricas visibles se guardan por usuario y archivo en
`DashboardConfiguration`. Cada elemento conserva la columna, el cálculo y su
posición. Una lista vacía también es una decisión válida, por lo que se distingue
de la ausencia de configuración; en ese último caso se muestran las métricas de
negocio sugeridas por el perfil.

La misma cuadrícula se reutiliza en resultados, análisis y dashboard. Agregar,
quitar o reordenar una tarjeta actualiza inmediatamente la configuración mediante
un endpoint autenticado y protegido por CSRF. El servidor vuelve a validar que la
columna sea numérica y que el cálculo pertenezca al catálogo permitido.

Las gráficas no comparan columnas con unidades distintas. Priorizan:

1. Una serie temporal basada en la primera métrica configurada que admita suma,
   promedio, mínimo o máximo. La granularidad es mensual, semanal, diaria u
   horaria según el rango; nunca baja a minutos o segundos.
2. Una agrupación categórica de baja cardinalidad, limitada a los diez grupos
   principales.
3. Las diez columnas con más valores nulos.

Si el usuario no conserva una métrica agregable, las gráficas muestran cantidad
de registros en lugar de inventar una suma sobre identificadores.
