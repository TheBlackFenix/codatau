# Asistente de limpieza

## Alcance aprobado

JSON queda fuera. Se mantienen CSV y Excel. Los tipos y decisiones se explican
en lenguaje cotidiano, con ayudas accesibles al pasar el cursor, al hacer clic
y mediante teclado. Toda modificación requiere una vista previa y confirmación.

## Etapa 1: selector de modelos implementado

La barra superior permite elegir un modelo de Google AI Studio para las siguientes
consultas de la sesión autenticada. No modifica la configuración global, el archivo
ni resultados anteriores. La elección se vincula al usuario para evitar que otro
usuario del navegador la herede. Salir y entrar puede restablecer el modelo inicial.

El servidor consulta `v1beta/models`, pagina el catálogo y selecciona modelos Gemini
con `generateContent`, excluyendo modelos especializados en imagen, audio,
transcripción, control del ordenador y robótica. La consulta solo envía la clave
en un encabezado al dominio oficial: no transmite archivos. El listado no demuestra
acceso efectivo, cuota disponible ni compatibilidad de cada modelo con el formato
de respuesta requerido; las validaciones existentes siguen siendo obligatorias.

El catálogo se mantiene 15 minutos en memoria; «Actualizar lista» fuerza una nueva
consulta. Si falla Google se conserva el último listado y se informa al usuario.
Las claves no se incluyen en formularios, URLs, mensajes ni registros de error.
La fábrica de proveedores entrega la elección efectiva también a las huellas de
caché y a la auditoría existentes: cambiar de modelo no reutiliza un análisis de
otro modelo. Las solicitudes que ya están en curso no cambian de modelo.

`AI_MODEL_SELECTOR_ENABLED` está habilitado por defecto en desarrollo y pruebas;
en producción está deshabilitado salvo activación explícita con `1`. No se modifica
el proveedor ni la credencial desde la interfaz.

Referencia: https://ai.google.dev/api/models

## Etapa 2: configuración de columnas implementada

Desde Resultados o Limpieza se accede a «Definir tipos de columnas». Una tarjeta
por campo muestra muestras, sugerencia y elección editable, con formatos de fechas
y separadores decimales cuando corresponden. Guardar confirma las sugerencias que
no se hayan cambiado. No hay conversiones ni nuevas versiones por guardar.

Las preferencias se guardan en `cleaning_configurations`, ligadas al archivo y a
su propietario, con la versión de datos revisada y un contador de revisión. Las
pantallas antiguas no sobrescriben decisiones guardadas en otra ventana. Si cambia
la versión activa se recuperan preferencias, pero la interfaz exige revisarlas.
La relación elimina la configuración cuando se elimina el archivo. La migración
`0003_cleaning_configuration` agrega únicamente esta tabla.

Este paso aún no gobierna el ejecutor: se indica explícitamente en pantalla.
Su aplicación conjunta se conectará con duplicados, tratamientos y vista previa
en los siguientes bloques, evitando aplicar parcialmente decisiones nuevas.

## Estado del recorrido

Los pasos de configuración, propuestas, vista previa, aplicación versionada y
resultado ya están conectados. Siguen pendientes validación de usabilidad con
archivos reales y optimizaciones para grandes volúmenes; no hay trabajos en cola.

## Etapa 3: navegación y preferencias de duplicados implementadas

Una navegación numerada muestra el paso actual y los pasos guardados; las pantallas
posteriores están señaladas como pendientes, sin enlaces a acciones inexistentes.
«Guardar y continuar» del primer paso lleva a duplicados. Volver conserva lo guardado,
y salir con cambios pendientes provoca una advertencia del navegador.

El segundo paso permite no eliminar duplicados, comparar todos los campos o una
combinación explícita. La comparación es exacta: no normaliza textos ni fusiona
clientes. Primero y último se refieren al orden del archivo. Más reciente usa una
columna configurada como Fecha y su formato elegido; un empate conserva el último
registro. Los campos de clave vacíos y los grupos con fechas no interpretables se
protegen: no se proponen eliminaciones para esos registros.

Los criterios se guardan en la nueva columna `duplicates` de la configuración,
agregada por la migración `0004_duplicate_preferences`. La pantalla calcula cuántas
filas se quitarían y muestra hasta ocho ejemplos. No modifica los datos ni crea
versiones: la aplicación conjunta se conectará en la siguiente etapa. Cambiar tipos
o formatos limpia estos criterios para exigir una nueva revisión. Las revisiones
de configuración y la versión activa impiden guardar desde pestañas antiguas.

