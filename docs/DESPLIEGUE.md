# Despliegue y operación

## Referencia de producción

La entrega incluye `Dockerfile`, `compose.yaml` y un fragmento Apache para **Linux**. Son una referencia para adaptar a la infraestructura de destino, no un instalador listo para cualquier servidor. La evaluación local está documentada por separado.

La topología prevista es proxy HTTPS → socket Unix → Gunicorn, con el prefijo `/waze/` preservado. El contenedor funciona sin red, como UID 10001 y grupo 33, con sistema de archivos de solo lectura. El socket debe ser accesible únicamente para el servicio y el proxy autorizado.

Antes de ponerlo en marcha, el administrador debe:

1. Configurar dominio y TLS, y ajustar `WAZE_PUBLIC_URL` en Compose.
2. Crear directorios de datos, secretos, socket y caché con propietarios y permisos compatibles con los usuarios reales del servidor.
3. Construir la imagen con `docker compose build`.
4. Crear el hash de credencial interactivamente con `manage.py password` e inicializar una base vacía con `manage.py init-db`, usando las variables y montajes del destino. La inicialización requiere un proceso con permiso de escritura sobre el directorio de datos.
5. Montar los secretos como solo lectura y la base sobre almacenamiento persistente. No activar `WAZE_LOCAL_TEST` en producción.
6. Adaptar `deploy/apache-location.conf` al VirtualHost HTTPS y habilitar los módulos de proxy, cabeceras y socket requeridos. El ejemplo asume terminación TLS en ese proxy; si hay otro proxy frontal hay que revisar explícitamente la cadena de confianza.
7. Iniciar `docker compose up -d`, verificar salud, inicio/cierre de sesión, roles y feed vacío antes de operar.
8. Configurar respaldo y restauración, retención, monitoreo y renovación TLS.

No publicar el backend directamente ni confiar en cabeceras enviadas por el cliente. `WAZE_TRUST_UNIX_SOCKET=1` solo corresponde a un socket protegido detrás de un proxy que reemplaza las cabeceras.

## Recolector opcional de alertas

Requiere una URL autorizada de Partner Hub. No incluirla en el repositorio, argumentos de procesos, navegador ni logs. Guardarla en un archivo privado legible únicamente por el usuario dedicado del recolector.

`collect_waze.py` acepta `--secret`, `--output`, `--city` y `--history`. Este último usa por defecto `history.sqlite3` junto a la caché. Sus valores predeterminados se corresponden con el cron incluido. Para usar ese ejemplo, instalar `collect_waze.py`, `monitoring.py` y `observation_store.py` en `/opt/waze-monitor/`, copiar `config/city.json` como `/opt/waze-monitor/city.json`, crear el usuario `waze-monitor` sin acceso interactivo y preparar directorios con permisos restrictivos. El usuario escribe su caché, histórico y archivo de bloqueo; no accede a la base de cierres. El recolector requiere Python 3.9 o posterior y solo biblioteca estándar.

El archivo `deploy/waze-monitor.cron` muestra una ejecución cada dos minutos con exclusión mutua y timeout. La caché se monta en el panel como solo lectura. Si falla la consulta, se conservan observaciones anteriores y se marca la fuente desactualizada.

### Histórico continuo de reportes

Cada consulta registra su resultado en `/var/lib/waze-monitor/history.sqlite3`, aunque nadie abra el panel. El cron del sistema vuelve a ejecutarse después de reiniciar el servidor. No instalar un segundo temporizador para el mismo recolector. La base es independiente de `state.sqlite3`; la recolección no publica cierres ni cambia decisiones municipales.

- `readings`: intentos, hora local de consulta en UTC, hora del feed, resultado, cantidades y fuentes repetidas. Un intento fallido no equivale a cero reportes. Un equipo apagado deja un hueco temporal entre intentos; no se inventan lecturas durante ese hueco.
- `reports`: un registro por UUID, publicación informada por Waze, primera/última observación y presencia en la última lectura completa aplicable.
- `versions` y `sightings`: contenido normalizado de cada versión y relación de cada reporte observado con la consulta. Repetir un UUID no crea un reporte nuevo; cada lectura queda registrada, incluso si Waze repite la misma hora de fuente.
- `transitions`: nuevo, cambiado, ausente, reaparecido e importado. Ausencia no significa resolución ni indica el instante exacto en que terminó el hecho.

