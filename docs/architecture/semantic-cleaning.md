# Limpieza semántica asistida

## Objetivo

El motor separa tres responsabilidades:

1. DuckDB detecta tipos, patrones y excepciones sobre las columnas completas.
2. Un catálogo cerrado propone operaciones conocidas y comprobables.
3. En una fase posterior, la IA analizará únicamente los casos ambiguos y
   devolverá parámetros para ese catálogo; nunca código arbitrario.

El perfil genera un plan con estado `proposed`. El usuario responde explícitamente
`Sí, aplicar` o `No, conservar` en las sugerencias que quiera resolver, revisa una
simulación y confirma antes de guardar sus decisiones. El Parquet de entrada nunca
se sobrescribe.

## Perfil semántico

Cada columna del perfil contiene un objeto `semantic`:

```json
{
  "type": "email",
  "confidence": 0.8,
  "source": "rules_and_patterns",
  "needs_ai": true,
  "evidence": {
    "non_blank_count": 100,
    "match_ratios": {"email": 0.8},
    "leading_zero_count": 0,
    "ambiguous_date_count": 0
  }
}
```

Los tipos iniciales son `number`, `identifier`, `email`, `phone`, `date`,
`boolean`, `text`, `empty` y `unknown`. La inferencia combina el tipo físico, el
nombre de la columna y proporciones calculadas sobre todos sus valores no vacíos.

Un valor formado por dígitos no se convierte automáticamente cuando la columna
parece un identificador o contiene ceros iniciales. Las fechas regionales, los
teléfonos y los separadores decimales con coma requieren confirmación o análisis.

## Plan de limpieza

`cleaning_plan.operations` solo acepta operaciones del catálogo versionado:

- `trim_text`
- `blank_to_null`
- `cast_type`
- `validate_email`
- `review_invalid_values`
- `parse_date`
- `normalize_boolean`
- `normalize_phone`
- `normalize_case`
- `remove_exact_duplicates`

Cada propuesta informa columna, filas afectadas, confianza, parámetros, razón y
una decisión:

- `automatic`: operación de riesgo bajo y patrón inequívoco.
- `user_review`: necesita una decisión de negocio o configuración regional.
- `ai_analysis`: contiene excepciones que justifican un análisis contextual.

Las operaciones automáticas compatibles permanecen propuestas hasta la aprobación.
El ejecutor reconstruye la selección desde el perfil del servidor y genera SQL de
DuckDB desde un catálogo cerrado. Ejecuta automáticamente `trim_text`,
`blank_to_null`, conversiones numéricas inequívocas, validación de correo, fechas
ISO y booleanos. Las reglas de revisión permiten al usuario confirmar el separador
decimal, el orden de día y mes, la normalización de mayúsculas/minúsculas y el
formato de teléfonos antes de previsualizar. Los valores nulos pueden enviarse a
cuarentena tras selección manual, sin inventar una imputación. Los duplicados
exactos también son ejecutables únicamente tras selección manual, porque dos
eventos reales pueden tener los mismos valores.

Las filas que no superan una conversión o validación salen de la versión limpia y
se escriben en un Parquet de cuarentena con la operación que causó el rechazo. El
usuario puede descargarlo como CSV para corregirlo.

Cada aplicación crea un registro `DatasetVersion` con las operaciones —incluidos
los parámetros confirmados por el usuario—, métricas y artefactos utilizados. Una
versión anterior o la base procesada se pueden activar sin eliminar las versiones
posteriores.

## Decisiones persistentes

`CleaningDecision` guarda el resultado de cada sugerencia por archivo y por
identificador de operación. Una decisión puede ser:

- `apply`: la regla fue aceptada y, si transforma datos, referencia el número de
  versión en que se aplicó.
- `keep`: el usuario decidió conservar los datos sin aplicar esa regla.

Las sugerencias sin respuesta siguen pendientes. Las aceptadas y rechazadas se
ocultan del listado principal y aparecen en una sección plegada de decisiones
resueltas. El usuario puede reabrir una decisión cuando la operación todavía se
detecta en el perfil activo.

Una confirmación que solo contiene respuestas `keep` no crea un artefacto ni una
versión vacía. Las operaciones de versiones creadas antes de esta funcionalidad
se incorporan de forma idempotente como decisiones `apply` cuando se abre el plan.

## Uso futuro de IA

La solicitud al modelo contendrá únicamente las columnas de
`ai_candidate_columns`, sus métricas, patrones anómalos y una muestra mínima
enmascarada cuando resulte necesaria. La respuesta deberá ajustarse a un esquema
JSON y elegir una operación del catálogo. Esos parámetros pasarán por el mismo
ejecutor, la vista previa y la aprobación ya implementados.

## Limitación transitoria

La capa de compatibilidad actual lee y normaliza archivos con pandas antes de
crear el Parquet. El original sigue siendo la fuente inmutable. `DatasetVersion`
ya conserva el linaje de las limpiezas aprobadas. Cuando se añada una entidad
`Dataset` independiente de la carga, el primer artefacto podrá conservar los
valores crudos y la inferencia de tipos pasará a ser una transformación explícita
y reversible.
