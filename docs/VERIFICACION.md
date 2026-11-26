# Verificación de la entrega

## Panel de históricos — 26 de noviembre de 2026

- Suite completa: 205 pruebas Python, 204 aprobadas y una omitida por requerir Gunicorn/socket Unix en Linux. Después de las últimas mejoras, suite específica de históricos: 13 aprobadas, incluidas paginación y fiabilidad desconocida/decimal.
- JavaScript: sintaxis validada y siete pruebas existentes de publicación aprobadas. IDs del HTML comprobados sin duplicaciones.
- Verificación visual local con copia del archivo real: acceso desde el menú, gráficos, filtro Hoy, filtro de fiabilidad, ficha con mapa y cronología, búsqueda sin resultados y ausencia de errores de consola.
- Imagen de producción probada antes del reemplazo con base de usuarios temporal y archivo real montado en solo lectura: autenticación, rechazo sin sesión, resumen, ficha y recursos estáticos correctos.
- Publicado en `https://davidrosso.ar/waze/history`. Comprobados página HTTP 200, API anónima HTTP 401 y salud HTTP 200. La consulta del día devolvió 22 reportes distintos, dos publicados ese día y fiabilidad promedio 5,3. El recolector continuó registrando lecturas.
- Respaldo consistente del estado de Vía antes del despliegue y copia de los archivos anteriores en `/opt/waze-closures/deploy-backups/history-dashboard-20261126T130216Z`. Imagen anterior conservada para reversión.

La prueba visual usó una copia local, por lo que el indicador de fuente desactualizada allí era esperado. Las comprobaciones del servidor usaron la base activa; no se publicaron cierres ni se alteraron reportes para probar los gráficos.

## Archivo histórico de alertas — 26 de noviembre de 2026

- Suite completa Python: 192 pruebas, 191 aprobadas y una omitida (integración Gunicorn/socket Unix en Windows). Incluye 14 nuevas pruebas del archivo histórico.
- Suite adicional de respaldo: dos pruebas aprobadas, incluida restauración del archivo comprimido y preservación de copias anteriores ante fallas.
- Cliente: siete pruebas aprobadas con Node del runtime local.
- `git diff --check`: sin errores.
- Recolector y módulo histórico compilados con Python 3.9.2 del servidor antes de instalar; las ejecuciones programadas usan ese intérprete.
- Instalado en el recolector existente de Vía. Se preservaron scripts y caché anteriores bajo `/var/lib/waze-monitor/pre-history-20261126T115557Z`.
- Inicio del archivo: 26/11/2026 a las 08:56 de Argentina. Se importaron 37 reportes disponibles en caché; 22 presentes en el feed. No se reconstruyeron lecturas anteriores.
- Comprobadas tres lecturas correctas: cron de 08:56, ejecución manual de 08:56 y cron de 08:58. `integrity_check=ok` y sin errores de claves foráneas. El contenedor web siguió saludable, sin reiniciarlo.
- Respaldo comprimido inicial generado y restaurado en una copia local aislada: integridad correcta y 37 reportes. Cron diario instalado a las 03:17 (Argentina) con siete copias locales; la primera ejecución nocturna futura no forma parte de esta verificación.

La base de observaciones vive en `/var/lib/waze-monitor/history.sqlite3`, separada de los cierres. El endpoint autenticado existente propaga `meta.archive`; no se incorporó aún una interfaz de búsqueda histórica ni reproducción de cámaras. No se publicaron cierres de prueba.

## Entrega original

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


### Análisis del histórico y notas

Pruebas en `tests/test_history_dashboard.py`: comparación con versión correspondiente a cada período y corte horario equivalente, cobertura insuficiente, base cero con cobertura completa, UUID distintos frente a consultas repetidas, reportes-día por hora, permanencia y ausencia sin filtrar datos futuros, escritura de notas solo con sesión/CSRF/origen válidos, rechazo de lectores, validación y conflicto de revisiones. Se comprueba que un segundo seguimiento conserva el anterior y atribuye el autor desde la sesión.

Verificación visual con copia del histórico y base operativa de prueba: gráficos, mapa y selección de zona, apertura de ficha, señales y guardado visible de nota con autor/fecha. Las notas de prueba no se suben a producción.
