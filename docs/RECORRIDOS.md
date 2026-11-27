# Velocidades y recorridos

El módulo `/traffic` consulta Traffic View cada dos minutos, independientemente del archivo de alertas. La URL privada se instala en `/etc/waze-monitor/traffic.url` (root:waze-monitor, 0640); nunca se incorpora al repositorio, al HTML ni a los logs. El servicio web solo lee `/monitor/traffic.sqlite3`. `WAZE_TRAFFIC_PATH` permite cambiar esa ubicación.

## Qué mide

- Velocidad media estimada del recorrido: `length / time * 3.6`, con metros y segundos. No es velocidad instantánea ni velocidad individual de cada segmento.
- Velocidad habitual: la misma fórmula con `historicTime`. Es la referencia que Waze entrega para el día y horario, no un promedio propio calculado con nuestro archivo.
- Tiempo actual, tiempo habitual, diferencia de tiempos y nivel de congestión.
- Geometría, origen, destino, nombre e identificador del recorrido.
- Subrecorridos y eventos de tráfico inusual, cuando aparecen, se conservan con sus geometrías y métricas. Esta primera vista muestra el historial de los recorridos configurados y el resumen actual del área.
- Longitud de congestión e indicador relativo de usuarios por nivel. No mide el número real de vehículos; cero no prueba que no haya tránsito.

Especificación: https://support.google.com/waze/partners/answer/13658466?hl=en

## Altas, bajas y cambios

No hay una lista fija de calles en Vía. Se aceptan los recorridos que vengan del área configurada en Traffic View, incluso si atraviesan el límite municipal. El identificador de Waze distingue recorridos con nombres iguales. Agregar un recorrido inicia su historial en la siguiente captura; quitarlo conserva sus observaciones y permite consultarlo en los períodos donde existió. «Fuera del seguimiento actual» significa que no figura en la última captura válida, no prueba una baja definitiva. Si reaparece con el mismo ID, continúa su historial; si tiene otro ID no se fusiona por nombre.

Los cambios de geometría, distancia, nombre u origen/destino crean una versión. Los promedios horarios se separan por versión, evitando combinar trayectos diferentes. El mapa muestra la última versión actual, o la última del período si ya no está en el feed. La tabla indica versión, distancia y nombre. No se interpolan puntos entre huecos.

## Archivo y calidad

`traffic_readings` registra intentos, hora de fuente, estado y resumen del área; `traffic_versions` guarda geometrías sin repetirlas; `traffic_samples` conserva cada estimación nueva. Un `updateTime` repetido se registra para salud de captura pero no duplica muestras. Valores negativos, ausentes o cero de tiempo se muestran como desconocidos, nunca como velocidad cero. Una lectura inválida o anterior a la última aceptada falla de forma completa y conserva la última válida. El panel advierte fallas y desactualización; muestra los huecos mayores a seis minutos, incluidos los extremos del período seleccionado.

El gráfico usa promedio aritmético de estimaciones por hora y versión (cada tres horas en consultas de más de 31 días), no ponderado por vehículos. Consultas de hasta 93 días, acceso autenticado para todos los roles de lectura. El historial comienza al instalar este recolector; no se reconstruyen datos anteriores. La vista actual no cambia con el filtro histórico.

## Operación

Instalar `collect_traffic.py` y `traffic_store.py` en `/opt/waze-monitor/`, junto a las dependencias existentes `collect_waze.py`, `monitoring.py` y `observation_store.py`. Directorio de datos `/var/lib/waze-monitor` propiedad waze-monitor:www-data con modo 2750; base 0640, para lectura del contenedor con grupo 33.

Cron `/etc/cron.d/waze-traffic`, usuario `waze-monitor`, cada dos minutos, `flock -n /var/lib/waze-monitor/traffic.lock`, límite de 45 segundos; ejecutar `/usr/bin/python3 /opt/waze-monitor/collect_traffic.py`. No modifica el recolector de alertas.

Respaldo diario separado con `backup_observations.py --database /var/lib/waze-monitor/traffic.sqlite3 --directory /var/lib/waze-monitor/traffic-backups --keep 7`, bloqueo `traffic-backup.lock` y límite de 300 segundos. Las siete copias comprimidas rotan; el archivo principal no se purga. Usar copia SQLite consistente, no copiar el archivo vivo directamente.

Imagen desplegada: `waze-closures:20261127-traffic`. Para revertir la interfaz, restaurar la imagen anterior y los archivos respaldados de `/opt/waze-closures/deploy-backups/`. Se puede deshabilitar solo el cron `waze-traffic` conservando su base y respaldos; el archivo de alertas sigue operando independientemente.

## Verificación

`tests/test_traffic.py` cubre cálculo/unidades, datos desconocidos, fuente repetida, antigüedad/error, alta/baja/reaparición, cambio de trazado, subrecorridos, indicadores, respaldos, límites temporales y acceso autenticado. Se probó el panel en navegador con los 10 recorridos de una captura real, selección de Esperanza, detalle horario y ausencia de errores JavaScript.
