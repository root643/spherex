# 🛰️ Guía de Arquitectura e Implementación: Servidor Traductor SPHEREx en Oracle Cloud (24 GB RAM)
**Desafío:** *NASA Space Apps Challenge 2026 – "Planet X and SPHEREx"*  
**Paradigma de Referencia:** *WiseView* (`byw.tools/wiseview` / Dan Caselden)  
**Infraestructura Host:** *Oracle Cloud Infrastructure (OCI) Always Free Tier (4 OCPU ARM64 Ampere A1, 24 GB RAM, 200 GB NVMe, 10 TB/mes transferencia gratuita)*  

---

## 1. Visión y Propósito del Servidor Traductor

En astronomía moderna, descargar imágenes FITS completas de $65\text{ MB}$ por cada observación satura el navegador web y agota el plan de datos del usuario. 

La solución —inspirada en cómo **WiseView** transformó la búsqueda de objetos transneptunianos y enanas marrones para *Backyard Worlds: Planet 9*— consiste en colocar un **servidor intermediario de alta velocidad** en tu instancia gratuita de Oracle Cloud (24 GB RAM) que actúe como **traductor, recortador (cutouts), alineador astrométrico y acelerador de caché en memoria**.

```
┌────────────────────────┐         HTTP/2 WebP / JSON (<80 ms)         ┌────────────────────────────────────────────────────────┐
│                        │ ◄──────────────────────────────────────────► │               SERVIDOR ORACLE CLOUD (24 GB)            │
│     NAVEGADOR WEB      │                                              │  1. Nginx Reverse Proxy (SSL, HTTP/2, Brotli)          │
│   (Usuario / Cliente)  │                                              │  2. FastAPI Backend (4 Workers en 4 OCPU ARM64)        │
│                        │                                              │  3. Redis L1 Cache (12 GB RAM)                         │
│  - WiseView Blink View │                                              │  4. Reproyección WCS, Diferencia y Coadición           │
│  - Inspector 102 Bandas│                                              │  5. NVMe Cache & SQLite Spatial Index (60 GB)          │
└────────────────────────┘                                              └───────────────────────────▲────────────────────────────┘
                                                                                                    │ HTTP Range / IBE REST
                                                                                                    ▼
                                                                        ┌────────────────────────────────────────────────────────┐
                                                                        │             FUENTES DE DATOS UPSTREAM (NASA)           │
                                                                        │  A. NASA IRSA IBE API (Recortes FITS bajo demanda)     │
                                                                        │  B. AWS S3 Open Data (s3://nasa-irsa-spherex)          │
                                                                        │  C. NASA IRSA TAP Service (Metadatos de observaciones) │
                                                                        └────────────────────────────────────────────────────────┘
```

---

## 2. Reparto de Recursos en tu Servidor Oracle (24 GB RAM)

La instancia gratuita de Oracle Cloud (Ampere A1 aarch64) ofrece **4 núcleos virtuales OCPU y 24 GB de memoria física**. Para que el servidor nunca sufra un bloqueo por falta de memoria (*OOM crash*), la distribución ideal es:

| Recurso | Asignación | Propósito y Justificación |
| :--- | :--- | :--- |
| **Redis L1 In-Memory Cache** | **12 GB RAM** | Almacena matrices astronómicas float32 y teselas WebP terminadas. Permite servir más de 1.200.000 recortes en memoria activa en $<5\,\text{ms}$. |
| **FastAPI / Uvicorn Workers** | **8 GB RAM** | 4 procesos paralelos (2 GB por núcleo OCPU). Cada reproyección astrométrica (`reproject_interp`) y cálculo de matrices consume temporalmente $150\text{--}300\,\text{MB}$. |
| **Nginx + Buffers de Red** | **1.5 GB RAM** | Manejo de conexiones HTTP/2 concurrentes y compresión al vuelo. |
| **Linux Kernel & Page Cache** | **2.5 GB RAM** | Búferes del sistema operativo y margen de seguridad. |

> [!CAUTION]
> **Ajuste Crítico de Redis**: En `/etc/redis/redis.conf`, debes configurar `maxmemory 12gb`, `maxmemory-policy allkeys-lru` y **desactivar los volcados a disco** (`save ""`). Si dejas activado el autoguardado RDB en disco, Redis ejecutará `fork()`, duplicando virtualmente el espacio de memoria y provocando que el kernel de Linux cierre el proceso por OOM.

---

## 3. Pipeline de Datos y Técnicas de Procesamiento Astronómico

### A. Reproyección WCS Obligatoria
SPHEREx rota su ángulo de inclinación (*roll angle*) entre los escaneos semestrales para mantener su escudo térmico apuntando lejos del Sol, y utiliza distorsiones ópticas no lineales polinomiales (`CTYPE='RA---TAN-SIP'`, SIP de orden 3). Un simple recorte matricial plano no sirve para comparar épocas. El servidor proyecta cada época a una cuadrícula tangencial común (`TAN` estándar, Norte arriba, Este a la izquierda) con resolución de $6.2''/\text{px}$ antes de parpadear o restar imágenes.

