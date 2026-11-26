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

El endpoint autenticado existente `GET /waze/api/monitor` devuelve en `meta.archive` inicio del archivo, última lectura archivada correcta, estado y cantidades de reportes/consultas. La interfaz de análisis por fechas y el cruce con cámaras son etapas posteriores.

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
