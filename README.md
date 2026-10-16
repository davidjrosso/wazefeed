# Vía Municipal · Waze Río Tercero

Panel web para gestionar cierres de calles y consultar alertas de tránsito en Río Tercero, Córdoba, Argentina. Este repositorio presenta el código del proyecto asociado a [davidrosso.ar/waze](https://davidrosso.ar/waze/) para evaluación técnica por una empresa.

**Entrega de código: 16 de octubre de 2026.** Incluye aplicación, catálogo geográfico, interfaz y pruebas. La instalación local comienza sin cierres activos y con credenciales creadas por quien la evalúa.

## Qué permite hacer

- Seleccionar tramos en un mapa, revisar y publicar cierres en un feed CIFS.
- Reabrir calles, guardar plantillas y configurar una agenda de cierres.
- Administrar cuentas con roles de administración, operación y consulta.
- Consultar alertas Waze y registrar su seguimiento municipal con historial.
- Mantener estado persistente en SQLite, control de versiones e idempotencia de solicitudes.

**Publicar en el panel significa actualizar el feed de esta aplicación.** La incorporación efectiva de un cierre en Waze depende de la integración y validación del proveedor. El repositorio no acredita una aprobación de Waze ni permite modificar sus alertas de origen.

## Recorrido recomendado para la empresa

1. Leer [la guía de evaluación](docs/EVALUACION.md): alcance, recorrido funcional y puntos a revisar.
2. Ejecutar la [instalación local](docs/INSTALACION.md), sin conexiones a cuentas Waze reales.
3. Consultar [arquitectura y API](docs/ARQUITECTURA.md).
4. Revisar [despliegue y operación](docs/DESPLIEGUE.md), [seguridad](SECURITY.md) y [verificación](docs/VERIFICACION.md).

## Inicio rápido

Requisitos: **Python 3.12** y las dependencias de `requirements.txt`. El servicio usa la biblioteca estándar y datos de zona horaria de `tzdata`; Gunicorn se utiliza en Linux para producción. Node.js permite ejecutar las pruebas del cliente.

```sh
git clone https://github.com/davidjrosso/wazefeed.git
cd wazefeed
python -m venv .venv
```

Seguí los comandos de [instalación para Windows o Linux](docs/INSTALACION.md) para crear una contraseña, inicializar la base y abrir `http://127.0.0.1:8766/waze/`.

## Organización

| Ruta | Contenido |
| --- | --- |
| `app.py`, `wsgi.py` | Aplicación WSGI, rutas HTTP, sesiones y persistencia |
| `core.py`, `operations.py`, `direct.py`, `agenda.py` | Cierres, operaciones, plantillas y calendario |
| `accounts.py` | Cuentas, permisos y auditoría |
| `monitoring.py`, `collect_waze.py` | Seguimiento de alertas y recolector independiente |
| `catalog.py`, `config/`, `tools/` | Catálogo de calles y herramientas geográficas |
| `templates/`, `static/` | Interfaz HTML/CSS/JavaScript y Leaflet |
| `tests/` | Pruebas Python y JavaScript con datos aislados |
| `deploy/`, `Dockerfile`, `compose.yaml` | Referencias de despliegue a adaptar |
| `docs/` | Documentación de evaluación y operación |

## Pruebas

Desde la raíz del repositorio:

```sh
python -m unittest discover -s tests -v
node --test tests/publication.test.cjs
```

La integración de Gunicorn sobre socket Unix se omite cuando el entorno no es compatible. Consultá los resultados y límites de esta entrega en [VERIFICACION.md](docs/VERIFICACION.md).

## Alcance de la entrega y derechos

Se publica una copia independiente del código, sin historial del espacio de trabajo, bases de producción, usuarios, sesiones, logs, credenciales ni URL privada del feed Waze. Se excluyen scripts de migración y diagnósticos propios de la infraestructura original.

El código propio no tiene una licencia abierta concedida en esta entrega; cualquier autorización de uso o explotación debe acordarse con su titular. Las dependencias y los datos conservan sus licencias: ver [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Waze es una marca de su titular; este proyecto no se presenta como producto oficial de Waze.
