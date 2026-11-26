# Arquitectura y API

## Flujo principal

```mermaid
flowchart LR
    U[Navegador: HTML, JS, Leaflet] --> P[Proxy HTTPS]
    P --> A[Aplicación Python WSGI]
    A --> D[(SQLite)]
    A --> C[Catálogo local OSM]
    A --> F[Feed CIFS público]
    W[Waze: lector autorizado] --> F
    R[Recolector independiente] --> H[API Partner Hub autorizada]
    R --> J[Caché JSON]
    A -->|solo lectura| J
```

El backend usa la biblioteca estándar de Python y Gunicorn como servidor de producción. La interfaz usa JavaScript sin compilación y una copia de Leaflet incluida en `static/vendor/leaflet/`. El navegador solicita las teselas de mapa al proveedor configurado.

SQLite almacena el estado operativo, cuentas, sesiones e historiales. Las escrituras se realizan en transacciones; la capa operativa valida revisiones y solicitudes repetidas para evitar publicaciones duplicadas. Las sesiones se vinculan a las credenciales para invalidarlas cuando cambia el acceso.

`operations.py`, `direct.py` y `agenda.py` concentran las reglas funcionales. `app.py` aplica autenticación, autorización, validación HTTP y persistencia. `core.py` genera el feed con eventos futuros, activos y finalizados hace menos de 48 horas; ver [la guía del feed CIFS](FEED_CIFS.md). `catalog.py` carga el catálogo y `tools/build_catalog.py` permite regenerarlo explícitamente.

El recolector es un proceso separado con acceso de red. Valida la URL esperada, rechaza redirecciones y escribe una caché atómica. La aplicación lee esa caché sin necesitar acceso de red al proveedor. Los estados y notas municipales se guardan en SQLite, sin modificar la alerta en Waze.

## Superficie HTTP

Todos los ejemplos usan el prefijo `/waze`. Las rutas públicas no requieren sesión; las demás aplican autenticación y permisos.

| Método y ruta | Uso |
| --- | --- |
| `GET /waze/`, `/waze/login`, `/waze/legacy` | Páginas públicas; acciones operativas protegidas |
| `GET /waze/healthz` | Salud del servicio |
| `GET /waze/feed.json` | Feed CIFS público con cierres programados, activos y recientemente finalizados |
| `GET /waze/roads.json`, `/waze/sector.geojson` | Geometrías públicas |
| `POST /waze/api/login` | Autenticar con usuario y contraseña |
| `POST /waze/api/logout` | Invalidar sesión |
| `GET /waze/api/state` | Estado operativo y token CSRF de la sesión |
| `POST /waze/api/state` | Cambios operativos sujetos a rol, revisión y acción |
| `GET /waze/api/catalog` | Catálogo y configuración de ciudad |
| `GET /waze/api/requests/{id}` | Resultado de una solicitud de la propia cuenta |
| `GET /waze/api/agenda/{id}` | Vista previa de agenda de plantilla |
| `GET, POST /waze/api/users` | Gestión de usuarios para administración |
| `GET, POST /waze/api/monitor` | Consulta y seguimiento municipal de alertas |
| `GET /waze/api/monitor/{id}` | Historial de una alerta |
| `GET /waze/history` | Panel de históricos y estadísticas (datos sujetos a sesión) |
| `GET /waze/api/history` | Resumen, gráficos y tabla paginada del archivo de solo lectura |
| `GET /waze/api/history/{id}` | Versión del período, cambios de Waze y últimas acciones municipales |

Las mutaciones autenticadas requieren cookie de sesión y `X-CSRF-Token`, además de las verificaciones de origen. La gestión de cuentas pide confirmar la contraseña del administrador. Las pruebas y el cliente son la referencia de los cuerpos JSON; no se ofrece aún un contrato OpenAPI versionado.

## Roles

- **Administración:** operación y administración de cuentas.
- **Operación:** gestión de cierres y seguimiento de alertas, según las reglas de cada acción.
- **Consulta:** lectura; sin cambios ni publicación de cierres.

## Adaptación a otro municipio

Revisar `config/city.json`, catálogo, geometrías, zona horaria y atribuciones. No basta con cambiar el título: es necesario validar sentidos, tramos, área operativa y correspondencia con Waze. La arquitectura actual es de una instancia por configuración municipal.


### Análisis y seguimiento de históricos

`history_dashboard.py` consulta el archivo con una transacción consistente y límite de consulta. La comparación usa una ventana anterior desplazada por el número de días elegidos, con la misma duración transcurrida si incluye hoy. Los filtros usan la última versión observada dentro de cada ventana, independientemente. Solo se calcula variación porcentual con cobertura de ambas ventanas, sin huecos superiores a seis minutos ni lecturas parciales; una base cero no produce porcentaje.

El mapa agrupa UUID distintos en celdas aproximadas de 100 × 100 m usando una proyección local para Río Tercero. No agrupa incidentes ni demuestra recurrencia del mismo problema. Muestra hasta 100 zonas y 50 enlaces de reportes por zona. La presencia por hora suma pares (día, UUID); varias consultas en la misma hora no aumentan el contador. Las franjas sin lecturas correctas no se presentan como cero.

Permanencia significa lapso entre primera y última observación del período, no duración del incidente. Las señales se agrupan por versiones consecutivas (hasta 500 visibles) y las ausencias nunca se presentan como resolución. `history_notes` vive en la base operativa `state.sqlite3`, separada del archivo de solo lectura. Son entradas inmutables con autor autenticado, fecha, estado y fundamento (2000 caracteres). El formulario muestra las últimas 100; no elimina anteriores. Los operadores y administradores pueden agregar, los lectores solo consultar. Revisión optimista, CSRF y control de origen impiden sobrescrituras y envíos no autorizados. Esta investigación es independiente de los estados operativos de Monitoreo.
