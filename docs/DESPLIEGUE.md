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

`collect_waze.py` acepta `--secret`, `--output` y `--city`. Sus valores predeterminados se corresponden con el cron incluido. Para usar ese ejemplo, instalar `collect_waze.py` y `monitoring.py` en `/opt/waze-monitor/`, copiar `config/city.json` como `/opt/waze-monitor/city.json`, crear el usuario `waze-monitor` sin acceso interactivo y preparar directorios con permisos restrictivos. El usuario debe escribir solo su caché y archivo de bloqueo; no acceder a la base de cierres.

El archivo `deploy/waze-monitor.cron` muestra una ejecución cada dos minutos con exclusión mutua y timeout. La caché se monta en el panel como solo lectura. Si falla la consulta, se conservan observaciones anteriores y se marca la fuente desactualizada.

## Respaldos y recuperación

`python manage.py backup --output /ruta/segura/respaldo.sqlite3` crea una copia SQLite consistente del archivo configurado en `WAZE_DB_PATH`. No sobrescribe un respaldo existente. Proteger la copia: contiene cuentas, sesiones e historial.

Para restaurar, detener escritores, conservar la base actual, verificar el respaldo y recuperar en una instancia aislada antes de sustituir producción. Acordar el procedimiento para archivos WAL/SHM y permisos con el administrador. No usar `init-db` para recuperar una base perdida.

## Validación externa

El repositorio no contiene evidencias actuales de funcionamiento del servidor ni acceso a sus respaldos. Las pruebas de esta entrega son locales. La aprobación del feed, su lectura por Waze, las condiciones de servicio y la capacidad de la instalación deben verificarse por separado.