## Etapa 4: preferencias de tratamiento implementadas

El tercer paso diagnostica valores vacíos y valores incompatibles con los tipos
elegidos por el usuario. Enteros no admiten fracciones, decimales respetan el
separador elegido y fechas usan el formato confirmado. Correo, teléfono y Sí/No
tienen validaciones básicas de formato, no verificaciones de identidad ni de
existencia real. Texto e identificadores no reciben conversiones numéricas.

Cuando el contexto propone valores positivos/no negativos para un campo numérico,
se muestra además un escenario de rango consultivo. Sin esa evidencia no se
rechazan números negativos simplemente por ser negativos.

Los escenarios se agrupan en desplegables por columna con ejemplos y recuentos.
El usuario puede conservar, separar toda la fila, reemplazar únicamente la celda,
dejarla vacía o solicitar una futura propuesta IA. Los vacíos no admiten IA para
no inventar datos. Reemplazos requieren entre 1 y 200 caracteres. Conservar no
envía valores ocultos de reemplazo al ejecutor; solo se guarda la acción elegida.

La migración `0005_cleaning_treatments` añade preferencias a la configuración.
Guardar no llama al proveedor, no transforma datos ni crea versiones. La próxima
etapa consumirá únicamente las preferencias IA y la etapa final integrará todas
las decisiones para la vista previa. Cambiar tipos o duplicados borra tratamientos
para exigir revisión; versiones y revisiones de borrador rechazan pestañas antiguas.

## Etapa 5: propuestas de valores implementadas

El cuarto paso recoge únicamente escenarios marcados para IA. Agrupa por columna
y valor distinto, conserva su frecuencia y pide hasta ocho propuestas por consulta.
Más valores se consultan con otra acción explícita. Un id derivado del escenario y
valor original mantiene la correspondencia sin aceptar columnas inventadas.

Se envían solamente conversiones de números, fechas y Sí/No, junto con el formato
elegido y dominio general. Contactos, identificadores, cadenas largas y posibles
correos/documentos encontrados en otras columnas quedan para revisión manual.
Vacíos y correcciones de rango sin evidencia no se inventan. La respuesta se valida
contra el tipo objetivo; para números ya interpretables tampoco se permite cambiar
su magnitud, por ejemplo redondear 3,5 a 3. Una propuesta ambigua no ofrece Aceptar.

`StructuredAIService` guarda modelo, tokens, huella y resultado con propósito
`cleaning_value_proposals`. Las conversiones se muestran con original, propuesta,
motivo y filas afectadas. Aceptar o conservar se guarda en `ai_review`, agregada por
`0006_value_proposals`; no cambia archivos ni crea versiones. Cambios de columnas,
duplicados o tratamientos invalidan lo pendiente. Cambiar el modelo exige propuestas
nuevas, manteniendo las ejecuciones anteriores en auditoría. Se rechazan revisiones
antiguas y respuestas recibidas después de cambiar el borrador en otra ventana.

El próximo bloque debe consumir SOLO decisiones aceptadas en la vista previa
conjunta y la aplicación versionada. Rechazar conserva los valores originales;
no es permiso para borrarlos ni enviarlos automáticamente a cuarentena.

## Etapa 6: vista previa y confirmación implementadas

`WizardExecutionService` toma una copia de la versión activa. Valida que estén
guardados todos los escenarios y que cada valor solicitado a la IA tenga decisión
actual. Revalida las propuestas aceptadas; las rechazadas se conservan. No ejecuta
SQL ni código del modelo. El orden es separar filas solicitadas, resolver celdas,
convertir tipos y recalcular duplicados sobre ese resultado. Los recuentos del paso
2 son orientativos hasta esta vista previa conjunta.

Los valores conservados o reemplazos incompatibles no se convierten silenciosamente
a nulos. Cuando una columna mezcla texto con números o fechas, se almacena como
texto preservando representaciones y se advierte que no quedó totalmente convertida.
Enteros fuera del rango representable también quedan como texto, sin redondearse.
El pipeline inicial ya normalizó la carga: no se reconstruyen ceros iniciales ni
información que no exista en esa versión de origen.

Confirmar exige un token firmado con propietario, archivo, versión de origen,
revisión de configuración, huella del plan/modelo y hash del archivo analítico.
Vence a los 30 minutos. El servidor vuelve a calcular y valida el estado antes
de materializar. Una actualización atómica consume la revisión del borrador y
serializa confirmaciones; los archivos se escriben con nombres únicos para que
un fallo no borre artefactos de otra solicitud. La transacción activa la versión
solo después de generar y perfilar el resultado. En un fallo se revierte la base
y se eliminan únicamente los nuevos artefactos de esa solicitud.

