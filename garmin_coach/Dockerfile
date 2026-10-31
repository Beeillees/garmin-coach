# Feste Basis mit Python. Neuere Supervisor-Versionen setzen BUILD_FROM auf ein
# Alpine-Image ohne Python, deshalb wird das Image hier direkt angegeben.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY app/ /app/
COPY ha_integration/ /app/ha_integration/
WORKDIR /app

CMD ["python", "-u", "/app/main.py"]
