# Guía para evaluación empresarial

## Objetivo

Evaluar la funcionalidad y mantenibilidad de un panel municipal de tránsito, y estimar el trabajo necesario para su integración, soporte o evolución. Esta entrega permite revisar el código y ejecutar una instancia independiente; no incluye acceso al sistema municipal ni compromisos de disponibilidad.

## Recorrido funcional sugerido

1. Instalar localmente y confirmar que el feed comienza vacío.
2. Iniciar sesión, ubicar calles y seleccionar tramos en el mapa.
3. Revisar el resumen antes de confirmar un cierre de prueba; inspeccionar el feed local.
4. Reabrir los tramos y verificar su efecto en el feed.
5. Guardar una plantilla; comprobar que guardarla no publica por sí sola.
6. Explorar agenda, activación, pausa y excepciones. Evaluar la zona horaria municipal.
7. Crear cuentas de operación y consulta; contrastar sus permisos.
8. Revisar las pruebas de monitoreo para conocer el flujo de alertas sin una URL privada real.

## Qué está incluido

- Interfaz de operación y pantalla anterior en `/waze/legacy`.
- Catálogo de calles de Río Tercero y geometría de un sector inicial.
- Autenticación, roles, controles CSRF, revisión de cambios e historial.
- Feed de cierres CIFS, plantillas y programación.
- Recolector de alertas separado del servicio de cierres.
- Seguimiento municipal de alertas con notas y estados.

## Límites relevantes

- La lectura del feed por Waze y su aceptación deben verificarse con el proveedor y la cuenta correspondiente. El panel no confirma automáticamente que un cierre ya se vea en Waze.
- La geometría proviene de OpenStreetMap; no acredita coincidencia exacta con la geometría interna de Waze.
- El área configurada es operativa, no un límite catastral.
- El monitoreo procesa alertas; no incluye análisis histórico de congestión ni notificaciones externas.
- Una alerta que deja de aparecer no se considera automáticamente resuelta. La derivación municipal no envía correos.
- SQLite y el diseño actual requieren evaluar concurrencia, crecimiento, retención y recuperación para cada instalación.
- La agenda se materializa al consultar el estado o feed; no es un planificador distribuido independiente.
- Las plantillas de despliegue requieren adaptar dominio, permisos, almacenamiento y proxy.

## Puntos para acordar con la empresa

| Área | Preguntas para la evaluación |
| --- | --- |
| Producto | ¿Qué flujos, indicadores e integraciones adicionales se necesitan? |
| Waze | ¿Quién gestiona Partner Hub, validación del feed y soporte del proveedor? |
| Datos | ¿Quién valida calles, sentidos, geometría y actualización del catálogo? |
| Operación | ¿Qué disponibilidad, respaldo, restauración y monitoreo se requieren? |
| Seguridad | ¿Se necesita SSO, MFA, revisión externa o políticas adicionales de retención? |
| Escala | ¿Qué volumen de usuarios, municipios y eventos debe soportar? |
| Derechos | ¿Qué condiciones de uso, mantenimiento y explotación se acuerdan? |

No se presentan las pruebas automatizadas como auditoría independiente ni como garantía de aptitud para cualquier despliegue.
