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

## Siguientes etapas pendientes

1. Guardar un borrador persistente por archivo: tipos de columnas con nombres
   Texto, Identificador, Número entero, Número decimal, Fecha, Correo, Teléfono
   y Sí/No; formatos y muestras; navegación entre pasos sin perder decisiones.
2. Duplicados por fila completa o combinación de campos; primero/último/más reciente;
   claves incompletas sin eliminación automática y ejemplos del impacto.
3. Tratamientos por escenario: conservar, cuarentena, reemplazo, vacío o propuesta IA.
   No declarar conversión completa si se conservan valores incompatibles.
4. Propuestas de transformación de valores distintos, con impacto y motivos;
   validación local, aceptación explícita y versiones con cambios por celda.
5. Integrar vista previa, restauración, invalidación de propuestas al cambiar tipos
   y pruebas de recorrido completo del asistente.