Se guardan alertas válidas dentro de `config/city.json`; registros inválidos y externos se contabilizan, pero no se archivan como reportes. Un cambio de límites requiere una migración explícita o un nuevo archivo histórico, para no simular desapariciones por haber achicado el área. Esta etapa no archiva `jams`, videos, identidades de reportantes ni el JSON íntegro: conserva los campos normalizados de las alertas, incluidos coordenadas, descripción, tipo, confianza y fecha de publicación.

No hay borrado automático del histórico. La retención de 30 días y el límite de la caché de pantalla no afectan a esta base. Supervisar espacio libre y acordar una política de conservación si crece. La primera ejecución importa los registros disponibles en la caché conservando primera/última observación y marcándolos como importados. No reconstruye lecturas o cambios anteriores a la instalación.

La escritura es transaccional y se confirma antes de actualizar la caché. Si falla la base, la caché conserva sus datos anteriores y publica `meta.error=archive_failed`; la ejecución devuelve un código no cero. Si falla la caché después de confirmar la base, lo archivado no se pierde. No recrear ni borrar la base ante una falla; diagnosticar permisos, disco y respaldos.

El endpoint autenticado existente `GET /waze/api/monitor` devuelve en `meta.archive` inicio del archivo, última lectura archivada correcta, estado y cantidades de reportes/consultas. El panel de análisis está en `/waze/history`; el cruce con cámaras corresponde a una etapa posterior.

### Panel de históricos y estadísticas

Desde el menú de Vía, abrir **Históricos y estadísticas**, o entrar a `/waze/history`. Usa la sesión existente y admite cuentas de administración, operación y consulta. La página pública solo contiene la estructura; los datos requieren autenticación.

`WAZE_HISTORY_PATH` permite especificar la base. Por defecto se usa `history.sqlite3` junto a `WAZE_MONITOR_PATH`, disponible en el montaje de solo lectura `/monitor`. El panel no crea ni modifica el archivo. Si falta o no se puede consultar, devuelve un error explícito, no estadísticas vacías.

Los filtros admiten hasta 93 días, tipo, búsqueda de calle sin distinción de acentos y fiabilidad mínima. Las fechas son inclusivas en la zona horaria de la ciudad; la consulta interna usa inicio inclusivo y fin exclusivo. La tabla pagina de a 50 reportes. Se usa la última versión observada **dentro del período** para tipo, calle, ubicación y fiabilidad, sin aplicar cambios posteriores a un análisis anterior.

- Reportes distintos: UUID observados en el período, no cantidad de lecturas ni de personas.
- Publicados: entre esos reportes observados y filtrados, los cuya fecha de publicación informada cae en el período. Puede preceder al inicio del archivo; no reconstruye lecturas anteriores.
- Evolución: UUID distintos por día; al consultar un solo día, por hora. Un reporte puede estar en varios intervalos. No sumar esas barras para obtener el total.
- Fiabilidad: promedio y distribución de un valor por reporte (última observación del período). Los valores no informados se excluyen del promedio y se muestran aparte.
- Cobertura: lecturas y fallas de todo el recolector, sin filtros de calle/tipo; las franjas sin lecturas correctas y los huecos mayores de seis minutos se señalan explícitamente.
- Ficha: mapa, fechas, confianza, confirmaciones, cambios de Waze durante el período y últimas 30 acciones municipales con usuario. No se infiere identidad del wazer ni resolución de un incidente.

Las consultas usan una transacción de lectura coherente, límites de período y tiempo de ejecución. Si el archivo crece y un rango excede el tiempo permitido, acotar las fechas. La primera importación se conserva en la base pero solo entra en las estadísticas del período si hay una observación detallada del reporte en ese período.

Ejecutar como `waze-monitor` (o una cuenta con lectura autorizada):

```sh
python3 /opt/waze-monitor/observation_store.py status
python3 /opt/waze-monitor/observation_store.py timeline ID_DEL_REPORTE
python3 /opt/waze-monitor/observation_store.py backup --output /ruta/segura/waze-history-2026-11-26.sqlite3
```

