FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    libopengl0 \
    libglu1-mesa \
    libwebkit2gtk-4.1-0 \
    libjavascriptcoregtk-4.1-0 \
    libsm6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app /app/app

# Project license and third-party notices
COPY LICENSE /project/LICENSE
COPY THIRD_PARTY_NOTICES.md /project/THIRD_PARTY_NOTICES.md

RUN mkdir -p \
    /project/tmp/uploads \
    /project/tmp/analysis \
    /project/tmp/slicer

# OrcaSlicer 2.4.2 - official x86_64 AppImage
ADD --checksum=sha256:d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd \
    https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage \
    /tmp/OrcaSlicer.AppImage

RUN chmod +x /tmp/OrcaSlicer.AppImage \
    && cd /opt \
    && /tmp/OrcaSlicer.AppImage --appimage-extract \
    && mv /opt/squashfs-root /opt/orca \
    && rm /tmp/OrcaSlicer.AppImage

ENV PYTHONPATH=/app
ENV MYMACHINES_PROJECT_ROOT=/project

ENV ORCA_COMMAND=/opt/orca/AppRun
ENV ORCA_PRUSA_PRINTER_PROFILE=/project/profiles/printers/prusa_mk3s_0.4.json

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]