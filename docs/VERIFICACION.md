# Verificación de la entrega

Fecha: **16 de octubre de 2026**. Ejecución sobre la copia pública preparada, en Windows con Python 3.12.14 y `tzdata==2025.2`.

| Comprobación | Resultado |
| --- | --- |
| `python -m unittest discover -s tests -q` | 178 pruebas descubiertas: 177 aprobadas y 1 omitida |
| `node --test tests/publication.test.cjs` | 7 aprobadas, sin fallos |
| `git diff --cached --check` | Sin errores de espacios en la entrega preparada |
| Revisión de archivos a publicar | Sin bases, hashes de producción, sesiones, entornos virtuales ni URL privada del feed |

La prueba omitida requiere Linux y Gunicorn sobre socket Unix. No se ejecutó una compilación Docker ni una validación de producción Linux en esta entrega. El descubrimiento de `unittest` puede contar casos heredados/importados en más de un módulo: el total no representa 178 escenarios funcionales únicos.

La primera ejecución detectó ausencia de datos de zona horaria en Windows. Se agregó `tzdata` a las dependencias y se condicionó Gunicorn a plataformas distintas de Windows. La ejecución final utilizó un entorno virtual independiente con esas dependencias instaladas. Las pruebas HTTP se ejecutaron con acceso permitido a loopback; la primera ejecución dentro del entorno restringido no permitía esas conexiones.

## Cobertura funcional de las suites

- Sesiones, autenticación, controles CSRF y límites de intentos.
- Roles, cuentas e invalidación de acceso.
- Cierres, geometría, plantillas, agenda y validaciones operativas.
- Seguimiento de alertas y manejo de caché.
- Recuperación de solicitudes pendientes e idempotencia del cliente.

La revisión previa a publicación incluyó búsqueda de patrones de credenciales, claves privadas, direcciones internas y artefactos de producción en los archivos seleccionados. No es una certificación de ausencia de vulnerabilidades.

No se conectó esta copia al servidor existente ni a un feed privado Waze. No se publicaron cierres de prueba en producción. La disponibilidad del sitio y la aceptación del feed por Waze no forman parte de estos resultados.
