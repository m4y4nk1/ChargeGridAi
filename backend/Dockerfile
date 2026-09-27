FROM python:3.12-slim

WORKDIR /app

# Geo Python deps (shapely, pyproj, pyogrio, rasterio) ship manylinux wheels
# with GEOS/PROJ/GDAL bundled, so no compiler or -dev packages are needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        osm2pgsql \
        osmium-tool \
        postgresql-client \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
