# Ejecutar CoDataU en local y en nube

La aplicación ahora puede elegir por configuración dónde guardar su información. Podemos usar SQLite o PostgreSQL para usuarios, decisiones e historial, y disco local, S3 o Azure Blob Storage para los originales y resultados. No es necesario cambiar código para alternar entre esos proveedores.

Para la demostración usaremos Docker con PostgreSQL local; para nube, Flask en Render, PostgreSQL de Render y Azure Blob Storage. Los otros proveedores quedan disponibles para pruebas y cambios futuros.

## 1. Qué cambió y qué se conserva

Los cálculos siguen usando pandas y DuckDB. Los datos completos de los archivos no se insertan en PostgreSQL: se conservan como originales y Parquet. La base de datos guarda las referencias, usuarios, versiones, reglas y configuraciones.

En almacenamiento remoto, cada petición utiliza una carpeta temporal independiente. Descarga los archivos necesarios y publica originales, Parquet, perfiles y cuarentena en el proveedor configurado. La carpeta temporal se elimina al terminar. Una nueva petición puede recuperar los resultados aunque el disco del contenedor esté vacío. Las versiones nuevas se publican antes de confirmar su registro en la base; si el flujo falla, se intenta retirar sus artefactos. No existe una transacción distribuida entre SQL y el almacenamiento: una interrupción brusca podría dejar objetos sin registro, que habrá que retirar posteriormente.

Los originales usan nombres únicos y no se sobrescriben. Los perfiles pueden actualizarse cuando cambia su formato. Las descargas pasan por Flask y conservan la comprobación del usuario; no se requieren archivos públicos.

Cambiar de proveedor no copia automáticamente la información anterior. Las bases y los archivos locales existentes siguen intactos. Para esta demo es más sencillo comenzar con una base vacía y cargar archivos de prueba. Si queremos conservar el histórico, habrá que preparar una migración de datos y objetos por separado.

## 2. Requisitos

- Git y Docker Desktop con contenedores Linux activos (Windows: virtualización/WSL2 habilitado).
- Docker Compose reciente: `docker compose version`. El archivo opcional `compose.remote.yaml` usa `!reset`, disponible desde Compose 2.24.0.
- Internet para la primera descarga de imágenes, dependencias y las llamadas a IA o proveedores remotos.
- No es necesario instalar Python ni PostgreSQL en el computador al usar Docker.

## 3. Local con PostgreSQL en Docker

Desde la carpeta del proyecto, en PowerShell:

Si ya existe una configuración de IA funcional en `.env`, ejecutar primero `venv\Scripts\python.exe scripts/configure-local.py` y omitir el `Copy-Item` siguiente. Ese asistente crea `deploy/local.env` conservando las opciones IA del `.env` y respeta los valores de un archivo Docker privado que ya exista. Después se usa siempre `--env-file deploy/local.env` al levantar el entorno.

```powershell
Copy-Item deploy/local.env.example deploy/local.env
docker compose --env-file deploy/local.env up --build -d
docker compose --env-file deploy/local.env ps
```

Abrimos <http://localhost:5055>. Se utiliza un puerto diferente al servidor de desarrollo anterior para evitar choques. La base de datos queda accesible solo desde el computador en `localhost:55432`. Las credenciales de ejemplo son únicamente para este entorno local.

El arranque espera a PostgreSQL, aplica las migraciones y ejecuta Gunicorn. Si una migración falla, el servidor no se inicia. El primer arranque puede tardar por las descargas. Se puede revisar el estado en `/healthz`, que comprueba también la conexión SQL.

```powershell
docker compose --env-file deploy/local.env logs --tail 80 web
docker compose --env-file deploy/local.env exec web python scripts/database.py ping
docker compose --env-file deploy/local.env exec web python scripts/database.py check
```

Para detener y volver a iniciar sin perder datos:

```powershell
docker compose --env-file deploy/local.env down
docker compose --env-file deploy/local.env up -d
```

Los volúmenes `postgres_data` y `app_data` conservan la información. **No añadir `-v` a `down` si se quieren conservar esos datos.** No cambiar la contraseña del archivo esperando que cambie la de una base ya inicializada: `POSTGRES_PASSWORD` solo la establece al crear la base por primera vez. Los caracteres especiales en contraseñas incluidas en URL deben estar codificados como URL.

## 4. Seleccionar base de datos

### Dónde colocar las variables

