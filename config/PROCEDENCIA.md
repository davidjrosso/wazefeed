# Geometría del sector

Datos descargados de la API pública de OpenStreetMap el 2026-10-06:
`https://api.openstreetmap.org/api/0.6/map?bbox=-64.120,-32.177,-64.113,-32.170`

© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), licencia ODbL. Las coordenadas de `segments.json` se almacenan como `[latitud,longitud]`. GeoJSON invierte ese orden.

| Tramo | ID Waze | Nodos OSM en sentido del tránsito |
|---|---:|---|
| Alsina: Intendente Abrile → Bolívar | 196400244 | 2000218799 → 2000218815 |
| Alsina: Bolívar → Sarmiento | 196400243 | 2000218815 → 2000218861 |
| Bolívar: 25 de Mayo → Alsina | 196902067 | 2000218859 → 2000218815 |
| Bolívar: Alsina → Deán Funes | 196902066 | 2000218815 → 2000218785 |

Cada borde recto de OSM se representó con sus extremos y un punto medio interpolado, sin atribuir al punto medio precisión independiente. Todos los cierres usan `ONE_DIRECTION`.

[Selección de los cuatro segmentos en Waze](https://beta.waze.com/es-419/editor?env=row&lat=-32.17374&lon=-64.11649&zoomLevel=18&segments=196902066,196902067,196400244,196400243).

[Especificación CIFS](https://developers.google.com/waze/data-feed/cifs-specification) · [Requisitos de cierres](https://developers.google.com/waze/data-feed/road-closure-information) · [Alojamiento del feed](https://developers.google.com/waze/data-feed/feed-setup).

No se afirma que OSM sea la geometría exacta del mapa interno de Waze. La aceptación y asociación final de estas líneas debe comprobarse con Waze.
