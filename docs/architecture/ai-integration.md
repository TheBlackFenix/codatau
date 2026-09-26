# Integración de IA intercambiable

## Alcance actual

La IA interviene únicamente cuando el perfil semántico marca una operación como
`ai_analysis`. El usuario inicia la consulta desde Limpieza y recibe una
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

La credencial solo se lee desde el entorno, se envía como Bearer al proveedor y
no se persiste en SQLite ni en resultados de análisis.

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