La versión registra instantánea de columnas, duplicados, tratamientos, propuestas,
decisiones y cambios por celda (registro de origen, columna, antes, después, motivo),
además de números de registros duplicados retirados. El registro 1 representa el
primer dato de la versión de origen, no una línea física del CSV original. El
paquete de calidad existente incluye esa auditoría en `reporte_calidad.json`.
Puede haber registros transformados que después sean retirados como duplicados:
los contadores de cambios, cuarentena y duplicados no deben sumarse como categorías
mutuamente excluyentes. Las filas separadas conservan sus valores de origen y motivos.

La pantalla final permite descargar el paquete, consultar el historial/restaurar
versiones y solicitar métricas de la versión activa. Un resultado vacío también
puede confirmarse, con advertencia explícita previa y filas separadas recuperables.
La configuración conserva su versión de origen: para limpiar una versión nueva
hay que revisarla de nuevo, impidiendo reaplicar por accidente una vista antigua.

## Regla de negativos y tratamiento por grupo

Para números enteros y decimales, el paso de columnas guarda `negative_policy`:
`allow` (válidos), `deny` (cero o positivo) o `unknown` (consultar el contexto).
Configuraciones anteriores equivalen a `unknown`. La elección explícita prevalece
sobre restricciones inferidas: `allow` no genera alertas por signo y `deny` marca
solo negativos, no ceros. Vacíos, formatos inválidos y fracciones en enteros se
tratan por separado. Cambiar esta preferencia invalida los pasos posteriores.

El escenario `negative` permite elegir una vez un tratamiento para toda la columna.
Además de conservar, separar, vaciar o reemplazar, ofrece convertir el grupo a
positivo (`absolute`). Es una decisión explícita del usuario, no una deducción del
modelo; no se ofrece para restricciones meramente inferidas ni otros escenarios.
El ejecutor revalida esta condición, conserva la magnitud y registra cada cambio
con su motivo en la nueva versión, después de la vista previa y confirmación.
No se consumen tokens para ejecutar esta operación determinista.

Esto no sustituye todavía el análisis IA valor por valor por un motor general de
reglas contextuales agrupadas. Ese siguiente bloque debe proponer reglas acotadas
por patrón, con ejemplos, cobertura y excepciones, sin ejecutar código generado
ni asumir que cualquier negativo es un error.

## Fechas deterministas y decisiones por grupo

`DateNormalizationService` separa interpretación (`date_source`: auto/dmy/mdy)
de presentación y exportación (`date_format`: iso/dmy/mdy). Configuraciones sin
`date_source` conservan su criterio anterior de interpretación. Fechas ISO válidas,
fechas ya tipadas y fechas numéricas de cuatro dígitos de año con orden inequívoco
se interpretan sin IA. Las formas día/mes/año y mes/día/año con dos interpretaciones
válidas se agrupan en `date_ambiguous`; confirmar día primero o mes primero resuelve
el grupo completo. No se extrapola el orden a partir de una muestra de otras filas.

El usuario también puede conservar, separar, vaciar o reemplazar ese grupo. Fechas
imposibles no se reconstruyen y no se envían a IA para adivinar. No se interpretan
números como seriales de Excel o timestamps sin configuración explícita. No se
aceptan años de dos dígitos. La primera versión soporta ISO con hora y zona; fechas
con barras, puntos o guiones en día/mes/año o mes/día/año se admiten sin hora.
Otros patrones se mantienen como incompatibles hasta agregar reglas acotadas.

Diagnóstico, conversión y selección del duplicado más reciente comparten el mismo
intérprete. La vista previa muestra el formato final. El paquete de calidad y la
descarga CSV de Reportes usan las preferencias de la versión descargada, no el
borrador actual. Los datos analíticos mantienen fechas tipadas cuando no hay valores
retenidos incompatibles. Se preservan horas y fracciones; zonas se normalizan a UTC,
como en el ejecutor anterior. No se promete que todas las pantallas de análisis
adopten todavía el formato de presentación personalizado.

Las reglas tienen versión en el token de confirmación y la auditoría: una vista
previa generada con reglas anteriores no confirma un resultado calculado con reglas
nuevas. Cambios de configuración siguen invalidando los pasos posteriores. Una
preferencia IA antigua para fechas exige volver a tratamientos y guardar el nuevo
criterio agrupado. Esta etapa elimina la revisión individual de fechas, no reemplaza
todavía todos los escenarios numéricos y semánticos por reglas agrupadas.