| Archivo o panel | Cuándo se usa |
|---|---|
| `.env` en la raíz | Ejecución directa con Python, normalmente en el puerto 5000. |
| `deploy/local.env` | Docker con PostgreSQL local, normalmente en el puerto 5055. Se pasa explícitamente con `--env-file`. |
| `deploy/cloud.env` | Docker local conectado a los servicios remotos. Se utiliza con el archivo Compose adicional. |
| Render → Environment | Variables privadas del despliegue. No se sube un `.env` al repositorio ni a la imagen. |

Los archivos `.example` solo contienen valores ficticios o campos vacíos. Cada persona completa su archivo privado; los archivos reales están excluidos de Git. Las contraseñas locales de ejemplo no deben reutilizarse en servicios públicos.

| Variable | Para qué sirve |
|---|---|
| `SECRET_KEY` | Firma las sesiones y formularios. Usar un valor largo y estable; Render puede generarlo. |
| `WEB_PORT` / `POSTGRES_PORT` | Puertos del computador para acceder a Docker; no cambian los puertos internos. |
| `POSTGRES_PASSWORD` | Contraseña inicial de PostgreSQL local. No modifica automáticamente una base existente. |
| `DATABASE_PROVIDER` / `DATABASE_URL` | Seleccionan SQLite o PostgreSQL y su conexión. En Compose, omitir la URL utiliza la base local `db`. |
| `STORAGE_PROVIDER` / `STORAGE_PREFIX` | Seleccionan dónde guardar archivos y el espacio de nombres de los objetos remotos. |
| `AI_PROVIDER` | `disabled` apaga la IA; Gemini usa `openai_compatible`. |
| `AI_BASE_URL` | Para Gemini: `https://generativelanguage.googleapis.com/v1beta/openai/`. |
| `AI_MODEL` | Identificador exacto de un modelo disponible para la cuenta. |
| `AI_API_KEY` | Clave privada del proveedor; nunca se incluye en capturas o commits. |
| `AI_MODEL_SELECTOR_ENABLED` | `1` muestra el selector a usuarios autenticados. Necesita un modelo configurado; el catálogo requiere una clave válida. |
| `AI_RESPONSE_MODE` | `json_schema` para respuestas estructuradas; `json_object` como compatibilidad. |
| `AI_TIMEOUT_SECONDS` / `AI_MAX_OUTPUT_TOKENS` | Tiempo de espera y límite de salida; predeterminados: 30 segundos y 1200 tokens. |
| `AI_MAX_CANDIDATE_COLUMNS` / `AI_SAMPLE_VALUES` | Límites de columnas candidatas y muestras por caso; predeterminados: 8 y 4. |
| `AI_CONTEXT_SAMPLE_ROWS` / `AI_CONTEXT_MAX_COLUMNS` | Límites para inferir contexto: 5 filas y 80 columnas. |

Para habilitar Gemini en Docker se añaden sus variables a `deploy/local.env`, usando una clave privada y un modelo disponible. Las demás variables de IA pueden omitirse para conservar sus valores predeterminados. Después de editar el archivo se ejecuta `docker compose --env-file deploy/local.env up -d`: recrea el servicio afectado y conserva los volúmenes. Editar el archivo sin actualizar el contenedor no modifica la aplicación en ejecución.

El selector se consulta después de iniciar sesión. Si el proveedor rechaza el catálogo, se conserva el modelo configurado; eso no confirma que una llamada de generación funcione. Hay que verificar la clave, el acceso al modelo y la cuota en la cuenta del proveedor.

`DATABASE_PROVIDER` acepta `auto`, `sqlite` y `postgresql`. `auto` mantiene la compatibilidad con instalaciones anteriores que solo usaban `DATABASE_URL`. Un proveedor explícito incompatible con la URL produce un error claro al arrancar.

| Proveedor | Ejemplo de DATABASE_URL |
|---|---|
| SQLite dentro de Docker | `sqlite:////data/instance/codatau.db` |
| PostgreSQL del Compose | `postgresql+psycopg://codatau:codatau-local-only@db:5432/codatau` |
| PostgreSQL remoto | `postgresql+psycopg://usuario:clave@servidor:5432/base?sslmode=require` |

En `deploy/local.env` se pueden establecer ambas variables para reemplazar el valor predeterminado PostgreSQL del Compose. Las URL `postgres://` y `postgresql://` se normalizan para el controlador psycopg. La base remota debe existir antes de ejecutar las migraciones.

