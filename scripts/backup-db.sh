#!/bin/sh
# Consistent SQLite backup; run inside the container, which owns sqlite3.
set -eu
DB="${YOURSS_DB_PATH:-/data/db/yourss.db}"
OUT="${YOURSS_BACKUP_DIR:-/data/backup}"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
sqlite3 "$DB" ".backup '$OUT/yourss-$STAMP.db'"
# Keep two weeks; a full 2Gi backup volume must not stop the app's writes.
find "$OUT" -name 'yourss-*.db' -type f -mtime +14 -delete
echo "$OUT/yourss-$STAMP.db"
