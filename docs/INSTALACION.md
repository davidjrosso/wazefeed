# Instalación local para evaluación

Usar Python 3.12 y una copia independiente del repositorio. Ejecutar todos los comandos desde su raíz. No copiar bases ni credenciales del servidor existente.

## Windows · PowerShell

```powershell
python -m venv .venv
$python = Join-Path (Get-Location) '.venv/Scripts/python.exe'
& $python -m pip install -r requirements.txt
$env:WAZE_LOCAL_TEST = '1'
$env:WAZE_PUBLIC_URL = 'http://127.0.0.1:8766/waze'
$env:WAZE_DB_PATH = Join-Path (Get-Location) 'data/state.sqlite3'
$env:WAZE_PASSWORD_HASH_FILE = Join-Path (Get-Location) 'secrets/operator.hash'
$env:WAZE_MONITOR_PATH = Join-Path (Get-Location) 'data/monitor.json'
$env:WAZE_TRUSTED_PROXIES = ''
& $python manage.py password --output $env:WAZE_PASSWORD_HASH_FILE
& $python manage.py init-db
& $python manage.py dev --port 8766
```

## Linux / macOS · shell

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export WAZE_LOCAL_TEST=1
export WAZE_PUBLIC_URL=http://127.0.0.1:8766/waze
export WAZE_DB_PATH="$PWD/data/state.sqlite3"
export WAZE_PASSWORD_HASH_FILE="$PWD/secrets/operator.hash"
export WAZE_MONITOR_PATH="$PWD/data/monitor.json"
export WAZE_TRUSTED_PROXIES=''
python manage.py password --output "$WAZE_PASSWORD_HASH_FILE"
python manage.py init-db
python manage.py dev --port 8766
```

Abrir <http://127.0.0.1:8766/waze/>. Usuario inicial: `operador`. Ingresar la contraseña elegida en el paso interactivo; debe incluir mayúscula, minúscula, número y signo, con al menos ocho caracteres. La cuenta inicial tiene funciones de administración; crear cuentas nominales para diferenciar roles.

`password` e `init-db` se ejecutan solo en la primera instalación. En sesiones posteriores volver a definir las variables y ejecutar únicamente `dev`. Ambos comandos rechazan sobrescribir archivos existentes. Detener el servidor con Ctrl+C.

## Comprobaciones

- `/waze/healthz` debe devolver `{"status":"ok"}`.
- `/waze/feed.json` debe comenzar como `{"incidents":[]}`.
- La página muestra el aviso de entorno local.
- Se puede crear una cuenta de consulta y comprobar que no publica cierres.
- Monitoreo aparece sin fuente disponible hasta configurar un recolector autorizado. Eso no impide evaluar el resto del panel.

La aplicación local no envía cierres a Waze: expone un feed local. No registrar esta instancia de evaluación en Partner Hub. El navegador descarga las teselas del mapa desde OpenStreetMap y algunos enlaces abren sitios externos.

## Configuración

| Variable | Función |
| --- | --- |
| `WAZE_PUBLIC_URL` | Origen y prefijo público; requiere HTTPS fuera de pruebas en loopback |
| `WAZE_DB_PATH` | Archivo SQLite persistente |
| `WAZE_PASSWORD_HASH_FILE` | Archivo de hash creado por `manage.py password` |
| `WAZE_OPERATOR_USER` | Nombre de cuenta inicial; por defecto `operador` |
| `WAZE_LOCAL_TEST` | `1` solo para evaluación local |
| `WAZE_MONITOR_PATH` | Caché JSON escrita por el recolector independiente |
| `WAZE_CATALOG_PATH` | Catálogo alternativo opcional |
| `WAZE_TRUSTED_PROXIES` | Redes de proxies confiables; vacío en esta prueba |
| `WAZE_TRUST_UNIX_SOCKET` | Confianza en cabeceras del proxy sobre socket protegido |

El programa lee variables del proceso: no carga automáticamente archivos `.env`. `deploy/server.env.example` es una referencia para el administrador del servicio.
