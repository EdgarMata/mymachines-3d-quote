FROM python:3.13-slim
ARG TARGETARCH
LABEL org.opencontainers.image.title="My-Instant 3D Quote Backend" \
    org.opencontainers.image.description="MyMachines 3D printing quotation backend" \
    org.opencontainers.image.source="https://github.com/EdgarMata/mymachines-3d-quote" \
    org.opencontainers.image.licenses="AGPL-3.0-only" \
    org.opencontainers.image.vendor="MyMachines"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    libopengl0 \
    libglu1-mesa \
    libwebkit2gtk-4.1-0 \
    libjavascriptcoregtk-4.1-0 \
    libsm6 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app /app/app

# Application configuration and slicer profiles
COPY config /project/config
COPY profiles /project/profiles

# Project license and third-party notices
COPY LICENSE /project/LICENSE
COPY THIRD_PARTY_NOTICES.md /project/THIRD_PARTY_NOTICES.md

RUN mkdir -p \
    /project/tmp/uploads \
    /project/tmp/analysis \
    /project/tmp/slicer

# OrcaSlicer 2.4.2 - architecture-specific official AppImage
ARG ORCA_VERSION=2.4.2

RUN set -eux; \
    case "${TARGETARCH}" in \
    amd64) \
    ORCA_ASSET="OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION}.AppImage"; \
    ORCA_SHA256="d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd"; \
    ;; \
    arm64) \
    ORCA_ASSET="OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V${ORCA_VERSION}.AppImage"; \
    ORCA_SHA256="e1a07275a25f176626c55a5df39e91bc4476d8c28ee4a3192ff758e29dd5c3ba"; \
    ;; \
    *) \
    echo "Unsupported architecture: ${TARGETARCH}"; \
    exit 1; \
    ;; \
    esac; \
    curl -fL --retry 3 \
    "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v${ORCA_VERSION}/${ORCA_ASSET}" \
    -o /tmp/OrcaSlicer.AppImage; \
    echo "${ORCA_SHA256}  /tmp/OrcaSlicer.AppImage" | sha256sum -c -; \
    chmod +x /tmp/OrcaSlicer.AppImage; \
    cd /opt; \
    /tmp/OrcaSlicer.AppImage --appimage-extract; \
    mv /opt/squashfs-root /opt/orca; \
    rm /tmp/OrcaSlicer.AppImage

ENV PYTHONPATH=/app
ENV MYMACHINES_PROJECT_ROOT=/project

ENV ORCA_COMMAND=/opt/orca/AppRun
ENV ORCA_PRUSA_PRINTER_PROFILE=/project/profiles/printers/prusa_mk3s_0.4.json

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]