`status` devuelve `stale=true` si pasan más de seis minutos desde la última consulta archivada correcta. `timeline` pagina hasta 500 transiciones; usar `--after ID` para continuar. Las horas se guardan como segundos Unix UTC para convertirlas después a la zona de Argentina. La fecha de publicación no se interpreta como fecha de inicio del incidente.

`backup` usa la API de respaldo de SQLite, verifica integridad y se niega a sobrescribir archivos existentes. Programar respaldos y conservar una copia fuera del servidor; una copia en el mismo disco no cubre una falla del equipo. Para restaurar, pausar el cron, preservar la base dañada, verificar la copia en un entorno aislado y restaurar permisos antes de reactivar. No copiar la base con herramientas de archivos mientras se está escribiendo.

El ejemplo `deploy/waze-history-backup.cron` ejecuta `backup_observations.py` a las 03:17 (hora del servidor). Instalar ese script junto al recolector y el cron como `/etc/cron.d/waze-history-backup`, propiedad de root y modo 0644. Conserva siete respaldos comprimidos en `/var/lib/waze-monitor/backups`; solo elimina copias generadas por ese script después de verificar una nueva. Nunca elimina datos de la base histórica. Se puede ejecutar manualmente bajo la cuenta del recolector para verificar la instalación. El directorio de copias tiene modo 0700 y sus archivos 0600.

Para actualizar una instalación existente, respaldar caché y scripts, adquirir el mismo `collector.lock` que usa el cron, instalar primero `observation_store.py` y después `collect_waze.py` de forma atómica. La versión de `monitoring.py` debe coincidir con esta entrega. Mantener propietario, permisos y la URL privada del servidor. Verificar una lectura manual y al menos dos lecturas programadas, los contadores de `meta.archive` y `PRAGMA integrity_check`. El panel existente puede continuar funcionando durante la actualización del recolector.

## Respaldos y recuperación

`python manage.py backup --output /ruta/segura/respaldo.sqlite3` crea una copia SQLite consistente del archivo configurado en `WAZE_DB_PATH`. No sobrescribe un respaldo existente. Proteger la copia: contiene cuentas, sesiones e historial.

Para restaurar, detener escritores, conservar la base actual, verificar el respaldo y recuperar en una instancia aislada antes de sustituir producción. Acordar el procedimiento para archivos WAL/SHM y permisos con el administrador. No usar `init-db` para recuperar una base perdida.

## Validación externa

El repositorio no contiene evidencias actuales de funcionamiento del servidor ni acceso a sus respaldos. Las pruebas de esta entrega son locales. La aprobación del feed, su lectura por Waze, las condiciones de servicio y la capacidad de la instalación deben verificarse por separado.


### Ampliación de análisis (26/11/2026)

Imagen `waze-closures:20261126-history-analysis`. La inicialización agrega `history_notes` e índice a la base operativa existente; no cambia el esquema del recolector. Respaldar `state.sqlite3` con `manage.py backup` antes de actualizar. Los respaldos completos de la base operativa incluyen las notas; el respaldo de `history.sqlite3` conserva exclusivamente las observaciones. Para revertir la aplicación, volver a la imagen anterior `waze-closures:20261126-history-dashboard`; la tabla adicional es compatible y no se borra.

Probar login, comparaciones, mapa, ficha y guardado de notas en una base de prueba antes de recrear el servicio. Nunca crear notas ficticias en producción para probar. La interfaz se consulta al abrir, cambiar filtros o pulsar Consultar; la recolección sigue siendo automática cada dos minutos.


### Herramientas de investigación (26/11/2026)

Imagen `waze-closures:20261126-history-research`, con nuevo módulo `history_tools.py`. Agrega `history_zones` y `history_links` a la base operativa, incluidas en su respaldo completo. Actualizar también `/opt/waze-monitor/monitoring.py` bajo `collector.lock`, preservando permisos. El esquema del archivo histórico no cambia; los nuevos campos se guardan dentro de versiones JSON desde la primera lectura con el recolector actualizado.

Guardar respaldo de estado y fuentes antes de actualizar; conservar la imagen anterior `waze-closures:20261126-history-analysis`. Para revertir el recolector, restaurar la copia de `monitoring.py` bajo el mismo bloqueo. Las tablas agregadas son compatibles con la versión anterior y no se eliminan. No cargar zonas, relaciones ni revisiones ficticias en producción.