No hay un segundo esquema SQL mantenido a mano: **los scripts de Alembic en `migrations/versions/` son la fuente del esquema**, y funcionan en ambos motores. `scripts/database.py` ofrece:

```powershell
python scripts/database.py upgrade
python scripts/database.py check
python scripts/database.py ping
```

`upgrade` aplica las revisiones pendientes sin borrar datos; `check` compara el esquema con los modelos; `ping` comprueba conexión. Se pueden ejecutar con Python local después de instalar `requirements.txt` y configurar las variables, o mediante `docker compose exec web` como en el ejemplo anterior. Por defecto usan configuración de producción; `APP_ENV=development` permite el entorno de desarrollo.

Para copias y restauración de PostgreSQL se usan las herramientas estándar `pg_dump` y `pg_restore`. En la demo local, por ejemplo:

```powershell
docker compose --env-file deploy/local.env exec -T db pg_dump -U codatau -d codatau -Fc -f /tmp/codatau.dump
docker compose --env-file deploy/local.env cp db:/tmp/codatau.dump ./output/codatau.dump
```

La copia contiene datos privados: no se versiona. La restauración debe hacerse sobre una base vacía distinta, verificando su destino antes de ejecutar `pg_restore`; no es parte del arranque normal.

## 5. Seleccionar almacenamiento

`STORAGE_PROVIDER` acepta `local`, `s3` o `azure`. `STORAGE_PREFIX` separa los objetos de diferentes entornos. Por ejemplo, usar `codatau-local` y `codatau-nube` junto con bases separadas mantiene independientes las pruebas. Si se comparte la misma base, deben usarse el mismo contenedor/bucket y prefijo para que las referencias correspondan a los mismos archivos.

### Disco local

```dotenv
STORAGE_PROVIDER=local
UPLOAD_FOLDER=/data/uploads
ANALYTICS_FOLDER=/data/artifacts
```

Docker ya configura esas carpetas en un volumen. En una ejecución Python sin Docker se mantienen las carpetas `uploads` y `artifacts` predeterminadas. En Render gratuito no se debe elegir este proveedor para conservar datos entre reinicios.

### S3, Cloudflare R2 u otro compatible

```dotenv
STORAGE_PROVIDER=s3
STORAGE_PREFIX=codatau-demo
S3_BUCKET=nombre-del-bucket
S3_ENDPOINT_URL=https://endpoint-del-proveedor
S3_REGION=us-east-1
S3_ADDRESSING_STYLE=path
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
```

Para AWS S3 se puede omitir el endpoint y usar la región real; el cliente también admite credenciales de su cadena habitual si las claves explícitas están vacías. Para R2, el endpoint es `https://<account-id>.r2.cloudflarestorage.com` y la región `auto`. El bucket debe estar creado y ser privado. Las credenciales necesitan consultar, leer, escribir y eliminar objetos del bucket/prefijo elegido. Se utilizan operaciones básicas compatibles con S3, incluida escritura condicional para originales; hay que verificar esa capacidad si se usa otro proveedor.

### Azure Blob Storage

```dotenv
STORAGE_PROVIDER=azure
STORAGE_PREFIX=codatau-demo
AZURE_STORAGE_CONTAINER=codatau
AZURE_STORAGE_CONNECTION_STRING=...
```

Crear antes un contenedor privado en la cuenta de almacenamiento. La cadena de conexión se configura en el archivo privado o en Render, nunca en Git. Como alternativa, se puede dejar vacía y definir `AZURE_STORAGE_ACCOUNT_URL`; se usará `DefaultAzureCredential`: con sesión de Azure CLI en Python local, o identidad de aplicación en Render mediante `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` y `AZURE_CLIENT_SECRET`, con permisos sobre los blobs. Una sesión `az login` del computador no se comparte automáticamente con el contenedor Docker.

El proveedor no crea buckets ni contenedores en la nube automáticamente. Una falta de permisos o fallo de red produce un error; no se cambia silenciosamente al disco local.

## 6. Local usando los servicios que ya creamos en nube

```powershell
Copy-Item deploy/cloud.env.example deploy/cloud.env
# Completar la URL externa de PostgreSQL y las variables privadas de Azure.
docker compose --env-file deploy/cloud.env -f compose.yaml -f compose.remote.yaml up --build -d web
```

