# PostGIS 3.4+ with h3-pg and pgvector (Section 4: Data & geospatial).
# Multi-stage: extensions are compiled in a throwaway builder so the
# toolchain (~1GB of gcc/clang/llvm) never ships in the runtime image.
# pg_trgm ships with contrib and is enabled via CREATE EXTENSION at init.
FROM postgis/postgis:16-3.4 AS builder

# The upstream image is pinned to Debian 11 (bullseye), whose live apt
# mirrors were retired after EOL; use the frozen snapshot archive.
RUN printf 'deb http://snapshot.debian.org/archive/debian/20240926T000000Z bullseye main\ndeb http://snapshot.debian.org/archive/debian-security/20240926T000000Z bullseye-security main\ndeb http://snapshot.debian.org/archive/debian/20240926T000000Z bullseye-updates main\n' > /etc/apt/sources.list

RUN apt-get -o Acquire::Check-Valid-Until=false update && apt-get install -y --no-install-recommends \
        build-essential git cmake postgresql-server-dev-16 libpq-dev clang-13 llvm-13-dev \
    && rm -rf /var/lib/apt/lists/*

# Pinned so a rebuild can't silently pick up an incompatible upstream change.
ARG H3_VERSION=v4.2.1
ARG H3_PG_VERSION=v4.2.3
ARG PGVECTOR_VERSION=v0.8.6

RUN git clone --depth 1 --branch ${H3_VERSION} https://github.com/uber/h3.git /tmp/h3 \
    && cmake -B /tmp/h3/build -S /tmp/h3 -DBUILD_TESTING=OFF \
    && cmake --build /tmp/h3/build --parallel \
    && cmake --install /tmp/h3/build \
    && git clone --depth 1 --branch ${H3_PG_VERSION} https://github.com/postgis/h3-pg.git /tmp/h3-pg \
    && make -C /tmp/h3-pg && make -C /tmp/h3-pg install \
    && git clone --depth 1 --branch ${PGVECTOR_VERSION} https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && make -C /tmp/pgvector && make -C /tmp/pgvector install

FROM postgis/postgis:16-3.4

COPY --from=builder /usr/lib/postgresql/16/lib/ /usr/lib/postgresql/16/lib/
COPY --from=builder /usr/share/postgresql/16/extension/ /usr/share/postgresql/16/extension/
COPY --from=builder /usr/local/lib/ /usr/local/lib/
RUN ldconfig
COPY init-extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql
