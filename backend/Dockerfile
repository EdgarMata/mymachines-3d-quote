FROM python:3.13-slim

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app /app/app

COPY config /project/config
COPY profiles /project/profiles

RUN mkdir -p \
    /project/tmp/uploads \
    /project/tmp/analysis \
    /project/tmp/slicer

ENV PYTHONPATH=/app
ENV MYMACHINES_PROJECT_ROOT=/project

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]