### B. Sustracción Temporal (Detección de Dipolos)
$$\Delta I(x, y) = I_{\text{Época } B}(x, y) - I_{\text{Época } A}(x, y)$$
- **Objetos en Movimiento (Planeta X, Enanas Marrones, Asteroides):** Al restar dos épocas, el objeto genera un **pozo oscuro negativo** en su posición anterior y un **pico blanco positivo** en su nueva posición. Este **dipolo blanco/negro** es la firma visual inequívoca de desplazamiento propio.
- **Estrellas Variables (RW Aurigae, R Draconis):** Como la posición no cambia, la resta produce una **mancha monopolar** (positiva si aumentó de brillo, negativa si se atenuó), sin efecto dipolar.

### C. Coadición Temporal (Stacking por Mediana)
$$\text{Coadd}(x, y) = \text{median}\left(I_1(x, y), I_2(x, y), \dots, I_N(x, y)\right)$$
Al tomar la mediana de todas las tomas semestrales disponibles, los rayos cósmicos y asteroides rápidos desaparecen, y la relación señal/ruido ($\text{SNR}$) del fondo cósmico se multiplica por $\sqrt{N}$, revelando objetos ultrafríos que no alcanzan el umbral de detección en una sola toma.

### D. Extracción Espectral de 102 Bandas al Vuelo
SPHEREx divide su cobertura espectral en 6 detectores criogénicos con Filtros Variables Lineales (LVF):
- **Detectores 1 a 4 ($0.75\text{--}3.8\,\mu\text{m}$):** Resolución baja ($R \approx 40$) para masa estelar y continuo.
- **Detectores 5 y 6 ($3.8\text{--}5.0\,\mu\text{m}$):** Alta resolución ($R \approx 110\text{--}135$) para detectar hielos de agua ($3.05\,\mu\text{m}$) y $\text{CO}_2$ ($4.27\,\mu\text{m}$).
Al hacer clic sobre cualquier coordenada, el servidor calcula la fotometría de apertura en los 102 canales y devuelve la curva de energía espectral (SED) junto con los índices diagnósticos de absorción.

---

## 4. Endpoints del Backend FastAPI (`/opt/spherex-backend/main.py`)

| Método | Endpoint | Parámetros Clave | Salida y Función |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/targets/search` | `q=wise 0855` | Resuelve nombres en $<1\,\text{ms}$ (SIMBAD / CDS Sesame / Benchmark local). Devuelve RA, Dec, movimiento propio y tipo. |
| `GET` | `/api/v1/observations` | `ra=133.79&dec=-7.24` | Lista las épocas semestrales de SPHEREx (fechas, MJD, detectores disponibles). |
| `GET` | `/api/v1/cutout/frame` | `ra`, `dec`, `fov`, `epoch`, `band`, `stretch`, `invert`, `diff` | **Endpoint central de WiseView**: Genera la imagen recortada, WCS-reproyectada y estirada en WebP ($<25\,\text{KB}$). Soporta modo diferencia (`diff=true`). |
| `GET` | `/api/v1/cutout/coadd` | `ra`, `dec`, `fov`, `band`, `stretch`, `method` | Devuelve la coadición temporal por mediana de todas las épocas para cazar objetos ultra-débiles. |
| `GET` | `/api/v1/spectrum` | `ra`, `dec` | Devuelve la curva de 102 canales fotométricos ($\mu\text{Jy}$) y las profundidades diagnósticas de hielo de agua y $\text{CO}_2$. |
| `GET` | `/api/v1/cache/stats` | *(Ninguno)* | Métricas de uso de RAM de Redis, tasa de aciertos y operaciones por segundo. |

---

## 5. Validación con los 5 Objetivos Astronómicos Obligatorios

| Objetivo de Prueba | Coordenadas ($\text{RA}, \text{Dec}$) | Movimiento Propio / Variabilidad | Comportamiento en tu Servidor WiseView SPHEREx |
| :--- | :--- | :--- | :--- |
| **Estrella de Barnard** | $269.452^\circ$, $+04.693^\circ$ | $\mu = 10.39''/\text{año}$ ($1.68\,\text{px/año}$) | En modo *Blink* se desplaza claramente hacia el norte. En modo *Diferencia* genera un **dipolo blanco y negro perfecto**. |
| **WISE 0855-0714** | $133.795^\circ$, $-07.245^\circ$ | $\mu = 8.1''/\text{año}$ ($1.31\,\text{px/año}$), Enana marrón ultragélida ($250\,\text{K}$) | **Invisible en detectores D1 a D3 ($<2.4\,\mu\text{m}$)**. Aparece con gran brillo en D4, D5 y D6. En el espectro de 102 canales muestra absorción profunda de hielo de agua ($3.05\,\mu\text{m}$) y metano ($3.3\,\mu\text{m}$). |
| **Epsilon Indi B** | $331.044^\circ$, $-56.783^\circ$ | $\mu = 4.70''/\text{año}$ ($0.76\,\text{px/año}$), Par binario de enanas marrones | Desplazamiento visible en el hemisferio sur celeste; dipolo inclinado en modo sustracción. |
| **RW Aurigae** | $076.956^\circ$, $+30.401^\circ$ | Movimiento propio nulo; variación de brillo de hasta 3 magnitudes | **Posición fija**. En modo diferencia produce una **mancha monopolar** (sin dipolo), confirmando variación fotométrica por discos circunestelares. |
| **R Draconis** | $248.168^\circ$, $+66.755^\circ$ | Gigante pulsante tipo Mira ($P \approx 245.6$ días) | El ciclo de 245 días entra en desfase con el muestreo semestral de 182 días: épocas alternas capturan la estrella cerca del máximo y cerca del mínimo. |

---

## 6. Guía de Instalación Rápida en tu Servidor Oracle (Paso a Paso)

### Paso 1: Abrir Puertos en Oracle Cloud
1. En la consola web de Oracle Cloud:
   - **Networking** $\to$ **Virtual Cloud Networks** $\to$ Selecciona tu VCN $\to$ **Security Lists** $\to$ `Default Security List`.
   - Haz clic en **Add Ingress Rules**:
     - *Source CIDR:* `0.0.0.0/0`
     - *IP Protocol:* `TCP`
     - *Destination Port Range:* `80, 443`
2. En la consola SSH de tu máquina Ubuntu en Oracle:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables-save | sudo tee /etc/iptables/rules.v4
```

