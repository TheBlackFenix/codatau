# Configuración persistente del dashboard

Las métricas visibles se guardan por usuario y archivo en
`DashboardConfiguration`. Cada elemento conserva la columna, el cálculo y su
posición. Una lista vacía también es una decisión válida, por lo que se distingue
de la ausencia de configuración; en ese último caso se muestran las métricas de
negocio sugeridas por el perfil.

La misma cuadrícula se reutiliza en resultados, análisis y dashboard. Agregar,
quitar o reordenar una tarjeta actualiza inmediatamente la configuración mediante
un endpoint autenticado y protegido por CSRF. El servidor vuelve a validar que la
columna exista y que el cálculo sea compatible con su tipo. Los campos de texto
admiten conteos, valores únicos y porcentajes de completitud, no sumas ni promedios.

Las gráficas no comparan columnas con unidades distintas. Priorizan:

1. Una serie temporal basada en la primera métrica configurada que admita suma,
   promedio, mínimo o máximo. La granularidad es mensual, semanal, diaria u
   horaria según el rango; nunca baja a minutos o segundos.
2. Una agrupación categórica de baja cardinalidad, limitada a los diez grupos
   principales.
3. Las diez columnas con más valores nulos.

Si el usuario no conserva una métrica agregable, las gráficas muestran cantidad
de registros en lugar de inventar una suma sobre identificadores.

## Propuesta después del ETL

Desde Limpieza, «Finalizar revisión ETL y proponer métricas» analiza la versión
activa. El usuario debe guardar antes las decisiones pendientes: este botón no
las aplica. La IA recibe contexto y catálogo de cálculos, sin muestras de filas,
y propone entre una y cinco métricas con sus motivos. No se exige inventar cinco
si el archivo no ofrece suficientes medidas útiles.

La propuesta se audita con propósito `dashboard_recommendations` y se reutiliza
por archivo, usuario, versión activa, modelo y prompt. «Crear dashboard con estas
métricas» guarda las tarjetas en la misma configuración existente. Una propuesta
no sustituye ajustes manuales hasta que el usuario la acepta; una propuesta de
otra versión no puede aplicarse. Los identificadores y contactos quedan excluidos
de la aritmética automática. Las gráficas usan las tarjetas elegidas, agregan el
tiempo como máximo a nivel de hora y excluyen categorías marcadas como contactos.
