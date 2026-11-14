# SPHEREx Blink — servidor traductor estilo WiseView

Implementación de `ORACLE_SERVER_WISEVIEW_ARCHITECTURE.md` para el reto *Planet X and SPHEREx* (NASA Space Apps 2026).

## Desplegar (un comando)

En PowerShell, dentro de esta carpeta:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

El script detecta el usuario SSH (`ubuntu`/`opc`/`root`), sube `server/` y ejecuta `server/install.sh` con sudo. Al acabar lanza pruebas de humo contra los datos reales (indica cuántos recortes han salido de AWS S3 y cuántos de IRSA) y deja la web en `http://51.170.50.113/`. Se puede relanzar para actualizar.

Si la web no abre desde fuera, falta la regla de entrada TCP 80 en la *Security List* de la VCN de Oracle Cloud (eso solo se cambia en la consola de Oracle).

## Qué instala

| Pieza | Dónde | Notas |
| --- | --- | --- |
| Backend FastAPI | `/opt/spherex-backend`, venv en `/opt/spherex-env` | `spherex.service`, 1 worker por CPU (máx. 4), solo en 127.0.0.1:8000 |
| Frontend | `/var/www/spherex-frontend` | HTML/JS sin dependencias |
| Redis L1 | `/etc/redis/redis.conf` | `maxmemory` = mitad de la RAM (12 GB con 24 GB), `allkeys-lru`, sin RDB/AOF |
| Caché FITS en disco | `/var/cache/spherex/fits` | poda diaria a 60 GB (`/etc/cron.daily/spherex-prune-cache`) |
| Nginx | `sites-available/spherex` | micro-caché 12 h de `/api/`, `X-Fast-Cache` |
| Kernel | `/etc/sysctl.d/99-spherex.conf` | overcommit, swappiness |

**Lo que el instalador quita:** detiene y deshabilita `apache2`/`httpd`/`caddy`/`lighttpd`, para los contenedores Docker que publiquen 80/443, aparta los demás sitios de Nginx a `/etc/nginx/sites-disabled-<fecha>/` y reemplaza `/opt/spherex-backend` y `/var/www/spherex-frontend`. No toca nada más.

## API

| Endpoint | Función |
| --- | --- |
| `GET /api/v1/targets/search?q=` | Benchmark local → «RA Dec» → CDS Sesame |
| `GET /api/v1/observations?ra=&dec=` | Épocas de SPHEREx que cubren la posición (TAP de IRSA) |
| `GET /api/v1/cutout/frame?ra=&dec=&fov=&epoch=&band=&stretch=&invert=&diff=&ref=&vmin=&vmax=&res=` | Fotograma WebP reproyectado a TAN (N arriba, E izquierda). `diff=true` resta la época `ref`. `res=low` saca la versión rápida de baja resolución (rejilla nativa) para precarga |
| `GET /api/v1/cutout/coadd?ra=&dec=&fov=&band=&stretch=&method=` | Coadición (mediana o media) de todas las épocas |
| `GET /api/v1/cutout/prewarm?ra=&dec=&fov=&band=` | Encola en segundo plano la construcción de todas las épocas de esa banda (sin esperarla) |
| `GET /api/v1/spectrum?ra=&dec=` | Fotometría de apertura → 102 canales (µJy) + profundidad de hielo de agua y CO₂ |
| `GET /api/v1/cache/stats` | Redis, caché de disco y origen de los recortes (`source`: cuántos de S3, cuántos de IRSA y el último error de S3) |
| `GET /api/v1/source/test?ra=&dec=&n=` | Diagnóstico: lee `n` recortes directamente de AWS S3, sin caché, y devuelve el tiempo o el error de cada uno |
| `GET /api/docs` | Documentación interactiva |

`fov` va en segundos de arco (60–1800). `band` es `D1`…`D6` (detector completo) o `ch1`…`ch102` (canal fino). Los fotogramas devuelven cabeceras `X-Spherex-*` (límites de escala, nº de exposiciones, fecha, λ media, escala).

## Cómo procesa los datos

1. **Épocas.** SPHEREx no publica «una imagen por banda y época»: cada exposición ve el punto a una longitud de onda distinta. El servidor pide por TAP todas las exposiciones que contienen la posición y las agrupa en épocas por huecos de más de 20 días (y en ventanas de 60 días donde la cobertura es continua, cerca de los polos eclípticos).
2. **Fotograma.** Para la época y el detector pedidos lee los recortes (hasta 16) del bucket público de AWS, resta `ZODI`, enmascara con `FLAGS`, reproyecta cada uno con su WCS TAN-SIP a una rejilla TAN común de 3,075″/px y toma la mediana.
3. **Diferencia / coadición.** Resta de dos fotogramas ya alineados (dipolo = movimiento, monopolo = variabilidad) o mediana de todas las épocas.
4. **Espectro.** Apertura de 2 px con anillo de fondo en hasta 24 exposiciones por detector; la longitud de onda sale del WCS espectral (`WCS-WAVE`) de cada recorte.

## De dónde salen las imágenes: AWS S3

Las imágenes se leen del bucket público `nasa-irsa-spherex` (AWS, `us-east-1`, sin cuenta ni credenciales), no del servicio de recortes de IRSA. IRSA sigue usándose para la consulta TAP (qué exposiciones cubren la posición) y como respaldo.

- La ruta en S3 sale de la URL de IRSA: `…/ibe/data/spherex/dr1/level2/…fits` → `https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/dr1/level2/…fits`.
- No se baja la imagen entera (decenas de MB). Con peticiones HTTP `Range` se leen solo las cabeceras (una vez por imagen; quedan en `/var/cache/spherex/fits/layout`) y las filas del recorte de `IMAGE`, `ZODI` y `VARIANCE`. `FLAGS` viene comprimido y se trae entero (queda en `…/flags`); la tabla `WCS-WAVE` queda en `…/wave`.
- Las lecturas de una misma imagen van en paralelo, y `VARIANCE` solo se pide para el espectro.
- Si una imagen no está en el bucket o la lectura falla, ese recorte se pide a IRSA. Tras 12 fallos seguidos de S3 se deja de intentar durante 10 minutos.

Variables de entorno (en `/etc/systemd/system/spherex.service`):

| Variable | Valor por defecto | Función |
| --- | --- | --- |
| `SPHEREX_SOURCE` | `s3` | `s3` (S3 con respaldo de IRSA), `irsa` (solo IRSA) o `s3only` (sin respaldo, para diagnosticar) |
| `SPHEREX_S3_BASE` | `https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/` | Base del bucket |
| `SPHEREX_S3_THREADS` | `48` | Lecturas `Range` simultáneas |

## Pruebas

```bash
python3 tests/test_imaging.py
python3 tests/test_s3fits.py
```

`test_imaging.py` cubre la aritmética de imágenes (apilado, máscaras, dipolo, estirado, WebP, fotometría, canales, épocas). `test_s3fits.py` cubre la lectura parcial de FITS por rangos contra un fichero sintético con la estructura de un level2. La parte que habla con AWS e IRSA (TAP, recortes reales, astropy/reproject) solo se puede comprobar en el servidor: lo hacen las pruebas de humo del instalador.

## Limitaciones conocidas

- Sin dominio no hay HTTPS ni HTTP/2; con un dominio basta `certbot --nginx`.
- El espectro no corrige apertura ni sigue el movimiento propio entre épocas.
- La primera petición de una zona tarda (descarga y reproyección); después sale de Redis/Nginx.
- No incluye todavía capas de Gaia/WISE ni máscaras de calidad visibles.