### Paso 2: Instalar Paquetes y Entorno Python ARM64
```bash
sudo apt-get update && sudo apt-get install -y \
    python3-pip python3-venv python3-dev \
    redis-server nginx git htop curl libcfitsio-dev libwebp-dev

# Crear entorno virtual de alto rendimiento
python3 -m venv /opt/spherex-env
source /opt/spherex-env/bin/activate
pip install --upgrade pip wheel
pip install fastapi "uvicorn[standard]" redis blosc numpy scipy astropy reproject pillow
```

### Paso 3: Configurar Redis (12 GB RAM)
Ejecuta estos comandos para ajustar Redis a la memoria disponible:
```bash
sudo sed -i 's/^maxmemory .*/maxmemory 12gb/' /etc/redis/redis.conf
sudo sed -i 's/^maxmemory-policy .*/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf
sudo sed -i 's/^save .*/save ""/' /etc/redis/redis.conf
sudo systemctl restart redis-server
sudo systemctl enable redis-server
```

### Paso 4: Optimizar el Kernel de Linux
Crea el archivo `/etc/sysctl.d/99-spherex.conf`:
```ini
vm.overcommit_memory = 1
vm.swappiness = 1
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
```
Aplica los cambios:
```bash
sudo sysctl --system
```

### Paso 5: Crear el Servicio Systemd para FastAPI
Crea el archivo `/etc/systemd/system/spherex.service`:
```ini
[Unit]
Description=NASA SPHEREx WiseView Translation Server
After=network.target redis-server.service

[Service]
User=ubuntu
WorkingDirectory=/opt/spherex-backend
ExecStart=/opt/spherex-env/bin/uvicorn main:app --workers 4 --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```
Inicia el servicio:
```bash
sudo systemctl daemon-reload
sudo systemctl enable spherex.service
sudo systemctl start spherex.service
```

### Paso 6: Configurar Nginx con Micro-caching y HTTP/2
Edita `/etc/nginx/sites-available/spherex`:
```nginx
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=blink_cache:50m max_size=2g inactive=12h use_temp_path=off;

server {
    listen 80;
    listen [::]:80;
    server_name _;

    gzip on;
    gzip_types application/json image/webp text/css application/javascript;

    # Interfaz web estática
    location / {
        root /var/www/spherex-frontend;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # API de recortes y traducción con micro-caching
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        proxy_cache blink_cache;
        proxy_cache_valid 200 12h;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        add_header X-Fast-Cache $upstream_cache_status;
    }
}
```
Activa el sitio y reinicia Nginx:
```bash
sudo ln -sf /etc/nginx/sites-available/spherex /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx
```

---

## 7. Conclusión: Ventaja Competitiva para el Space Apps Challenge

Al utilizar este diseño:
1. **Tu servidor Oracle de 24 GB RAM asume todo el cómputo pesado** (WCS, FITS y reproyecciones), permitiendo que la web del usuario funcione fluida a 60 FPS en cualquier móvil o PC modesto.
2. **Implementas la metodología de descubrimiento de WiseView**: animación en bucle (*blink*), resta para caza de dipolos (*dipole hunting*) y coadición para objetos sub-ruido.
3. **Superas a WiseView gracias a SPHEREx**: en lugar de 4 bandas monocromáticas, ofreces **102 canales infrarrojos continuos** y diagnóstico de firmas químicas de hielo de agua y $\text{CO}_2$ en tiempo real.
