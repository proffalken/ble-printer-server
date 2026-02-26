FROM python:3.12-slim

# System deps:
#   liblzo2-dev  — for python-lzo (not used at runtime but needed to build the dep wheel)
#   libbluetooth-dev — for bleak/bluez headers
#   bluez        — bluetoothctl, hciconfig etc. (useful for diagnostics)
#   fonts-dejavu-core — monospace bold font for text rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
        liblzo2-dev \
        libbluetooth-dev \
        bluez \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy submodule first (changes less often)
COPY TiMini-Print/ ./TiMini-Print/

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application
COPY print_server.py .

ENV PRINT_HOST=0.0.0.0
ENV PRINT_PORT=8080

# Run as non-root. The bluetooth group (GID 112 on Debian) gives access to
# BlueZ D-Bus; the actual GID on your host may differ — check with
# `getent group bluetooth` and set BLUETOOTH_GID in docker-compose if needed.
ARG BLUETOOTH_GID=112
RUN groupadd -g ${BLUETOOTH_GID} bluetooth-host 2>/dev/null || true \
 && useradd -r -u 1000 -G ${BLUETOOTH_GID} printer

USER printer

EXPOSE 8080

CMD ["python", "-u", "print_server.py"]
