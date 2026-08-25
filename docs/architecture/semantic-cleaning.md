# Limpieza semántica asistida

## Objetivo

El motor separa tres responsabilidades:

1. DuckDB detecta tipos, patrones y excepciones sobre las columnas completas.
2. Un catálogo cerrado propone operaciones conocidas y comprobables.
3. En una fase posterior, la IA analizará únicamente los casos ambiguos y
   devolverá parámetros para ese catálogo; nunca código arbitrario.

La versión actual solo genera un plan con estado `proposed`. No ejecuta las
recomendaciones ni modifica el Parquet automáticamente.

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

Incluso las operaciones clasificadas como automáticas permanecen propuestas hasta
que exista el ejecutor versionado, la vista previa y el mecanismo de aprobación.
Los duplicados exactos requieren revisión porque dos eventos reales pueden tener
los mismos valores.

## Uso futuro de IA

La solicitud al modelo contendrá únicamente las columnas de
`ai_candidate_columns`, sus métricas, patrones anómalos y una muestra mínima
enmascarada cuando resulte necesaria. La respuesta deberá ajustarse a un esquema
JSON y elegir una operación del catálogo. DuckDB generará después una vista previa
con las filas modificadas, rechazadas o puestas en cuarentena.

## Limitación transitoria

La capa de compatibilidad actual lee y normaliza archivos con pandas antes de
crear el Parquet. El original sigue siendo la fuente inmutable. Cuando se añadan
`Dataset` y `DatasetVersion`, el primer artefacto conservará los valores crudos y
la inferencia de tipos pasará a ser una transformación explícita y reversible.
