#!/usr/bin/env bash
# Restore a bundle made by export-bundle.sh into this checkout, replacing the current
# database and volumes. Run from the repository root (docker-compose.yml, config/,
# data/ and .env must be present):
#   scripts/import-bundle.sh <bundle-dir> [--yes]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f docker-compose.yml ] || { echo "Run from the ChargeGridAI repository" >&2; exit 1; }
IN="$(cd "${1:?usage: import-bundle.sh <bundle-dir> [--yes]}" && pwd)"
[ -f "$IN/chargegrid.dump" ] || { echo "$IN is not a ChargeGrid bundle" >&2; exit 1; }

if [ "${2:-}" != "--yes" ]; then
  echo "This REPLACES the current ChargeGrid database, MinIO and MLflow data."
  read -r -p "Continue? [y/N] " ok; [ "$ok" = "y" ] || exit 1
fi

echo "1/5 verifying checksums"
(cd "$IN" && grep 'sha256:' MANIFEST.txt | while read -r _ f _ sum; do
   [ "$(shasum -a 256 "$f" | cut -d' ' -f1)" = "${sum#sha256:}" ] || { echo "checksum mismatch: $f" >&2; exit 1; }
 done)

echo "2/5 loading images"
gunzip -c "$IN/images.tar.gz" | docker load | tail -n +1 | sed 's/^/  /'

echo "3/5 database"
docker compose down --remove-orphans >/dev/null 2>&1 || true
# The database is replaced wholesale: start from a fresh volume.
docker volume rm chargegridai_postgres-data >/dev/null 2>&1 || true
docker compose up -d --no-build postgis
# A fresh volume runs its init scripts first (and restarts the server): wait until
# that has finished, or dropping the database would kill the init session.
echo "  waiting for the database to finish initialising"
until docker compose logs postgis 2>/dev/null | grep -q "PostgreSQL init process complete"; do
  sleep 3
done
until docker compose exec -T postgis pg_isready -U chargegrid >/dev/null 2>&1; do sleep 2; done
docker compose exec -T postgis sh -c 'dropdb -U chargegrid --if-exists --force chargegrid && createdb -U chargegrid chargegrid'
docker compose exec -T postgis pg_restore -U chargegrid -d chargegrid --no-owner < "$IN/chargegrid.dump" || \
  echo "  (pg_restore reported warnings; checked below)"

echo "4/5 volumes"
for v in minio-data mlflow-data; do
  docker volume create "chargegridai_${v}" >/dev/null
  docker run --rm -v "chargegridai_${v}:/v" -v "$IN:/in:ro" --entrypoint sh chargegrid-backend:dev \
    -c "rm -rf /v/* /v/.[!.]* 2>/dev/null; tar xzf /in/${v}.tgz -C /v"
done

echo "5/5 starting everything"
docker compose up -d --no-build
docker compose exec -T postgis psql -U chargegrid -d chargegrid -Atc \
  "select 'tables restored: ' || count(*) from information_schema.tables where table_schema = 'public'"
echo "Done. Open http://localhost:5173 (the API is on :8000)."
