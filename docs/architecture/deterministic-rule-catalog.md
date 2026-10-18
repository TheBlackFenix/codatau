# Catálogo de transformaciones antes de IA

## Contrato

Cada regla declara tipo de entrada, gramática completa, parámetros, salida,
validaciones y excepciones. No se extraen fragmentos de una celda arbitraria para
forzar una conversión. Varias interpretaciones válidas implican una decisión de
formato; una infracción de negocio no autoriza inventar un valor. Toda mutación
pasa por vista previa y confirmación versionada. Conservar protege el original.

La configuración tiene prioridad sobre inferencias de contexto. Las conversiones
se evalúan antes de comparar duplicados, de modo que una conversión puede crear
duplicados que no existían como texto. Los escenarios por celda no deben solaparse:
vacío, inválido, ambiguo, signo, rango y conversión tienen tratamientos separados.

## Matriz de cobertura del asistente

| Tipo / regla | Estado | Parámetros y protección | Prueba principal |
|---|---|---|---|
| Texto numérico a entero/decimal | Implementado | Sin redondear enteros; límites exactos | test_numeric_normalization.py |
| Miles y decimales regionales | Implementado en catálogo opcional | auto, punto decimal o coma decimal; agrupación estricta | test_numeric_normalization.py |
| Enteros en palabras españolas | Implementado en catálogo opcional | Celda completa, text2num fijado a 3.0.2; menos conserva signo | test_numeric_normalization.py |
| Notación científica | Implementado en catálogo opcional | Punto en mantisa; exponentes acotados; valores finitos | test_numeric_normalization.py |
| Mínimo y máximo | Implementado | Inclusivos, opcionales, compatibles con enteros y regla de signo | test_numeric_bounds.py |
| Signo a positivo | Implementado con confirmación de grupo | Solo negativos prohibidos; resultado dentro de límites | test_numeric_bounds.py |
| Fechas inequívocas y orden ambiguo | Implementado | Origen separado de presentación; no inventar calendario | test_date_normalization.py |
| Sí/No | Implementado básico | Diccionario cerrado en el ejecutor; no verdad por cadena no vacía | test_cleaning_treatments.py |
| Vacíos: conservar, reemplazar, vaciar o separar | Implementado | Elección por escenario; no imputación automática | test_wizard_execution.py |
| Duplicados exactos o por claves | Implementado | Criterio y registro conservado explícitos | test_duplicate_configuration.py |
| Correo/teléfono | Validación básica actual | No reconstruir contactos; pendiente mejorar validadores compartidos | test_cleaning_treatments.py |
| Texto: espacios, controles seleccionados, Unicode NFC y mayúsculas | Implementado, opt-in por columna de texto o identificador | Propuesta agrupada, conservar protege el valor; mantiene tildes y saltos de línea | test_text_normalization.py |
| Categorías y diccionarios de equivalencias | Pendiente | Aprobación de equivalencias; no fusionar por parecido automáticamente | Pendiente |
| Marcadores configurables de vacío | Pendiente | Por columna; no tratar 0 o NA como ausente globalmente | Pendiente |
| Porcentajes, moneda, notación contable, unidades | Pendiente | Escala, moneda y unidad explícitas; no eliminar símbolos arbitrariamente | Pendiente |
| Decimales escritos en palabras | Pendiente | No usar extracción parcial ni parser permisivo | Pendiente |
| Fechas con meses escritos, seriales y timestamps | Pendiente | Idioma, sistema/origen y unidad explícitos | Pendiente |
| Relleno e interpolación | Pendiente / fuera del bloque inicial | Grupo, orden, método y límites explícitos | Pendiente |
| Relaciones entre campos | Pendiente | Fórmulas y tolerancias confirmadas; detectar no equivale a corregir | Pendiente |

## Catálogo numérico `standard-es-v1`

Se habilita en columnas nuevas y se propone, no se aplica por el mero hecho de
guardar tipos. Configuraciones anteriores sin `numeric_rules` mantienen el análisis
previo hasta que el usuario active el catálogo y guarde. Desactivar el catálogo no
desactiva la conversión de texto numérico básico elegida con el tipo de columna.

