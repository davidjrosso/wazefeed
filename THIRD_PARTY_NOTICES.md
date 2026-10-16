# Componentes y datos de terceros

- **Leaflet:** distribuido en `static/vendor/leaflet/`; su licencia original se conserva en [LICENSE](static/vendor/leaflet/LICENSE).
- **OpenStreetMap:** geometría y catálogo derivados de datos OSM. © OpenStreetMap contributors, ODbL. Ver [procedencia del sector](config/PROCEDENCIA.md), metadatos del catálogo y [atribución de OSM](https://www.openstreetmap.org/copyright). La zona operativa no representa un límite oficial.
- **Python, Gunicorn y tzdata:** entornos y dependencias externos con sus respectivas licencias. `requirements.txt` fija las dependencias; no se redistribuyen sus paquetes en este repositorio. `tzdata` aporta la base de zonas horarias para entornos que no la incluyen.
- **Waze:** nombre, marca y servicios pertenecen a sus titulares. El acceso al feed y su uso requieren las autorizaciones y condiciones aplicables del proveedor. No se distribuyen credenciales de Partner Hub.

El código propio se publica para revisión sin añadir una licencia abierta. La publicación del repositorio no sustituye un acuerdo de uso o explotación con su titular, ni modifica las licencias de los componentes y datos de terceros.
