# Integración de IA intercambiable

## Alcance actual

La IA identifica contexto durante la carga y participa bajo demanda en limpieza,
propuestas de dashboard y preguntas sobre datos. En limpieza analiza operaciones
marcadas como `ai_analysis`. El usuario inicia la consulta y recibe una
recomendación (`apply`, `keep` o `user_review`), una confianza, una explicación y
parámetros permitidos. Consultar la IA no crea una versión, no ejecuta SQL y no
modifica el archivo.

El flujo es:

1. DuckDB perfila el conjunto completo y detecta casos ambiguos.
2. `AICleaningService` selecciona como máximo las columnas configuradas, toma unas
   pocas muestras y enmascara correos, teléfonos, identificadores y secuencias
   numéricas largas.
3. El adaptador del proveedor solicita JSON estructurado.
4. CoDataU rechaza operaciones, identificadores y parámetros fuera del catálogo.
5. La recomendación validada queda en `AIAnalysisRun` con modelo, proveedor,
   tokens y huella de la solicitud.
6. Si perfil, modelo y contexto no cambiaron, la misma respuesta se reutiliza sin
   otra llamada ni consumo de tokens.
7. El usuario conserva el control: elige Sí/No, revisa la vista previa y solo
   entonces crea una versión de datos.

Las muestras deben considerarse datos no confiables. El prompt ordena ignorar
cualquier instrucción contenida en ellas, y el servidor no acepta código ni SQL
en la respuesta.

## Proveedores

La lógica de negocio depende del protocolo `StructuredAIProvider`, no de un SDK.
Los adaptadores iniciales son:

- `openai_responses`: API Responses con esquema JSON estricto.
- `openai_compatible`: endpoint compatible con Chat Completions. Permite usar un
  servicio remoto o local que respete ese contrato.

Cambiar de un servicio compatible a otro requiere normalmente modificar
`AI_BASE_URL`, `AI_MODEL` y `AI_API_KEY`. Si cambia el contrato HTTP, se agrega un
adaptador pequeño sin tocar el perfilado, el caché, la validación ni la interfaz.

## Configuración

```env
AI_PROVIDER=disabled
AI_MODEL=
AI_API_KEY=
AI_BASE_URL=https://api.openai.com/v1
AI_RESPONSE_MODE=json_schema
AI_TIMEOUT_SECONDS=30
AI_MAX_OUTPUT_TOKENS=1200
AI_MAX_CANDIDATE_COLUMNS=8
AI_SAMPLE_VALUES=4
```

`disabled` es el valor seguro predeterminado. Para OpenAI se usa
`AI_PROVIDER=openai_responses`. Para un servidor compatible se usa
`AI_PROVIDER=openai_compatible`; `AI_RESPONSE_MODE=json_object` existe para
servidores que aún no admiten esquemas JSON estrictos.

### Gemini mediante Google AI Studio

Gemini expone un endpoint compatible con Chat Completions, por lo que no
requiere un adaptador específico:

```env
AI_PROVIDER=openai_compatible
AI_MODEL=gemini-3.5-flash-lite
AI_API_KEY=tu-clave-de-google-ai-studio
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
AI_RESPONSE_MODE=json_schema
```

Los proyectos nuevos de Google ya no tienen acceso general a los modelos 2.5.
La configuración validada usa `gemini-3.5-flash-lite`, que admite respuestas
estructuradas. La clave debe rotarse si aparece en una terminal, captura o log.

La credencial solo se lee desde el entorno, se envía como Bearer al proveedor y
no se persiste en SQLite ni en resultados de análisis.

## Prueba en vivo opcional

La suite normal nunca llama a un proveedor externo. Para comprobar de forma
explícita la credencial y el contrato del proveedor configurado:

```powershell
$env:RUN_LIVE_AI_TEST='1'
python -m pytest tests/test_ai_provider_live.py -q --tb=short
```

Esta prueba realiza una solicitud pequeña y puede consumir cuota. Los errores
se resumen sin imprimir encabezados de autorización.

## Funciones previstas sobre la misma capa

La arquitectura se puede reutilizar para:

