"""
stream_gui.py
--------------
PyQt5 GUI for live-preview of images from ESP32 (OV2640, grayscale) over serial.

Without PSRAM we use raw QVGA/QQVGA grayscale (not JPEG), so frame size is
large relative to serial baud rate. Expected frame rate is around ~1-5 fps
(depending on resolution and baud). This is NOT real-time video, but it is
useful for live preview.

Install dependencies first:
    pip install pyserial numpy pillow pyqt5

Usage:
    python stream_gui.py
"""

import struct
import sys
import time

import numpy as np
import serial
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ==================== CONFIGURATION ====================
PORT = "COM3"       # change to your ESP32 port
BAUD = 921600
READ_TIMEOUT = 3    # seconds, per-frame timeout (not connection timeout)

SYNC_BYTE_1 = 0xAA
SYNC_BYTE_2 = 0x55


class CaptureThread(QThread):
    """Separate thread so the serial read loop does not freeze the GUI."""

    frame_ready = pyqtSignal(np.ndarray)
    error_occurred = pyqtSignal(str)
    fps_updated = pyqtSignal(float)

    def __init__(self, port: str, baud: int):
        super().__init__()
        self.port = port
        self.baud = baud
        self._running = False
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=READ_TIMEOUT)
            time.sleep(2)  # wait boot time
        except serial.SerialException as e:
            self.error_occurred.emit(f"Failed to open port {self.port}: {e}")
            return

        self._running = True
        last_time = time.time()

        while self._running:
            try:
                frame = self._read_one_frame()
                if frame is not None:
                    self.frame_ready.emit(frame)
                    now = time.time()
                    fps = 1.0 / max(now - last_time, 1e-6)
                    last_time = now
                    self.fps_updated.emit(fps)
            except TimeoutError as e:
                # frame drop, continue trying without stopping the stream
                continue
            except Exception as e:
                self.error_occurred.emit(str(e))
                break

        if self.ser:
            self.ser.close()

    def _wait_for_sync(self) -> bool:
        prev = None
        start = time.time()
        while time.time() - start < READ_TIMEOUT:
            if not self._running:
                return False
            b = self.ser.read(1)
            if not b:
                continue
            val = b[0]
            if prev == SYNC_BYTE_1 and val == SYNC_BYTE_2:
                return True
            prev = val
        return False

    def _read_one_frame(self):
        self.ser.reset_input_buffer()
        self.ser.write(b"c")

        if not self._wait_for_sync():
            raise TimeoutError("sync not found")

        header = self.ser.read(5)
        if len(header) != 5:
            raise TimeoutError("incomplete header")

        width, height, fmt = struct.unpack("<HHB", header)
        if fmt == 0:
            length = width * height
        elif fmt == 1:
            length = width * height * 2
        else:
            raise TimeoutError(f"format {fmt} is not supported by this GUI")

        data = bytearray()
        while len(data) < length:
            chunk = self.ser.read(length - len(data))
            if not chunk:
                raise TimeoutError("payload interrupted")
            data.extend(chunk)

        if fmt == 0:
            arr = np.frombuffer(bytes(data), dtype=np.uint8).reshape((height, width))
        else:
            # RGB565 of OV2640/esp32-camera is BIG-endian (not little-endian),
            raw = np.frombuffer(bytes(data), dtype=">u2").reshape((height, width))
            r = ((raw >> 11) & 0x1F).astype(np.uint8) << 3
            g = ((raw >> 5) & 0x3F).astype(np.uint8) << 2
            b = (raw & 0x1F).astype(np.uint8) << 3
            arr = np.dstack([r, g, b])  # shape (height, width, 3), RGB888

        return arr

    def stop(self):
        self._running = False
        self.wait(READ_TIMEOUT + 1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32-CAM Live Preview (no PSRAM)")
        self.resize(500, 450)

        self.image_label = QLabel("No image yet. Click Start.")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(320, 240)
        self.image_label.setStyleSheet("background-color: #222; color: white;")

        self.status_label = QLabel("Status: stopped")
        self.fps_label = QLabel("FPS: -")

        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_stream)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_stream)
        self.stop_button.setEnabled(False)

        layout = QVBoxLayout()
        layout.addWidget(self.image_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.fps_label)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.capture_thread = None

    def start_stream(self):
        self.capture_thread = CaptureThread(PORT, BAUD)
        self.capture_thread.frame_ready.connect(self.update_image)
        self.capture_thread.error_occurred.connect(self.handle_error)
        self.capture_thread.fps_updated.connect(self.update_fps)
        self.capture_thread.start()

        self.status_label.setText("Status: streaming...")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop_stream(self):
        if self.capture_thread:
            self.capture_thread.stop()
            self.capture_thread = None

        self.status_label.setText("Status: stopped")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def update_image(self, arr: np.ndarray):
        arr = np.ascontiguousarray(arr)  # QImage requires contiguous memory layout

        if arr.ndim == 2:
            height, width = arr.shape
            qimg = QImage(arr.data, width, height, width, QImage.Format_Grayscale8)
        else:
            height, width, _ = arr.shape
            bytes_per_line = width * 3
            qimg = QImage(
                arr.data, width, height, bytes_per_line, QImage.Format_RGB888
            )

        # keep a reference to the array so it is not garbage-collected while
        # QImage is still using its buffer memory
        self._last_arr = arr

        pixmap = QPixmap.fromImage(qimg)
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.width(),
                self.image_label.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def update_fps(self, fps: float):
        self.fps_label.setText(f"FPS: {fps:.2f}")

    def handle_error(self, message: str):
        self.status_label.setText(f"Status: error - {message}")
        self.stop_stream()

    def closeEvent(self, event):
        self.stop_stream()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
