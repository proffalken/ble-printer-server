# BLE Print Server

A lightweight HTTP server that prints to a TiMini-compatible Bluetooth thermal printer over BLE. It supports two modes:

**QR + text** — include a `qr` field. The QR code fills the left half of the paper, the text the right. `text` falls back to the `qr` value if not provided.

**Text only** — omit `qr` entirely. Text fills the full paper width. Useful for receipts, orders, or any free-form output. Newlines (`\n`) and tabs (`\t`) are honoured, and `text` may be a nested JSON object which is formatted into readable plain text.

```
# QR + text
GET  /print?text=Box+1&qr=http://inventory.example.com/box/1
GET  /print?qr=https://example.com

# Text only
GET  /print?text=Hello+World
POST /print   {"text": "Order #1\nSmashburger\n\nToppings:\n\tCheese\n\tBacon"}
POST /print   {"text": {"order": "#1", "item": "Smashburger", "toppings": ["Cheese", "Bacon"]}}

# QR + text via POST
POST /print   {"text": "Box 1", "qr": "http://inventory.example.com/box/1"}
```

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
# Label: QR code + text
curl "http://localhost:8080/print?text=Box+1&qr=http://inventory.example.com/box/1"

# Receipt: text only, full width
curl -X POST http://localhost:8080/print \
     -H "Content-Type: application/json" \
     -d '{"text": "Order #1\nSmashburger\n\nToppings:\n\tCheese\n\tBacon"}'

# Nested object printed as plain text
curl -X POST http://localhost:8080/print \
     -H "Content-Type: application/json" \
     -d '{"text": {"order": "#1", "item": "Smashburger", "toppings": ["Cheese", "Bacon"]}}'
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

## AI Statment

For those of you wondering or looking at the commits - yes, this is almost entirely written by Claude Code.

I've spent years developing and contributing software, this was a deliberate test to see what Claude was capable of and I'm pretty impressed.

I fed it the concept, nudged it via prompts, and asked it to use certain libraries, then asked it to do a security test.

**At all points in the process it came up with code and approaches that were what I'd expect from a junior developer, but instead of rewriting
mistakes myself I gave it a new prompt and told it to change direction, which it did**

If you don't want AI code on your home network at all, don't use this project.  If you're happy with AI Generated code that has been reviewed by a
human and tested thoroughly then welcome to the project and I'd love to hear your thoughts/PR's on future improvements!


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
