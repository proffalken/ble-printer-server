# BLE Print Server

A lightweight HTTP server that accepts text and prints it — along with a QR code of that text — to a TiMini-compatible Bluetooth thermal printer over BLE.

```
POST /print   body = text to print
GET  /print   ?text=text to print
```

The output is a QR code on the left half of the paper with the text vertically centred on the right.

## Supported printers

Any printer supported by [TiMini-Print](https://github.com/Dejniel/TiMini-Print) that uses BLE (not SPP/RFCOMM). Tested with the **X6**.

## Quick start (Docker)

```bash
git clone --recurse-submodules <this repo>
cd ble-print-server

# Edit docker-compose.yml and set PRINTER_BLUETOOTH to your printer's name or MAC
docker compose up -d
```

Then print something:

```bash
curl -X POST http://localhost:8080/print -d "https://example.com"
curl "http://localhost:8080/print?text=Hello+World"
```

## Quick start (local)

Requires `liblzo2-dev` system package (`sudo apt install liblzo2-dev`).

```bash
git clone --recurse-submodules <this repo>
cd ble-print-server
pip install -e .
python print_server.py --bluetooth X6
```

## Configuration

| CLI flag | Environment variable | Default | Description |
|---|---|---|---|
| `--bluetooth` | `PRINTER_BLUETOOTH` | — | Printer BLE name prefix or MAC address |
| `--serial` | `PRINTER_SERIAL` | — | Serial port (e.g. `/dev/rfcomm0`) |
| `--model` | `PRINTER_MODEL` | auto | Override printer model (see `--list-models`) |
| `--port` | `PRINT_PORT` | `8080` | HTTP listen port |
| `--host` | `PRINT_HOST` | `0.0.0.0` | HTTP bind address |

## Notes on BLE + Docker

BLE access from a container requires:
- `network_mode: host` — so the container can reach the host Bluetooth adapter
- `/var/run/dbus` volume — so bleak can talk to BlueZ via D-Bus
- `privileged: true` — for raw BLE socket access

The provided `docker-compose.yml` sets all of these.
