#!/usr/bin/env bash
# Export everything needed to run ChargeGrid again without rebuilding or re-seeding:
# the Docker images, a compressed database dump and the MinIO/MLflow volumes.
#   scripts/export-bundle.sh [output-dir]      (default: exports/chargegrid-bundle-<date>)
# Secrets (.env) are NOT included: copy .env separately and keep it private.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-exports/chargegrid-bundle-$(date +%Y%m%d-%H%M)}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
echo "Exporting to $OUT"

docker compose ps postgis --status running -q | grep -q . || {
  echo "The stack isn't running: start it with 'docker compose up -d' first" >&2; exit 1; }

TABLES=$(docker compose exec -T postgis psql -U chargegrid -d chargegrid -Atc \
  "select count(*) from information_schema.tables where table_schema = 'public'")
ROADS=$(docker compose exec -T postgis psql -U chargegrid -d chargegrid -Atc \
  "select count(*) from road_segment" 2>/dev/null || echo 0)
if [ "${TABLES:-0}" -lt 20 ] || [ "${ROADS:-0}" -eq 0 ]; then
  echo "The database looks empty ($TABLES tables, $ROADS road segments): refusing to export." >&2
  echo "Restore or seed it first (make import-bundle / make seed-pune)." >&2
  exit 1
fi

IMAGES=$(docker compose config --images | sort -u)
echo "1/4 images: $(echo $IMAGES | wc -w | tr -d ' ') (a few minutes)"
# shellcheck disable=SC2086
docker save $IMAGES | gzip -1 > "$OUT/images.tar.gz"

echo "2/4 database dump"
docker compose exec -T postgis pg_dump -U chargegrid -d chargegrid -Fc -Z 6 > "$OUT/chargegrid.dump"

echo "3/4 volumes (MinIO raw lake, MLflow runs)"
for v in minio-data mlflow-data; do
  docker run --rm -v "chargegridai_${v}:/v:ro" -v "$OUT:/out" --entrypoint tar \
    chargegrid-backend:dev czf "/out/${v}.tgz" -C /v .
done

echo "4/4 manifest"
{
  echo "created: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "alembic_head: $(docker compose exec -T postgis psql -U chargegrid -d chargegrid -Atc 'select version_num from alembic_version')"
  echo "images:"; for i in $IMAGES; do echo "  - $i"; done
  echo "files:"
  (cd "$OUT" && for f in images.tar.gz chargegrid.dump minio-data.tgz mlflow-data.tgz; do
     echo "  - $f $(du -h "$f" | cut -f1) sha256:$(shasum -a 256 "$f" | cut -d' ' -f1)"; done)
} > "$OUT/MANIFEST.txt"
cp scripts/import-bundle.sh "$OUT/"
cat "$OUT/MANIFEST.txt"
echo "Done: $(du -sh "$OUT" | cut -f1) in $OUT"
