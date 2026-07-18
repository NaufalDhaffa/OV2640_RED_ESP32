"""
receive_image.py
-----------------
Reads a single frame from ESP32 (OV2640, grayscale) over serial,
reconstructs it as an image, then saves and displays it.

Expected protocol from ESP32:
    [0xAA][0x55][width_L][width_H][height_L][height_H][format][payload...]
    format 0 = grayscale (1 byte per pixel)

Install dependencies first:
    pip install pyserial numpy pillow

Usage:
    python receive_image.py
"""

import struct
import sys
import time

import numpy as np
import serial
from PIL import Image

# ==================== CONFIGURATION ====================
PORT = "COM3"        # change to your ESP32 port (check Device Manager / ls /dev/tty*)
BAUD = 921600
TIMEOUT_SEC = 5
OUTPUT_FILE = "capture.png"

SYNC_BYTE_1 = 0xAA
SYNC_BYTE_2 = 0x55


def wait_for_sync(ser: serial.Serial) -> bool:
    """Search for 2 sync bytes 0xAA 0x55 in the serial stream."""
    prev = None
    start = time.time()
    while time.time() - start < TIMEOUT_SEC:
        b = ser.read(1)
        if not b:
            continue
        val = b[0]
        if prev == SYNC_BYTE_1 and val == SYNC_BYTE_2:
            return True
        prev = val
    return False


def read_frame(ser: serial.Serial) -> Image.Image:
    # trigger capture on the ESP32 side
    ser.reset_input_buffer()
    ser.write(b"c")

    if not wait_for_sync(ser):
        raise TimeoutError("Did not receive sync bytes from ESP32 (check wiring/baud/port)")

    header = ser.read(5)
    if len(header) != 5:
        raise TimeoutError("Incomplete header, connection lost?")

    width, height, fmt = struct.unpack("<HHB", header)
    print(f"Frame diterima: {width}x{height}, format={fmt}")

    if fmt == 0:
        length = width * height
    elif fmt == 1:
        length = width * height * 2  # RGB565
    else:
        raise ValueError(f"Unknown format: {fmt}")

    data = bytearray()
    while len(data) < length:
        chunk = ser.read(length - len(data))
        if not chunk:
            raise TimeoutError(
                f"Payload interrupted, only received {len(data)}/{length} bytes"
            )
        data.extend(chunk)

    if fmt == 0:
        arr = np.frombuffer(bytes(data), dtype=np.uint8).reshape((height, width))
        img = Image.fromarray(arr, mode="L")
    else:
        # RGB565 of OV2640/esp32-camera is BIG-endian (not little-endian),
        raw = np.frombuffer(bytes(data), dtype=">u2").reshape((height, width))
        r = ((raw >> 11) & 0x1F) << 3
        g = ((raw >> 5) & 0x3F) << 2
        b = (raw & 0x1F) << 3
        rgb = np.dstack([r, g, b]).astype(np.uint8)
        img = Image.fromarray(rgb, mode="RGB")

    return img


def main():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as e:
        print(f"Failed to open port {PORT}: {e}")
        sys.exit(1)

    time.sleep(2)  # wait boot

    try:
        img = read_frame(ser)
    except Exception as e:
        print(f"Error: {e}")
        ser.close()
        sys.exit(1)

    ser.close()

    img.save(OUTPUT_FILE)
    print(f"Image saved as {OUTPUT_FILE}")
    img.show()


if __name__ == "__main__":
    main()
