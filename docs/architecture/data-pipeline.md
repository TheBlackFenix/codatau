# Pipeline de datos de CoDataU

## Decisión

CoDataU separa el archivo que entrega el usuario de la representación usada para
analizarlo:

1. El original se guarda sin modificar mediante una abstracción de
   almacenamiento.
2. La capa actual de compatibilidad lee y normaliza CSV o Excel con pandas.
3. DuckDB materializa el resultado en Parquet comprimido con Zstandard.
4. DuckDB calcula un perfil determinista: esquema, nulos, cardinalidad
   aproximada, duplicados, estadísticas numéricas y una muestra acotada.
5. El dashboard y los reportes leen el artefacto Parquet y pueden recurrir al
   original para archivos creados antes de esta versión.
6. Una limpieza aprobada crea un nuevo Parquet y, cuando corresponde, un artefacto
   de cuarentena; la versión activa puede cambiarse sin sobrescribir las anteriores.

Por ahora, SQLite solo almacena usuarios, metadatos e insights. No contiene los
datos tabulares cargados.

## Por qué Parquet + DuckDB

Parquet es el formato canónico porque es columnar, comprimido y portable. DuckDB
puede consultarlo directamente, proyectar únicamente las columnas necesarias y
aplicar filtros sin cargar el archivo completo. La API relacional también permite
escribir Parquet desde una relación.

No se usa un único archivo `.duckdb` compartido como almacenamiento principal.
Eso acoplaría los datasets a un proceso escritor y complicaría la concurrencia
entre workers web. Cada operación abre una conexión DuckDB en memoria y trabaja
sobre artefactos independientes.

Referencias oficiales:

- [API relacional de DuckDB](https://duckdb.org/docs/current/clients/python/relational_api)
- [Lectura y escritura de Parquet](https://duckdb.org/docs/stable/data/parquet/overview)
- [Concurrencia de DuckDB](https://duckdb.org/docs/stable/connect/concurrency)

## Artefactos

Para un original `uploads/<uuid>.csv`, el pipeline crea:

- `artifacts/<uuid>.parquet`: dataset normalizado para consulta.
- `artifacts/<uuid>.profile.json`: contrato compacto para UI, reglas e IA.

La escritura usa archivos temporales y reemplazo atómico. Al eliminar el registro
de un archivo se eliminan el original y ambos artefactos. Los artefactos no se
versionan en Git.

El perfil se puede consultar en `GET /files/profile/<id>` y solo está disponible
para el propietario autenticado. La muestra puede contener información sensible;
antes de conectarla a un proveedor de IA se debe añadir clasificación, enmascarado
y una política de consentimiento.

## Evolución prevista

Esta entrega mantiene la limpieza existente en pandas para no cambiar el
comportamiento del producto durante la estabilización. Los siguientes incrementos
deben:

1. Incorporar entidades `Dataset` y `DatasetVersion` para conservar linaje.
2. Llevar agregaciones, filtros y transformaciones deterministas a SQL de DuckDB.
3. Hacer que la IA produzca un plan estructurado y validable, no código arbitrario.
4. Mostrar costo estimado, vista previa y cambios antes de aplicar el plan.
5. Enviar a la IA primero el perfil y solo muestras mínimas cuando sean necesarias.
6. Sustituir el almacenamiento local por un adaptador de objetos sin cambiar el
   pipeline ni las rutas.

Así, la IA decide y explica transformaciones; DuckDB las ejecuta de forma
repetible, auditable y económica en tokens.

El contrato de inferencia, el catálogo permitido y los niveles de decisión están
detallados en [Limpieza semántica asistida](semantic-cleaning.md).