El origen `auto` compara las gramáticas punto/coma: 3,5 es inequívoco; 1,234 tiene
dos interpretaciones y queda en `number_ambiguous`. No se resuelve mediante mayoría
de una muestra ni con IA. El usuario confirma el formato de origen en columnas.
Las gramáticas regionales no aceptan miles mal agrupados, espacios internos, monedas,
porcentajes ni unidades. La mantisa científica usa punto. Los enteros en palabras
son españoles, incluida una forma explícita de signo «menos». Expresiones aproximadas,
intervalos, ordinales y frases con unidades no se aceptan como números.

`NumericNormalizationService` comparte interpretación entre diagnóstico y ejecución.
Clasifica las propuestas no triviales en `numeric_convertible`: una acción
`normalize_number` las acepta como grupo, o conservar mantiene sus celdas intactas.
Las magnitudes interpretadas fuera de reglas de signo/rango se excluyen del grupo
de conversión para que reciban un tratamiento explícito separado. No consumen IA.

Los cálculos de interpretación y límites usan Decimal, y los enteros se convierten
sin pasar por float. El almacenamiento analítico decimal sigue siendo double cuando
la representación puede hacer ida/vuelta; valores que perderían dígitos quedan como
Decimal y terminan en una columna de texto advertida por el ejecutor. Esto NO es
todavía un contrato monetario de precisión fija: moneda y escala siguen pendientes.

La versión del catálogo participa en huellas de propuestas y en el contrato de
confirmación del ejecutor. Se audita la regla concreta por celda. Cambiar formato,
límites o activación invalida los pasos posteriores. Propuestas IA usan el formato
de destino, se contrastan con límites y no pueden cambiar una magnitud reconocible.

## Referencias de diseño

### Texto `text-v1`

El asistente guarda solo opciones activadas en `text_rules`: recortar espacios
horizontales exteriores, reducir espacios/tabulaciones, retirar controles Cc salvo
tabulaciones y saltos de línea, retirar U+200B y U+FEFF, normalizar NFC, y convertir
a mayúsculas o minúsculas. No elimina tildes, no aplica capitalización de nombres,
no elimina todos los caracteres Cf ni fusiona categorías. Ninguna opción se activa
por defecto y solo aplica a Texto e Identificador. La casilla de espacios no borra
saltos de línea; la reducción horizontal sí convierte tabulaciones si se selecciona.

El escenario `text_normalizable` excluye vacíos y permite aplicar una vez el grupo
con `normalize_text`, conservar, reemplazar, vaciar o separar. La IA no participa.
Se muestran hasta tres ejemplos y se advierte si retirar caracteres dejará celdas
vacías. La transformación solo corre tras la elección del grupo; no se reaplica
automáticamente a reemplazos ni a celdas conservadas. Duplicados se comparan después
de los cambios aceptados, con auditoría y preferencias en la instantánea de versión.
La huella de propuestas y el token firmado incluyen versiones de reglas.

Las nuevas cargas ya no recortan espacios de texto antes del asistente. Se conserva
la preparación estructural previa (filas/columnas totalmente nulas), y la utilidad
`clean_dataframe` conserva su comportamiento de recorte por defecto para otros
consumidores. Versiones analíticas antiguas pueden tener espacios recortados durante
su carga original; este cambio no reconstruye caracteres que ya no existen allí.

- https://learn.microsoft.com/en-ie/powerquery-m/number-fromtext
- https://text2num.readthedocs.io/en/latest/api.html
- https://babel.pocoo.org/en/latest/api/numbers.html
- https://www.unicode.org/faq/normalization.html
- https://pint.readthedocs.io/en/latest/getting/tutorial.html
- https://pandas.pydata.org/docs/user_guide/missing_data.html

Estas referencias describen técnicas y capacidades; la matriz anterior, no la
existencia de una biblioteca, define el alcance implementado en CoDataU.
