#!/bin/bash
# Poda del cache de la caja AWS (/var/cache/spherex-aws) por tamano total.
# Borra los ficheros MAS VIEJOS (cualquier tipo: los caches de flags/wave/layout
# no llevan extension) hasta dejar margen en el disco pequeno (6.8 GB).
CACHE=/var/cache/spherex-aws
LIMIT=2000; TARGET=800        # MB
USED=$(du -sm "$CACHE" 2>/dev/null | cut -f1)
[ -z "$USED" ] && exit 0
if [ "$USED" -gt "$LIMIT" ]; then
  NEED=$(( USED - TARGET ))
  find "$CACHE" -type f -printf "%T@ %s %p\n" | sort -n | \
  awk -v need="$NEED" '{ tot += $2; print $3; if (tot >= need*1048576) exit }' | \
  xargs -r -d "\n" rm -f
  # directorios vacios de hash: fuera
  find "$CACHE" -mindepth 1 -type d -empty -delete 2>/dev/null
fi
