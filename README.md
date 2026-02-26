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
| `--model` | `PRINTER_MODEL` | auto | Override printer model (see TiMini-Print `--list-models`) |
| `--port` | `PRINT_PORT` | `8080` | HTTP listen port |
| `--host` | `PRINT_HOST` | `0.0.0.0` | HTTP bind address |

## Notes on BLE + Docker

BLE access from a container requires:
- `network_mode: host` — so the container can reach the host Bluetooth adapter
- `/var/run/dbus` volume — so bleak can talk to BlueZ via D-Bus
- `privileged: true` — for raw BLE socket access

The provided `docker-compose.yml` sets all of these.

## Credits

### [TiMini-Print](https://github.com/Dejniel/TiMini-Print) — Dejniel
The printer protocol implementation, image/text rendering pipeline, and device model
database are all provided by TiMini-Print, included here as a git submodule. If you
find this project useful, please consider [supporting TiMini-Print](https://buymeacoffee.com/dejniel).
Licensed under the [MIT License](https://github.com/Dejniel/TiMini-Print/blob/main/LICENSE).

### [bleak](https://github.com/hbldh/bleak) — Henrik Blidh
Cross-platform BLE client library used to communicate with the printer over Bluetooth Low Energy.
Licensed under the [MIT License](https://github.com/hbldh/bleak/blob/develop/LICENSE).

### [Pillow](https://python-pillow.org/)
Image composition and text rendering for the QR + text layout.
Licensed under the [HPND License](https://raw.githubusercontent.com/python-pillow/Pillow/main/LICENSE).

### [qrcode](https://github.com/lincolnloop/python-qrcode) — Lincoln Loop
QR code generation.
Licensed under the [BSD License](https://github.com/lincolnloop/python-qrcode/blob/master/LICENSE).

### [crc8](https://github.com/niccokunzmann/crc8) — Nicco Kunzmann
CRC8 checksum used in the printer protocol framing.
Licensed under the [MIT License](https://github.com/niccokunzmann/crc8/blob/master/LICENSE).

### [pyserial](https://github.com/pyserial/pyserial) — Chris Liechti
Serial port support for wired printer connections.
Licensed under the [BSD License](https://github.com/pyserial/pyserial/blob/master/LICENSE.txt).