El archivo adicional quita la dependencia de PostgreSQL local. Solo se levanta Flask y se abre en <http://localhost:5055>. Usar la URL **externa** de PostgreSQL de Render, TLS y sus reglas de acceso de red. En Azure, permitir acceso desde el servidor/computador según la configuración de red de la cuenta.

La IA es opcional: arranca deshabilitada para no consumir llamadas en las pruebas. Para activarla, establecer las variables `AI_PROVIDER`, `AI_BASE_URL`, `AI_MODEL` y `AI_API_KEY` que ya usa el proyecto.

## 7. Publicar Flask en Render

El repositorio incluye `render.yaml` y `Dockerfile`. No publica nada por sí solo. Podemos crear un Blueprint desde la rama que contenga estos archivos, o crear un Web Service con runtime Docker y usar su configuración como guía.

1. Conectar el repositorio y seleccionar la rama preparada.
2. Elegir el plan gratuito para la demo.
3. Definir `DATABASE_URL` con la URL interna si el servicio y PostgreSQL están en la misma región. Usar la externa con TLS si no lo están.
4. Establecer `DATABASE_PROVIDER=postgresql`.
5. Elegir `STORAGE_PROVIDER=azure` y completar contenedor, prefijo y cadena de conexión. El Blueprint pide esos valores privados y genera `SECRET_KEY`.
6. Mantener `APP_ENV=production`. La imagen ejecuta `sh scripts/start-web.sh`: migraciones y Gunicorn escuchando el `PORT` de Render. No usa el servidor de desarrollo.
7. Conservar `/healthz` como comprobación de salud.
8. Si se quiere probar IA, cambiar `AI_PROVIDER` y añadir sus demás variables en el panel. El Blueprint la deja apagada inicialmente.
9. Esperar el despliegue y probar registro, carga, limpieza, cuarentena, descarga y dashboard. Reiniciar el servicio y verificar que los datos siguen disponibles.

El Blueprint usa la base existente mediante su URL; no crea ni reemplaza PostgreSQL. Para S3, cambiar el proveedor y añadir sus variables en Render. Solo desplegamos una instancia con un proceso Gunicorn para esta demo; la coordinación de migraciones para varias instancias queda fuera de este alcance.

Render gratuito puede dormir y dispone de poca memoria. Para la presentación usar archivos pequeños y abrir la página con anticipación. Revisar la fecha de vencimiento de PostgreSQL si es gratuito. El almacenamiento y las llamadas IA consumen las cuotas/créditos de sus respectivas cuentas.

## 8. Pruebas repetibles con PostgreSQL y almacenamiento remoto simulado

```powershell
docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yaml down
```

Este entorno usa un proyecto Docker separado, PostgreSQL, Moto para S3 y Azurite para Azure. No publica sus puertos ni utiliza las credenciales reales. Las credenciales del archivo son ficticias, exclusivas de los emuladores. La base de pruebas no tiene un volumen nombrado de la demo; `down` permite una próxima ejecución limpia.

El contenedor aplica migraciones dos veces, comprueba el esquema y la conexión y ejecuta la suite de Python. Las pruebas que usan el fixture de aplicación se conectan a PostgreSQL; las pruebas específicas de SQLite siguen usando SQLite. Las integraciones verifican carga, recuperación desde un espacio temporal nuevo, resultados, dashboard, descarga y eliminación, además de Parquet, perfil y cuarentena. Las seis pruebas de IA real siguen deshabilitadas porque consumen API.

Moto y Azurite validan nuestros clientes y flujos, pero no sustituyen una prueba con las credenciales, permisos y red de Azure/R2 reales. Tampoco se han probado límites de carga o muchos usuarios simultáneos. Azurite se ejecuta con `--skipApiVersionCheck` únicamente para permitir el SDK actual contra el emulador; esa opción no se aplica al servicio real.

## Referencias de preparación

- Flask en Render: https://render.com/docs/deploy-flask
- PostgreSQL y URL interna/externa: https://render.com/docs/postgresql-creating-connecting
- Límites de Render gratuito: https://render.com/docs/free
- Azure Blob Storage con Python: https://learn.microsoft.com/en-us/azure/storage/blobs/storage-quickstart-blobs-python
- Subida de blobs: https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python
- Escritura S3 y condiciones: https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html
- R2 con boto3: https://developers.cloudflare.com/r2/examples/aws/boto3/
