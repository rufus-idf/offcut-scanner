import json
from pathlib import Path

import cv2
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from scanner import OffcutScannerEngine


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Offcut Scanner")
        self.resize(1400, 860)

        self.engine = OffcutScannerEngine()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_frame)
        self.timer.setInterval(60)

        self.frozen_view = None
        self.freeze_active = False

        self.preview_label = QLabel("Camera not started")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(960, 720)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet("background-color: #111; color: #ddd; border: 1px solid #444;")

        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.shape_value = QLabel("-")
        self.size_value = QLabel("-")
        self.area_value = QLabel("-")
        self.height_value = QLabel("-")
        self.calibration_value = QLabel("Not loaded")
        self.baseline_value = QLabel("Not captured")

        self.json_view = QPlainTextEdit()
        self.json_view.setReadOnly(True)
        self.json_view.setPlaceholderText("Latest scan payload will appear here.")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(200)

        self.start_button = QPushButton("Start Camera")
        self.stop_button = QPushButton("Stop Camera")
        self.capture_baseline_button = QPushButton("Capture Empty Bed")
        self.freeze_button = QPushButton("Freeze Scan")
        self.resume_button = QPushButton("Resume Live")
        self.save_button = QPushButton("Save Scan")

        self.start_button.clicked.connect(self.start_camera)
        self.stop_button.clicked.connect(self.stop_camera)
        self.capture_baseline_button.clicked.connect(self.capture_baseline)
        self.freeze_button.clicked.connect(self.freeze_scan)
        self.resume_button.clicked.connect(self.resume_live)
        self.save_button.clicked.connect(self.save_scan)

        self.stop_button.setEnabled(False)
        self.capture_baseline_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.save_button.setEnabled(False)

        self._build_layout()

    def _build_layout(self):
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.addWidget(self.preview_label)

        controls_box = QGroupBox("Controls")
        controls_layout = QVBoxLayout(controls_box)
        for button in [
            self.start_button,
            self.stop_button,
            self.capture_baseline_button,
            self.freeze_button,
            self.resume_button,
            self.save_button,
        ]:
            controls_layout.addWidget(button)
        controls_layout.addStretch(1)

        results_box = QGroupBox("Latest Scan")
        results_layout = QGridLayout(results_box)
        rows = [
            ("Status", self.status_label),
            ("Shape", self.shape_value),
            ("Size", self.size_value),
            ("Area", self.area_value),
            ("Height", self.height_value),
            ("Calibration", self.calibration_value),
            ("Baseline", self.baseline_value),
        ]
        for row, (label_text, widget) in enumerate(rows):
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            results_layout.addWidget(label, row, 0)
            results_layout.addWidget(widget, row, 1)

        payload_box = QGroupBox("Scan Payload")
        payload_layout = QVBoxLayout(payload_box)
        payload_layout.addWidget(self.json_view)

        log_box = QGroupBox("Session Log")
        log_layout = QVBoxLayout(log_box)
        log_layout.addWidget(self.log_view)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(controls_box)
        right_layout.addWidget(results_box)
        right_layout.addWidget(payload_box, stretch=1)
        right_layout.addWidget(log_box, stretch=1)

        splitter = QSplitter()
        splitter.addWidget(preview_container)
        splitter.addWidget(right_panel)
        splitter.setSizes([950, 450])

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.addWidget(splitter)
        self.setCentralWidget(central)

    def log(self, message):
        self.log_view.appendPlainText(message)

    def set_preview_image(self, image):
        if image is None:
            return

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        bytes_per_line = channels * width
        qt_image = QImage(rgb.data, width, height, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qt_image.copy())
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        active_view = self.frozen_view if self.freeze_active else self.engine.latest_view
        if active_view is not None:
            self.set_preview_image(active_view.preview_image)

    def start_camera(self):
        try:
            self.engine.start_camera()
        except Exception as exc:
            QMessageBox.critical(self, "Camera Error", str(exc))
            self.log(f"Failed to start camera: {exc}")
            return

        self.timer.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.capture_baseline_button.setEnabled(True)
        self.freeze_button.setEnabled(True)
        self.calibration_value.setText(Path(self.engine.calibration_file).name)
        self.status_label.setText("Camera started.")
        self.log("Camera started.")

    def stop_camera(self):
        self.timer.stop()
        self.engine.stop_camera()
        self.freeze_active = False
        self.frozen_view = None
        self.preview_label.setText("Camera stopped")
        self.preview_label.setPixmap(QPixmap())
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.capture_baseline_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status_label.setText("Camera stopped.")
        self.log("Camera stopped.")

    def refresh_frame(self):
        if self.freeze_active:
            return

        try:
            view = self.engine.process_next_frame()
        except Exception as exc:
            self.timer.stop()
            QMessageBox.critical(self, "Capture Error", str(exc))
            self.log(f"Frame processing failed: {exc}")
            self.stop_camera()
            return

        if view is None:
            return

        self.update_from_view(view)

    def update_from_view(self, view):
        self.set_preview_image(view.preview_image)
        self.status_label.setText(view.status_text)

        if self.engine.baseline_depth_mm is not None:
            self.baseline_value.setText("Captured")

        payload = view.payload
        if payload is None:
            self.shape_value.setText("-")
            self.size_value.setText("-")
            self.area_value.setText("-")
            self.height_value.setText("-")
            self.json_view.clear()
            self.save_button.setEnabled(False)
            return

        self.shape_value.setText(payload["shape_type"])
        self.size_value.setText(f"{payload['bbox_w_mm']:.1f} x {payload['bbox_h_mm']:.1f} mm")
        self.area_value.setText(f"{payload['area_mm2']:.1f} mm²")
        self.height_value.setText(f"P95: {payload['height_mm_above_bed_p95']:.1f} mm")
        self.json_view.setPlainText(json.dumps(payload, indent=2))
        self.save_button.setEnabled(view.has_detection)

    def capture_baseline(self):
        try:
            self.engine.capture_baseline()
        except Exception as exc:
            QMessageBox.warning(self, "Baseline", str(exc))
            self.log(f"Baseline capture failed: {exc}")
            return

        self.baseline_value.setText("Captured")
        self.log("Empty-bed baseline captured.")

    def freeze_scan(self):
        view = self.engine.latest_view
        if view is None:
            QMessageBox.information(self, "Freeze", "No live frame available yet.")
            return

        self.freeze_active = True
        self.frozen_view = view
        self.resume_button.setEnabled(True)
        self.log("Live preview frozen.")
        self.update_from_view(view)

    def resume_live(self):
        self.freeze_active = False
        self.frozen_view = None
        self.resume_button.setEnabled(False)
        self.log("Live preview resumed.")

    def active_scan_result(self):
        if self.freeze_active and self.frozen_view is not None:
            return self.frozen_view.scan_result
        if self.engine.latest_view is not None:
            return self.engine.latest_view.scan_result
        return None


    def closeEvent(self, event):
        if self.timer.isActive():
            self.timer.stop()
        self.engine.stop_camera()
        super().closeEvent(event)

    def save_scan(self):
        scan_result = self.active_scan_result()
        if scan_result is None:
            QMessageBox.information(self, "Save Scan", "No valid scan result is available to save.")
            return

        try:
            saved = self.engine.save_scan_result(scan_result)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            self.log(f"Save failed: {exc}")
            return

        self.log(f"Saved preview: {saved['image_path']}")
        self.log(f"Saved mask: {saved['mask_path']}")
        self.log(f"Saved json: {saved['json_path']}")
        QMessageBox.information(
            self,
            "Scan Saved",
            f"Preview: {saved['image_path']}\nMask: {saved['mask_path']}\nJSON: {saved['json_path']}",
        )
