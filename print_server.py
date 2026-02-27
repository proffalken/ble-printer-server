#!/usr/bin/env python3
"""BLE Print Server — prints text and/or a QR code to a TiMini-compatible thermal printer over BLE.

Usage:
  python print_server.py [--bluetooth NAME_OR_ADDR] [--port 8080]
  python print_server.py --serial /dev/rfcomm0 --model X6

Environment variables (used as defaults):
  PRINTER_BLUETOOTH   Bluetooth name prefix or address
  PRINTER_SERIAL      Serial port path
  PRINTER_MODEL       Model override
  PRINT_PORT          HTTP port (default: 8080)
  PRINT_HOST          Bind address (default: 0.0.0.0)

Modes
-----
QR + text  — include a "qr" field; the QR code fills the left half, text the right.
             "text" falls back to the "qr" value if omitted.
Text only  — omit "qr" entirely; text fills the full paper width.
             "text" may be a plain string (\\n and \\t are honoured) or a nested
             JSON object/array, which is formatted into readable plain text.

Examples:
  # Label: QR code + text side by side
  curl "http://localhost:8080/print?text=Box+1&qr=http://inventory.example.com/box/1"

  # Receipt / order: text only, full width
  curl -X POST http://localhost:8080/print -H "Content-Type: application/json" \\
       -d '{"text": "Order #1\\nSmashburger\\n\\nToppings:\\n\\tCheese\\n\\tBacon"}'

  # Nested object flattened to plain text
  curl -X POST http://localhost:8080/print -H "Content-Type: application/json" \\
       -d '{"text": {"order": "#1", "item": "Smashburger", "toppings": ["Cheese", "Bacon"]}}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent / "TiMini-Print"))

from timiniprint.cli import write_serial_blocking
from timiniprint.converters import (
    columns_for_width,
    fit_truetype_font,
    font_line_height,
    wrap_text_lines,
)
from timiniprint.device_utils import require_model, resolve_model
from timiniprint.font_utils import find_monospace_bold_font
from timiniprint.models import PrinterModel, PrinterModelRegistry
from timiniprint.print_job import PrintJobBuilder, PrintSettings

BLE_WRITE_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
MAX_BODY_BYTES = 10_240  # 10 KB — enough for any reasonable label text
BLE_PRINT_TIMEOUT = 60.0  # seconds before a stuck BLE job is abandoned


def _format_value(value: object, indent: int = 0) -> str:
    """Recursively format a JSON value (dict, list, or scalar) as plain text."""
    pad = "  " * indent
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_format_value(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(_format_value(item, indent))
            else:
                lines.append(f"{pad}{item}")
        return "\n".join(lines)
    return str(value)


def _text_lines(text: str, columns: int) -> list[str]:
    """Split on newlines, expand tabs, then word-wrap each line."""
    result: list[str] = []
    for raw in text.split("\n"):
        expanded = raw.expandtabs(4)
        if not expanded.strip():
            result.append("")
        else:
            result.extend(wrap_text_lines(expanded, columns))
    return result


def compose_image(display_text: str, qr_data: str | None, printer_width: int) -> Image.Image:
    """Compose the print image.

    When qr_data is provided: QR code on the left half, text on the right.
    When qr_data is None: text only, spanning the full paper width.
    """
    if qr_data is not None:
        try:
            import qrcode
        except ImportError:
            raise RuntimeError("qrcode is required: pip install 'qrcode[pil]'")
        qr_size = printer_width // 2
        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
        qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
        text_area_width = printer_width - qr_size
    else:
        qr_size = 0
        text_area_width = printer_width

    font_path = find_monospace_bold_font()
    columns = columns_for_width(text_area_width)
    font = fit_truetype_font(font_path, text_area_width, columns)
    lines = _text_lines(display_text, columns)
    lh = font_line_height(font)
    text_block_height = max(1, lh * len(lines))

    text_img = Image.new("L", (text_area_width, text_block_height), 255)
    draw = ImageDraw.Draw(text_img)
    y = 0
    for line in lines:
        draw.text((0, y), line, font=font, fill=0)
        y += lh

    if qr_data is not None:
        total_height = max(qr_size, text_block_height)
        out = Image.new("L", (printer_width, total_height), 255)
        out.paste(qr_img, (0, (total_height - qr_size) // 2))
        out.paste(text_img, (qr_size, (total_height - text_block_height) // 2))
        return out

    return text_img


def build_print_data(display_text: str, qr_data: str | None, model: PrinterModel) -> bytes:
    printer_width = PrintJobBuilder._normalized_width(model.width)
    img = compose_image(display_text, qr_data, printer_width)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        img.save(tmp_path)
        return PrintJobBuilder(model, PrintSettings()).build_from_file(tmp_path)
    finally:
        os.unlink(tmp_path)


class PrintServer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._registry = PrinterModelRegistry.load()
        self._lock = threading.Lock()

    def print_text(self, display_text: str, qr_data: str | None) -> None:
        with self._lock:
            if self.args.serial:
                self._print_serial(display_text, qr_data)
            else:
                asyncio.run(asyncio.wait_for(self._print_ble(display_text, qr_data), timeout=BLE_PRINT_TIMEOUT))

    def _print_serial(self, display_text: str, qr_data: str | None) -> None:
        model = require_model(self._registry, self.args.model)
        data = build_print_data(display_text, qr_data, model)
        write_serial_blocking(
            self.args.serial, data, model.img_mtu or 180, model.interval_ms or 4
        )

    async def _print_ble(self, display_text: str, qr_data: str | None) -> None:
        from bleak import BleakClient, BleakScanner

        target = self.args.bluetooth
        if ":" not in target:
            devices = await BleakScanner.discover(timeout=5.0)
            match = next(
                (d for d in devices if (d.name or "").lower().startswith(target.lower())),
                None,
            )
            if not match:
                raise RuntimeError(f"No BLE device found matching '{target}'")
            address, name = match.address, match.name or target
        else:
            devices = await BleakScanner.discover(timeout=5.0)
            match = next((d for d in devices if d.address.lower() == target.lower()), None)
            address, name = target, (match.name if match else target)

        model = resolve_model(self._registry, name, self.args.model)
        data = build_print_data(display_text, qr_data, model)
        mtu = model.img_mtu or 20
        interval = (model.interval_ms or 4) / 1000

        async with BleakClient(address) as client:
            for i in range(0, len(data), mtu):
                await client.write_gatt_char(BLE_WRITE_UUID, data[i : i + mtu], response=False)
                await asyncio.sleep(interval)


class _PrintHandler(BaseHTTPRequestHandler):
    _server: PrintServer

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.address_string()}] {fmt % args}", flush=True)

    def _extract_params(self) -> tuple[str, str | None] | None:
        """Return (display_text, qr_data), or None if the request cannot be handled.

        qr_data=None means text-only mode (no QR code, full-width text).
        The presence of the "qr" key — not its value — determines the mode.
        "text" may be a plain string, or a nested JSON object/array which is
        formatted into readable plain text via _format_value.

        Sends an error response before returning None when the path matched /print
        but the payload was malformed; the caller must not send a second response.
        """
        qs = parse_qs(urlparse(self.path).query)
        text_param = qs["text"][0] if "text" in qs else None
        qr_param = qs["qr"][0] if "qr" in qs else None

        if text_param is not None or qr_param is not None:
            if qr_param is not None:
                # QR mode: text falls back to the qr value if not provided
                return (text_param if text_param is not None else qr_param), qr_param
            else:
                # Text-only mode
                return text_param, None

        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._respond(400, "Invalid Content-Length.\n")
                return None
            if length < 0:
                self._respond(400, "Invalid Content-Length.\n")
                return None
            if length > MAX_BODY_BYTES:
                self._respond(413, f"Request body too large (max {MAX_BODY_BYTES} bytes).\n")
                return None
            if length:
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    self._respond(400, "Invalid JSON body.\n")
                    return None
                if not isinstance(body, dict):
                    self._respond(400, "JSON body must be an object.\n")
                    return None
                text_val = body.get("text")
                qr_val = body.get("qr")
                if text_val is None and qr_val is None:
                    # Bare object — treat the whole body as the text content
                    return _format_value(body), None
                if qr_val is not None and not isinstance(qr_val, str):
                    self._respond(400, '"qr" must be a string.\n')
                    return None
                display_text = _format_value(text_val if text_val is not None else qr_val)
                return display_text, qr_val  # qr_val is str or None

        return None

    def _respond(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle(self) -> None:
        if urlparse(self.path).path != "/print":
            self._respond(404, "Not found.\n\nUsage: GET /print?text=...&qr=... or POST /print with JSON body.\n")
            return
        params = self._extract_params()
        if params is None:
            return  # error response already sent by _extract_params
        display_text, qr_data = params
        if not display_text.strip():
            self._respond(400, "Empty text.\n")
            return
        try:
            self._server.print_text(display_text, qr_data)
            self._respond(200, "OK\n")
        except Exception as exc:
            print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
            self._respond(500, "Print error — check server logs.\n")

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()


def _make_handler(server: PrintServer) -> type:
    class Handler(_PrintHandler):
        _server = server

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BLE print server — prints text + QR code to a TiMini-compatible thermal printer."
    )
    parser.add_argument(
        "--bluetooth",
        metavar="NAME_OR_ADDR",
        default=os.environ.get("PRINTER_BLUETOOTH"),
        help="BLE printer name prefix or address (env: PRINTER_BLUETOOTH)",
    )
    parser.add_argument(
        "--serial",
        metavar="PATH",
        default=os.environ.get("PRINTER_SERIAL"),
        help="Serial port, e.g. /dev/rfcomm0 (env: PRINTER_SERIAL)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PRINTER_MODEL"),
        help="Printer model override (env: PRINTER_MODEL)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PRINT_PORT", 8080)),
        help="HTTP port (env: PRINT_PORT, default: 8080)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("PRINT_HOST", "0.0.0.0"),
        help="Bind address (env: PRINT_HOST, default: 0.0.0.0)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.serial and not args.bluetooth:
        print(
            "Error: specify --bluetooth / PRINTER_BLUETOOTH or --serial / PRINTER_SERIAL.",
            file=sys.stderr,
        )
        return 2

    server = PrintServer(args)
    httpd = HTTPServer((args.host, args.port), _make_handler(server))
    print(f"Print server listening on {args.host}:{args.port}", flush=True)
    print(f"  GET  http://{args.host}:{args.port}/print?text=...&qr=...")
    print(f"  POST http://{args.host}:{args.port}/print  (JSON: {{\"text\": \"...\", \"qr\": \"...\"}})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
