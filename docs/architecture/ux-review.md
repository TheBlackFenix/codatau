# Revisión de interfaz

Se conserva la identidad morada/naranja y las rutas y operaciones existentes.
La capa `ux.css` unifica espaciado, jerarquía, foco visible, controles táctiles,
tablas desplazables y layouts a una columna en pantallas pequeñas. Los elementos
ocultos no recuperan display por reglas CSS. No se declara certificación WCAG.

Cambios funcionales acotados: menú móvil con estado accesible y Escape; menú de
cuenta con etiqueta/estado; enlace para saltar al contenido y landmark principal.
La configuración IA queda en un desplegable sin cambiar modelo ni consultas.
El asistente destaca el paso actual, señala los guardados y bloquea enlaces que
requieren configuración previa. Solo Guardar y continuar tiene énfasis principal;
guardar sin avanzar sigue disponible. En propuestas, Guardar y revisar limpia la
ambigüedad entre guardar y navegar: persiste decisiones antes de abrir la vista
previa. El servidor sigue validando que estén resueltas. No aplica transformaciones.

La página de limpieza destaca el asistente, conservando las herramientas individuales
y el historial. El dashboard mantiene las sugerencias IA en un desplegable para no
ocultar las métricas debajo de una explicación larga. No se cambia la configuración
de métricas, sus valores, su orden ni las decisiones de limpieza existentes.

Las acciones de archivos tienen texto visible (Ver, Usar, Limpiar, Eliminar). La
confirmación de eliminación usa un atributo de datos y no concatena nombres dentro
de JavaScript, evitando errores con comillas. Es una confirmación, no una papelera:
el alcance de borrado del servidor no cambia. Se corrigieron desbordamientos reales
de los layouts de Análisis y Reportes: los cuatro módulos principales, carga y
columnas no desbordan la página en la comprobación de 390 px. El menú móvil abre y
cierra con Escape; la vista se restaura al tamaño normal tras la prueba.

Validación: suite de backend y plantillas; pruebas de shell para dashboard, archivos,
reportes, análisis, resultados y limpieza; test de guardar decisiones y avanzar.
Revisión visual sobre el local con sesión existente, sin escribir en sus archivos:
dashboard, entrada a limpieza, columnas y carga; comprobación de menú a 390 px,
medición de anchura de página y tablas confinadas. Se preservan las pruebas de
versiones, exportación, permisos, CSRF y reglas. No implica haber probado manualmente
todos los estados con datos reales, todas las tecnologías asistivas o todos los
navegadores. Confirmaciones finales y operaciones destructivas no se ejecutan sobre
archivos del usuario durante esta auditoría; se prueban con datos sintéticos aislados.

## Tablas del chatbot

El detalle usa caption, thead, tbody, encabezados de columna/fila, cifras alineadas
a la derecha con dígitos tabulares, filas alternadas y región desplazable accesible.
Los metadatos del cálculo se presentan en una lista de definiciones. Los números
se formatean para es-CO, sin asumir moneda; cero y ausencia permanecen distintos.
Solo las etiquetas de agrupaciones temporales se presentan como día/mes/año; se
oculta medianoche, no horas reales ni fracciones. Categorías y formatos desconocidos
se mantienen literales. Todos los datos se insertan con textContent, nunca como HTML.
Es presentación: no se modifican consultas, planes, resultados guardados ni caché IA.

Verificación específica: `node --test tests/chat_presentation.test.cjs`, suite Python
y revisión del historial existente, sin consultas nuevas al proveedor. A 390 px la
tabla conserva sus 570 px desplazables dentro del contenedor, sin desbordar la página.