- clasificar columnas cuyo significado no se puede inferir por reglas;
- explicar anomalías y priorizar problemas de calidad;
- recomendar parámetros de las reglas existentes;
- sugerir métricas, dimensiones y visualizaciones apropiadas;
- redactar un resumen ejecutivo basado en estadísticas agregadas;
- proponer nuevas reglas, que deberán incorporarse antes al catálogo y al
  ejecutor determinista.

Cada función debe tener su propio propósito, esquema de respuesta, presupuesto y
huella de caché. Ninguna debe permitir que el modelo ejecute transformaciones o
consultas arbitrarias.

## Flujo MVP implementado

1. **Carga y contexto:** encabezados y hasta cinco muestras enmascaradas permiten
   inferir dominio, roles y restricciones. Los prompts piden textos en español,
   conservando nombres de columnas y enums técnicos. El cambio de versión de
   prompt invalida contextos antiguos; no se promete traducción perfecta del modelo.
2. **Limpieza por campo:** escenarios desplegables por columna, una sola decisión
   sobre correo inválido, configuración visible únicamente al aplicar y valor de
   reemplazo visible únicamente al reemplazar. Las decisiones resueltas no vuelven
   a consultarse. La IA permite conservar, recomendar parámetros validados o pedir
   una elección humana concreta; una revisión humana puede omitir parámetros
   inciertos. Solo `apply` requiere todos los parámetros deterministas.
3. **Dashboard:** `AIDashboardService` propone hasta cinco métricas compatibles;
   `StructuredAIService` comparte auditoría/caché por versión. La aceptación explícita
   persiste las tarjetas sin sobrescribir silenciosamente cambios manuales.
4. **Chat:** `DatasetChatService` recibe pregunta (máximo 1000 caracteres), contexto,
   catálogo de campos, rangos de fechas y hasta tres turnos previos. No recibe filas
   completas ni resultados del conjunto. La pregunta también se transmite al proveedor:
   la interfaz advierte no introducir información sensible.

### Contrato seguro del chat

El modelo devuelve un plan, nunca SQL. Se admiten:

- total, promedio, mínimo, máximo, conteo y valores únicos;
- grupos por campo o tiempo (hora, día, mes o año), hasta veinte resultados;
- hasta cinco filtros simples unidos con AND;
- comparación de dos períodos no superpuestos, inicio incluido y fin excluido;
- aclaraciones cuando falta información o la pregunta está fuera del alcance.

El servidor valida columnas, tipos, roles y estructura, cita identificadores y
parametriza filtros. DuckDB calcula sobre el dataframe completo de la versión
activa, leído del Parquet del pipeline; no sobre las muestras enviadas al modelo.
La conexión de consulta deshabilita acceso externo, limita memoria y usa dos hilos.
La respuesta se construye localmente con números verificables, sin segunda llamada
al modelo. Incluye filas coincidentes, valores usados, filtros, fechas y exclusiones.
Los valores infinitos no intervienen en cálculos numéricos. Una base cero no produce
un porcentaje ficticio; la ausencia de datos no se presenta como ventas cero.

El servidor también solicita aclaración ante varios años sin año explícito o varias
medidas monetarias para una pregunta genérica de ventas. La comprensión lingüística
sigue dependiendo del modelo; las validaciones no garantizan interpretar cualquier
pregunta correctamente. El usuario puede inspeccionar el plan y reformularla.
Las fechas de texto con ambigüedad día/mes requieren configurar el formato durante
la limpieza; las agrupaciones temporales se normalizan como máximo a nivel de hora.

El historial se guarda en `AIAnalysisRun` con propósito `dataset_chat`, aislado por
propietario, archivo y versión. Una consulta de una pantalla con versión antigua
se rechaza con 409. La caché incluye modelo, prompt, contexto e historial breve;
repetir una pregunta sin nuevos turnos no consume otra llamada. Todas las rutas
requieren sesión, propiedad del archivo y CSRF para escritura.

### Alcance pendiente después del MVP

No hay SQL libre, joins entre archivos, predicciones, edición desde chat, streaming
ni gráficos generados arbitrariamente. Para despliegue público faltan colas y
trabajos asíncronos, límites de solicitudes/cuota por usuario, política de retención
de preguntas y métricas de costo. La suite normal usa proveedores simulados; la
suite opt-in prueba contexto, limpieza, métricas y comparación con datos sintéticos.
