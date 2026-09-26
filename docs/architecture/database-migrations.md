# Migraciones de base de datos

CoDataU usa Flask-Migrate y Alembic para administrar el esquema de SQLite en
desarrollo y PostgreSQL en una futura instalación productiva. La aplicación ya
no ejecuta `db.create_all()` al arrancar fuera del entorno de pruebas.

## Primer arranque y actualizaciones

Después de configurar el entorno se debe ejecutar:

```bash
python -m flask --app run.py db upgrade
```

Las dos revisiones iniciales son compatibles tanto con una base vacía como con
una instalación anterior creada por `db.create_all()`:

1. `0001_legacy_schema_baseline` adopta o crea usuarios, archivos e insights.
2. `0002_pipeline_and_ai_tables` adopta o crea versiones, decisiones de limpieza,
   configuración del dashboard y auditoría de IA.

Las revisiones de adopción tienen un `downgrade` intencionalmente no destructivo,
porque no es posible saber si una tabla existente fue creada por Alembic o por
una versión anterior. Las revisiones futuras deben incluir cambios reversibles
cuando hacerlo no implique pérdida de datos.

## Crear una nueva revisión

```bash
python -m flask --app run.py db migrate -m "describe el cambio"
python -m flask --app run.py db check
python -m flask --app run.py db upgrade
```

El archivo generado se debe revisar antes del commit. Alembic no puede inferir
con seguridad todos los renombres ni transformaciones de datos.

Antes de actualizar un entorno persistente se debe respaldar la base y los
directorios `uploads/` y `artifacts/`.
