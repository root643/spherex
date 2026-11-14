#!/usr/bin/env bash
# Instalador del servidor traductor SPHEREx (Ubuntu/Debian, pensado para OCI Ampere A1).
# Se ejecuta como root en el servidor:  sudo bash install.sh
# Es idempotente: se puede volver a lanzar para actualizar.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-ubuntu}"
id "$RUN_USER" >/dev/null 2>&1 || RUN_USER="$(id -un 1000 2>/dev/null || echo root)"
[ "$RUN_USER" = "root" ] && id ubuntu >/dev/null 2>&1 && RUN_USER=ubuntu

say() { printf '\n\033[1;33m== %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo."; exit 1; }
command -v apt-get >/dev/null || { echo "Este instalador necesita apt (Ubuntu/Debian)."; exit 1; }

RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
CPUS=$(nproc)
WORKERS=$(( CPUS > 4 ? 4 : CPUS ))
REDIS_MB=$(( RAM_MB / 2 ))            # 12 GB en la maquina de 24 GB
[ "$REDIS_MB" -lt 256 ] && REDIS_MB=256
say "Maquina: $(uname -m), ${CPUS} CPU, ${RAM_MB} MB RAM -> ${WORKERS} workers, Redis ${REDIS_MB} MB, usuario ${RUN_USER}"

# ---------------------------------------------------------------- 1. liberar el puerto 80
say "Liberando los puertos 80/443 de lo que hubiera antes"
for svc in apache2 httpd caddy lighttpd; do
  if systemctl cat "$svc" >/dev/null 2>&1; then
    systemctl disable --now "$svc" 2>/dev/null || true
    echo "  detenido y deshabilitado: $svc"
  fi
done
if command -v docker >/dev/null 2>&1; then
  for c in $(docker ps --format '{{.ID}} {{.Ports}}' 2>/dev/null | awk '/:(80|443)->/ {print $1}'); do
    docker update --restart=no "$c" >/dev/null 2>&1 || true
    docker stop "$c" >/dev/null 2>&1 || true
    echo "  detenido contenedor docker $c (ocupaba 80/443)"
  done
fi
systemctl stop spherex.service 2>/dev/null || true

# ---------------------------------------------------------------- 2. paquetes
say "Instalando paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-pip python3-venv python3-dev build-essential \
    redis-server nginx git htop curl libcfitsio-dev libwebp-dev

say "Entorno Python en /opt/spherex-env"
[ -x /opt/spherex-env/bin/python ] || python3 -m venv /opt/spherex-env
/opt/spherex-env/bin/pip install --quiet --upgrade pip wheel
/opt/spherex-env/bin/pip install --quiet -r "$SRC/backend/requirements.txt"
/opt/spherex-env/bin/pip install --quiet blosc || echo "  (blosc no disponible: se usara zlib)"

# ---------------------------------------------------------------- 3. codigo
say "Copiando backend y frontend"
rm -rf /opt/spherex-backend /var/www/spherex-frontend
mkdir -p /opt/spherex-backend /var/www/spherex-frontend /var/cache/spherex/fits /var/cache/nginx/spherex
cp -r "$SRC/backend/." /opt/spherex-backend/
cp -r "$SRC/frontend/." /var/www/spherex-frontend/
chown -R "$RUN_USER":"$RUN_USER" /opt/spherex-backend /var/cache/spherex
chown -R www-data:www-data /var/cache/nginx/spherex
chmod -R a+rX /var/www/spherex-frontend

# ---------------------------------------------------------------- 4. Redis
say "Configurando Redis (maxmemory ${REDIS_MB}mb, allkeys-lru, sin volcados a disco)"
RC=/etc/redis/redis.conf
sed -i -E '/^[[:space:]]*(maxmemory|maxmemory-policy|save|appendonly)[[:space:]]/d; /^# --- spherex ---$/,/^# --- \/spherex ---$/d' "$RC"
cat >> "$RC" <<EOF
# --- spherex ---
maxmemory ${REDIS_MB}mb
maxmemory-policy allkeys-lru
save ""
appendonly no
# --- /spherex ---
EOF
systemctl enable redis-server >/dev/null 2>&1 || true
systemctl restart redis-server

# ---------------------------------------------------------------- 5. kernel
say "Ajustes del kernel"
cp "$SRC/conf/99-spherex.conf" /etc/sysctl.d/99-spherex.conf
sysctl --system >/dev/null

# ---------------------------------------------------------------- 6. systemd
say "Servicio systemd spherex.service"
cat > /etc/systemd/system/spherex.service <<EOF
[Unit]
Description=NASA SPHEREx WiseView Translation Server
After=network-online.target redis-server.service
Wants=network-online.target

[Service]
User=${RUN_USER}
WorkingDirectory=/opt/spherex-backend
Environment=SPHEREX_FITS_DIR=/var/cache/spherex/fits
Environment=SPHEREX_REDIS_URL=redis://127.0.0.1:6379/0
ExecStart=/opt/spherex-env/bin/uvicorn main:app --workers ${WORKERS} --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
install -m 755 "$SRC/conf/prune-cache.sh" /etc/cron.daily/spherex-prune-cache
systemctl daemon-reload
systemctl enable spherex.service >/dev/null 2>&1
systemctl restart spherex.service

# ---------------------------------------------------------------- 7. Nginx
say "Nginx"
BK="/etc/nginx/sites-disabled-$(date +%Y%m%d-%H%M%S)"
for f in /etc/nginx/sites-enabled/*; do
  [ -e "$f" ] || [ -L "$f" ] || continue
  [ "$(basename "$f")" = "spherex" ] && continue
  mkdir -p "$BK"; mv "$f" "$BK/"; echo "  sitio anterior apartado: $(basename "$f") -> $BK"
done
cp "$SRC/conf/nginx-spherex.conf" /etc/nginx/sites-available/spherex
ln -sf /etc/nginx/sites-available/spherex /etc/nginx/sites-enabled/spherex
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx

# ---------------------------------------------------------------- 8. cortafuegos local
say "Cortafuegos del sistema (80/443)"
if command -v iptables >/dev/null 2>&1; then
  for port in 80 443; do
    iptables -C INPUT -m state --state NEW -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || iptables -I INPUT 1 -m state --state NEW -p tcp --dport "$port" -j ACCEPT
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save >/dev/null 2>&1 || true
  elif [ -d /etc/iptables ]; then iptables-save > /etc/iptables/rules.v4; fi
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
fi

# ---------------------------------------------------------------- 9. pruebas de humo
say "Pruebas de humo"
FAIL=0
check() {  # check <descripcion> <url> [tiempo-max]
  local code
  code=$(curl -s -o /tmp/spherex-smoke.out -w '%{http_code} %{content_type} %{size_download}B %{time_total}s' --max-time "${3:-30}" "$2" || true)
  if [ "${code%% *}" = "200" ]; then echo "  OK    $1  [$code]"
  else echo "  FALLO $1  [$code]"; head -c 400 /tmp/spherex-smoke.out 2>/dev/null; echo; FAIL=1; fi
}
for i in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:8000/api/v1/health && break; sleep 1; done
B="http://127.0.0.1"
check "frontend"                 "$B/"
check "health"                   "$B/api/v1/health"
check "cache/stats"              "$B/api/v1/cache/stats"
check "search (benchmark)"       "$B/api/v1/targets/search?q=wise%200855"
check "search (CDS Sesame)"      "$B/api/v1/targets/search?q=Proxima%20Centauri"
check "observations (Barnard)"   "$B/api/v1/observations?ra=269.4521&dec=4.6934" 120
check "frame WebP (Barnard, D2)" "$B/api/v1/cutout/frame?ra=269.4521&dec=4.6934&fov=240&epoch=0&band=D2" 280
check "spectrum (Barnard)"       "$B/api/v1/spectrum?ra=269.4521&dec=4.6934" 290

if [ "$FAIL" -ne 0 ]; then
  say "Algun chequeo ha fallado. Ultimas lineas del servicio:"
  journalctl -u spherex.service -n 60 --no-pager || true
fi

IP=$(curl -s --max-time 5 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
say "Instalacion terminada -> http://${IP}/"
echo "Si no abre desde fuera: en Oracle Cloud, VCN > Security List > Ingress Rule TCP 80 desde 0.0.0.0/0."
exit "$FAIL"
