# Mapnificently with the data of every configured region baked in, ready for Cloud Run
# (the "Run on Google Cloud" button builds this file) or any container host.
#
#   make docker-build && make docker-run      # http://127.0.0.1:8080
#
# Stage 1 installs the app and downloads + prepares the timetables. Stage 2 keeps only the
# app and the prepared data (~35 MB), not the ~270 MB of downloaded feeds.

# Images are pinned by digest (updated by Dependabot), so a rebuild uses exactly these CA
# bundles: uv 0.13.5 and this Debian ca-certificates no longer trust the compromised
# eMudhra intermediates (October 2026).
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962 AS build

COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# Timetables: one region per process, so memory is released in between (Bergen, the
# largest, peaks at ~1.5 GB). The data is as fresh as the image: rebuild to refresh it.
ARG ENTUR_CLIENT_NAME=mapnificently-docker
ENV MAPNIFICENTLY_DATA_DIR=/data \
    MAPNIFICENTLY_ENTUR_CLIENT_NAME=${ENTUR_CLIENT_NAME}
RUN for region in $(/app/.venv/bin/mapnificently data regions); do \
        /app/.venv/bin/mapnificently data fetch "$region" || exit 1; \
    done \
    && rm -rf /data/feeds


FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962

RUN useradd --create-home --uid 10001 mapnificently
COPY --from=build /app/.venv /app/.venv
COPY --from=build --chown=mapnificently /data /data

# The server writes cached timetables next to the prepared data, hence the data's owner.
# Refreshing over HTTP is off: on a public server anyone could trigger it. Rebuild instead.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MAPNIFICENTLY_DATA_DIR=/data \
    MAPNIFICENTLY_ALLOW_REFRESH=false \
    MAPNIFICENTLY_ENTUR_CLIENT_NAME=mapnificently-docker \
    PORT=8080

USER mapnificently
EXPOSE 8080
# `serve` listens on $PORT, which Cloud Run sets.
CMD ["mapnificently", "serve", "--host", "0.0.0.0"]
