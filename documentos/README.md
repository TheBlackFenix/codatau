# Documentos de entrega de CoDataU

En esta carpeta dejo organizada la documentación del cierre de desarrollo. La idea es que se pueda instalar el proyecto, usarlo y entender cómo está construido sin tener que reconstruir toda la historia del repositorio.

| Punto | Documento | Qué contiene |
|---|---|---|
| 2.7 | [Código fuente, scripts e instalación](2.7_codigo_fuente_e_instalacion.md) | Contenido de la entrega, configuración, arranque, pruebas y generación del ZIP. |
| 2.8 | [Manual de usuario](2.8_manual_de_usuario.md) | Recorrido por la aplicación, decisiones de limpieza y ejemplos. Incluye espacios señalados para agregar capturas. |
| 2.9 | [Informe técnico](2.9_informe_tecnico.md) | Arquitectura, diagramas, modelo de datos, requisitos, pruebas, decisiones y límites del MVP. |

Los scripts de apoyo están en [scripts](scripts/). No contienen credenciales ni datos de usuarios.

## Cómo agregar las capturas

El manual tiene 16 recuadros identificados como «Espacio para captura», con una descripción de qué conviene mostrar. Se pueden reemplazar por imágenes guardadas en una subcarpeta `capturas/`, evitando contraseñas, claves de API o información personal. Por ejemplo:

```markdown
![Pantalla de carga de archivos](capturas/03_carga_archivo.png)
```

La numeración es una ayuda para completar el documento, no una restricción: se pueden combinar o retirar capturas si el recorrido se entiende mejor.

## Versión y evidencia

La base de desarrollo de esta entrega es el commit `e16c522` de la rama `main`. Los cambios posteriores de esta carpeta corresponden a documentación y herramientas de entrega. El ZIP generado registra el commit exacto del que se obtuvo el código.

Los conteos de pruebas del informe corresponden a la verificación indicada allí; no son una garantía de que no puedan aparecer errores nuevos. Tampoco presento como terminadas las mejoras que quedaron fuera del MVP.
