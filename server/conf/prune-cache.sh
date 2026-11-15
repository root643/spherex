#!/bin/sh
# Poda diaria de la cache FITS en disco: borra lo menos usado hasta bajar de 60 GB.
DIR=/var/cache/spherex/fits
LIMIT_KB=$((60 * 1024 * 1024))
[ -d "$DIR" ] || exit 0
USED_KB=$(du -sk "$DIR" | cut -f1)
[ "$USED_KB" -le "$LIMIT_KB" ] && exit 0
find "$DIR" -type f -printf '%T@ %k %p\n' | sort -n | while read -r _ kb path; do
  rm -f "$path"
  USED_KB=$((USED_KB - kb))
  [ "$USED_KB" -le "$LIMIT_KB" ] && break
done
