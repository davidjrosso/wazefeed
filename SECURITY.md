# Seguridad

Esta entrega excluye datos de operación, contraseñas, hashes de producción, sesiones, URL privada de Partner Hub y registros internos de infraestructura. Las contraseñas y URL ficticias presentes en las pruebas son exclusivamente fixtures.

## Controles implementados

- Contraseñas derivadas con PBKDF2-SHA256 y sal; creación interactiva.
- Sesiones con expiración, validación de origen, CSRF y limitación de intentos.
- Roles aplicados en el servidor; historial de cambios y controles de revisión.
- HTTPS requerido fuera de pruebas explícitas en loopback.
- Referencia Docker con usuario no privilegiado, sistema de archivos de solo lectura y sin red para el panel.
- Recolector separado, validación de URL, límites de descarga y caché atómica.

Estos controles no equivalen a una auditoría de seguridad independiente. Antes de un despliegue nuevo, revisar dependencias, permisos efectivos, encabezados del proxy, concurrencia, copias de seguridad y restauración.

## Manejo de información sensible

No versionar archivos `.env`, bases SQLite, hashes, claves, cookies, cachés de alertas ni URL de Partner Hub. El archivo `.gitignore` reduce errores, pero no reemplaza una revisión antes de publicar.

Los respaldos contienen cuentas, sesiones e historial: deben protegerse como datos de producción. No adjuntar información sensible a issues públicos. Para reportar una vulnerabilidad, coordinar un canal privado con el responsable del proyecto antes de enviar detalles o una prueba de explotación.
