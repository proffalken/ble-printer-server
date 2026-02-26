#!/usr/bin/env python3
"""BLE Print Server — prints text + QR code to a TiMini-compatible thermal printer over BLE.

Usage:
  python print_server.py [--bluetooth NAME_OR_ADDR] [--port 8080]
  python print_server.py --serial /dev/rfcomm0 --model X6

Environment variables (used as defaults):
  PRINTER_BLUETOOTH   Bluetooth name prefix or address
  PRINTER_SERIAL      Serial port path
  PRINTER_MODEL       Model override
  PRINT_PORT          HTTP port (default: 8080)
  PRINT_HOST          Bind address (default: 0.0.0.0)

Send text to print:
  curl -X POST http://localhost:8080/print -d "https://example.com"
  curl "http://localhost:8080/print?text=Hello"
"""

from __future__ import annotations

import argparse
import asyncio
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


def compose_qr_text_image(text: str, printer_width: int) -> Image.Image:
    try:
        import qrcode
    except ImportError:
        raise RuntimeError("qrcode is required: pip install 'qrcode[pil]'")

    qr_size = printer_width // 2

    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
    qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)

    text_area_width = printer_width - qr_size
    font_path = find_monospace_bold_font()
    columns = columns_for_width(text_area_width)
    font = fit_truetype_font(font_path, text_area_width, columns)
    lines = wrap_text_lines(text, columns)
    lh = font_line_height(font)
    text_block_height = max(1, lh * len(lines))

    text_img = Image.new("L", (text_area_width, text_block_height), 255)
    draw = ImageDraw.Draw(text_img)
    y = 0
    for line in lines:
        draw.text((0, y), line, font=font, fill=0)
        y += lh

    total_height = max(qr_size, text_block_height)
    out = Image.new("L", (printer_width, total_height), 255)
    out.paste(qr_img, (0, (total_height - qr_size) // 2))
    out.paste(text_img, (qr_size, (total_height - text_block_height) // 2))

    return out


def build_print_data(text: str, model: PrinterModel) -> bytes:
    printer_width = PrintJobBuilder._normalized_width(model.width)
    img = compose_qr_text_image(text, printer_width)
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

    def print_text(self, text: str) -> None:
        with self._lock:
            if self.args.serial:
                self._print_serial(text)
            else:
                asyncio.run(asyncio.wait_for(self._print_ble(text), timeout=BLE_PRINT_TIMEOUT))

    def _print_serial(self, text: str) -> None:
        model = require_model(self._registry, self.args.model)
        data = build_print_data(text, model)
        write_serial_blocking(
            self.args.serial, data, model.img_mtu or 180, model.interval_ms or 4
        )

    async def _print_ble(self, text: str) -> None:
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
        data = build_print_data(text, model)
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

    def _extract_text(self) -> str | None:
        parsed = urlparse(self.path)
        if parsed.path != "/print":
            return None
        qs = parse_qs(parsed.query)
        if "text" in qs:
            return qs["text"][0]
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._respond(400, "Invalid Content-Length.\n")
                return ""  # signal handled, empty string caught by strip() check
            if length < 0:
                self._respond(400, "Invalid Content-Length.\n")
                return ""
            if length > MAX_BODY_BYTES:
                self._respond(413, f"Request body too large (max {MAX_BODY_BYTES} bytes).\n")
                return ""
            if length:
                return self.rfile.read(length).decode("utf-8", errors="replace")
        return None

    def _respond(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle(self) -> None:
        text = self._extract_text()
        if text is None:
            self._respond(404, "Not found.\n\nUsage: POST or GET /print with body or ?text=...\n")
            return
        if not text.strip():
            self._respond(400, "Empty text.\n")
            return
        try:
            self._server.print_text(text)
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
    print(f"  POST http://{args.host}:{args.port}/print  (text in body)")
    print(f"  GET  http://{args.host}:{args.port}/print?text=...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
