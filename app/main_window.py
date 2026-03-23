import json
from pathlib import Path

import cv2
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QCheckBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QFormLayout,
)

from scanner import (
    DEFAULT_PUSH_URL,
    OffcutScannerEngine,
    build_workshop_bundle,
    load_settings,
    post_workshop_bundle,
    save_settings,
)


class ClickablePreviewLabel(QLabel):
    clicked = Signal(int, int)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(int(event.position().x()), int(event.position().y()))
        super().mousePressEvent(event)


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

        self.preview_label = ClickablePreviewLabel("Camera not started")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(960, 720)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet("background-color: #111; color: #ddd; border: 1px solid #444;")
        self.preview_label.clicked.connect(self.handle_preview_click)
        self.preview_image_shape = None
        self.preview_target_rect = None
        self.calibration_mode = False
        self.calibration_points_px = []

        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.shape_value = QLabel("-")
        self.size_value = QLabel("-")
        self.area_value = QLabel("-")
        self.height_value = QLabel("-")
        self.calibration_value = QLabel("Not loaded")
        self.baseline_value = QLabel("Not captured")
        self.export_status_value = QLabel("Not prepared")

        self.json_view = QPlainTextEdit()
        self.json_view.setReadOnly(True)
        self.json_view.setPlaceholderText("Latest Workshop Hub export bundle will appear here.")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(200)

        settings = load_settings()
        self.material_input = QLineEdit(settings["material"])
        self.thickness_input = QDoubleSpinBox()
        self.thickness_input.setRange(0.0, 100.0)
        self.thickness_input.setDecimals(1)
        self.thickness_input.setSingleStep(0.5)
        self.thickness_input.setValue(float(settings["thickness_mm"]))
        self.qty_input = QSpinBox()
        self.qty_input.setRange(1, 999)
        self.qty_input.setValue(int(settings["qty"]))
        self.notes_input = QPlainTextEdit()
        self.notes_input.setPlainText(settings["notes"])
        self.notes_input.setMaximumBlockCount(20)
        self.notes_input.setMaximumHeight(90)
        self.push_target_label = QLabel("Workshop Hub stock sheet (hardcoded)")
        self.push_target_label.setWordWrap(True)
        self.push_on_save_checkbox = QCheckBox("Push to Google Sheet on save")
        self.push_on_save_checkbox.setChecked(bool(settings["push_on_save"]))
        self.push_now_button = QPushButton("Save + Push to Google Sheets")
        self.start_calibration_button = QPushButton("Start / Edit Calibration")
        self.reset_calibration_points_button = QPushButton("Reset Calibration Points")
        self.save_calibration_button = QPushButton("Save Calibration")
        self.bed_width_input = QDoubleSpinBox()
        self.bed_width_input.setRange(50.0, 10000.0)
        self.bed_width_input.setDecimals(1)
        self.bed_width_input.setValue(400.0)
        self.bed_height_input = QDoubleSpinBox()
        self.bed_height_input.setRange(50.0, 10000.0)
        self.bed_height_input.setDecimals(1)
        self.bed_height_input.setValue(300.0)

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
        self.push_now_button.clicked.connect(self.save_and_push_scan)
        self.start_calibration_button.clicked.connect(self.start_calibration_mode)
        self.reset_calibration_points_button.clicked.connect(self.reset_calibration_points)
        self.save_calibration_button.clicked.connect(self.save_calibration)

        self.stop_button.setEnabled(False)
        self.capture_baseline_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.push_now_button.setEnabled(False)
        self.start_calibration_button.setEnabled(False)
        self.reset_calibration_points_button.setEnabled(False)
        self.save_calibration_button.setEnabled(False)

        self._build_layout()
        self._connect_export_form()

    def _connect_export_form(self):
        widgets = [
            self.material_input,
            self.thickness_input,
            self.qty_input,
            self.notes_input,
            self.push_on_save_checkbox,
        ]
        for widget in widgets:
            signal = None
            if hasattr(widget, "textChanged"):
                signal = widget.textChanged
            elif hasattr(widget, "valueChanged"):
                signal = widget.valueChanged
            elif hasattr(widget, "stateChanged"):
                signal = widget.stateChanged
            if signal is not None:
                signal.connect(self.refresh_export_preview_from_active_view)

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
            self.start_calibration_button,
            self.reset_calibration_points_button,
            self.save_calibration_button,
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
            ("Export", self.export_status_value),
        ]
        for row, (label_text, widget) in enumerate(rows):
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            results_layout.addWidget(label, row, 0)
            results_layout.addWidget(widget, row, 1)

        metadata_box = QGroupBox("Workshop Hub Export")
        metadata_layout = QFormLayout(metadata_box)
        metadata_layout.addRow("Material", self.material_input)
        metadata_layout.addRow("Thickness (mm)", self.thickness_input)
        metadata_layout.addRow("Qty", self.qty_input)
        metadata_layout.addRow("Notes", self.notes_input)

        calibration_box = QGroupBox("Calibration")
        calibration_layout = QFormLayout(calibration_box)
        calibration_layout.addRow("Bed width (mm)", self.bed_width_input)
        calibration_layout.addRow("Bed height (mm)", self.bed_height_input)
        calibration_hint = QLabel("Click the 4 bed corners in preview: top-left, top-right, bottom-right, bottom-left.")
        calibration_hint.setWordWrap(True)
        calibration_layout.addRow(calibration_hint)

        sheets_box = QGroupBox("Google Sheets Push")
        sheets_layout = QVBoxLayout(sheets_box)
        sheets_form = QFormLayout()
        sheets_form.addRow("Target", self.push_target_label)
        sheets_layout.addLayout(sheets_form)
        sheets_layout.addWidget(self.push_on_save_checkbox)
        sheets_layout.addWidget(self.push_now_button)
        sheets_layout.addStretch(1)

        payload_box = QGroupBox("Export Preview")
        payload_layout = QVBoxLayout(payload_box)
        payload_layout.addWidget(self.json_view)

        log_box = QGroupBox("Session Log")
        log_layout = QVBoxLayout(log_box)
        log_layout.addWidget(self.log_view)

        right_content = QWidget()
        right_layout = QVBoxLayout(right_content)
        right_layout.addWidget(controls_box)
        right_layout.addWidget(results_box)
        right_layout.addWidget(calibration_box)
        right_layout.addWidget(sheets_box)
        right_layout.addWidget(metadata_box)
        right_layout.addWidget(payload_box, stretch=1)
        right_layout.addWidget(log_box, stretch=1)

        right_panel = QScrollArea()
        right_panel.setWidgetResizable(True)
        right_panel.setWidget(right_content)

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

    def draw_pending_calibration_overlay(self, image):
        if not self.calibration_mode:
            return image

        overlay = image.copy()
        for index, (x, y) in enumerate(self.calibration_points_px):
            cv2.circle(overlay, (int(x), int(y)), 8, (0, 165, 255), -1)
            cv2.putText(
                overlay,
                str(index + 1),
                (int(x) + 10, int(y) - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 165, 255),
                2,
                cv2.LINE_AA,
            )

        if len(self.calibration_points_px) == 4:
            ordered = self.engine.order_points(self.calibration_points_px).astype(int)
            cv2.polylines(overlay, [ordered], True, (0, 255, 255), 2)

        cv2.putText(
            overlay,
            "Calibration mode: click 4 bed corners",
            (20, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 165, 255),
            2,
            cv2.LINE_AA,
        )
        return overlay

    def set_preview_image(self, image):
        if image is None:
            return

        image = self.draw_pending_calibration_overlay(image)

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
        self.preview_image_shape = (height, width)
        x_offset = (self.preview_label.width() - scaled.width()) // 2
        y_offset = (self.preview_label.height() - scaled.height()) // 2
        self.preview_target_rect = (x_offset, y_offset, scaled.width(), scaled.height())
        self.preview_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        active_view = self.frozen_view if self.freeze_active else self.engine.latest_view
        if active_view is not None:
            self.set_preview_image(active_view.preview_image)

    def refresh_calibration_status(self):
        if self.engine.has_calibration():
            self.calibration_value.setText(Path(self.engine.calibration_file).name)
            if self.engine.bed_points_mm is not None and len(self.engine.bed_points_mm) >= 3:
                width_mm = float(self.engine.bed_points_mm[1][0] - self.engine.bed_points_mm[0][0])
                height_mm = float(self.engine.bed_points_mm[2][1] - self.engine.bed_points_mm[1][1])
                self.bed_width_input.setValue(width_mm)
                self.bed_height_input.setValue(height_mm)
        else:
            self.calibration_value.setText("Not calibrated")

    def update_baseline_status(self):
        self.baseline_value.setText("Captured" if self.engine.has_baseline() else "Not captured")

    def handle_preview_click(self, x, y):
        if not self.calibration_mode or self.preview_image_shape is None or self.preview_target_rect is None:
            return

        rect_x, rect_y, rect_w, rect_h = self.preview_target_rect
        if rect_w <= 0 or rect_h <= 0:
            return
        if x < rect_x or y < rect_y or x > rect_x + rect_w or y > rect_y + rect_h:
            return

        image_h, image_w = self.preview_image_shape
        image_x = (x - rect_x) * image_w / rect_w
        image_y = (y - rect_y) * image_h / rect_h

        if len(self.calibration_points_px) < 4:
            self.calibration_points_px.append([float(image_x), float(image_y)])
            self.save_calibration_button.setEnabled(len(self.calibration_points_px) == 4)
            self.status_label.setText(f"Calibration point {len(self.calibration_points_px)}/4 recorded.")
            self.log(f"Calibration point {len(self.calibration_points_px)} captured at px=({image_x:.1f}, {image_y:.1f}).")
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
        self.capture_baseline_button.setEnabled(self.engine.has_calibration())
        self.start_calibration_button.setEnabled(True)
        self.reset_calibration_points_button.setEnabled(False)
        self.save_calibration_button.setEnabled(False)
        self.freeze_button.setEnabled(True)
        self.refresh_calibration_status()
        self.update_baseline_status()
        if self.engine.has_calibration():
            self.status_label.setText("Camera started.")
            self.log("Camera started.")
        else:
            self.status_label.setText("Camera started. No saved calibration found.")
            self.log("Camera started. No saved calibration found; use in-app calibration.")

    def stop_camera(self):
        self.timer.stop()
        self.engine.stop_camera()
        self.freeze_active = False
        self.calibration_mode = False
        self.calibration_points_px.clear()
        self.frozen_view = None
        self.preview_label.setText("Camera stopped")
        self.preview_label.setPixmap(QPixmap())
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.capture_baseline_button.setEnabled(False)
        self.start_calibration_button.setEnabled(False)
        self.reset_calibration_points_button.setEnabled(False)
        self.save_calibration_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.push_now_button.setEnabled(False)
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
        self.refresh_calibration_status()
        self.update_baseline_status()

        payload = view.payload
        if payload is None:
            self.shape_value.setText("-")
            self.size_value.setText("-")
            self.area_value.setText("-")
            self.height_value.setText("-")
            self.export_status_value.setText("Waiting for detection")
            self.json_view.clear()
            self.save_button.setEnabled(False)
            self.push_now_button.setEnabled(False)
            return

        self.shape_value.setText(payload["shape_type"])
        self.size_value.setText(f"{payload['bbox_w_mm']:.1f} x {payload['bbox_h_mm']:.1f} mm")
        self.area_value.setText(f"{payload['area_mm2']:.1f} mm²")
        self.height_value.setText(f"P95: {payload['height_mm_above_bed_p95']:.1f} mm")
        if self.thickness_input.value() == 0:
            self.thickness_input.setValue(float(payload["height_mm_above_bed_p95"]))
        try:
            self.refresh_export_preview(payload)
        except ValueError as exc:
            self.export_status_value.setText(str(exc))
            self.json_view.setPlainText(json.dumps({"scan_payload": payload}, indent=2))
        self.save_button.setEnabled(view.has_detection)
        self.push_now_button.setEnabled(view.has_detection)

    def current_export_metadata(self):
        return {
            "material": self.material_input.text().strip(),
            "thickness_mm": float(self.thickness_input.value()),
            "qty": int(self.qty_input.value()),
            "grade": "",
            "location": "workshop",
            "sheet_origin_job": "",
            "sheet_origin_index": "",
            "min_internal_width_mm": "",
            "usable_score": "",
            "notes": self.notes_input.toPlainText().strip(),
            "push_url": DEFAULT_PUSH_URL,
            "push_on_save": self.push_on_save_checkbox.isChecked(),
        }

    def persist_export_settings(self):
        path = save_settings(self.current_export_metadata())
        self.log(f"Saved Workshop Hub settings: {path}")

    def current_export_bundle(self, payload):
        metadata = self.current_export_metadata()
        if not metadata["material"] or metadata["thickness_mm"] <= 0:
            return None
        return build_workshop_bundle(payload, metadata)

    def refresh_export_preview(self, payload):
        bundle = self.current_export_bundle(payload)
        if bundle is None:
            self.export_status_value.setText("Enter material + thickness")
            self.json_view.setPlainText(json.dumps({"scan_payload": payload}, indent=2))
            return

        self.export_status_value.setText("Bundle ready")
        self.json_view.setPlainText(json.dumps(bundle, indent=2))

    def refresh_export_preview_from_active_view(self, *_args):
        scan_result = self.active_scan_result()
        if scan_result is None:
            return
        try:
            self.refresh_export_preview(scan_result["payload"])
        except ValueError as exc:
            self.export_status_value.setText(str(exc))

    def capture_baseline(self):
        try:
            self.engine.capture_baseline()
        except Exception as exc:
            QMessageBox.warning(self, "Baseline", str(exc))
            self.log(f"Baseline capture failed: {exc}")
            return

        self.update_baseline_status()
        self.log(f"Empty-bed baseline captured and saved to {self.engine.baseline_file}.")

    def start_calibration_mode(self):
        if self.engine.latest_view is None:
            QMessageBox.information(self, "Calibration", "Start the camera and wait for a live frame first.")
            return

        self.calibration_mode = True
        self.calibration_points_px = []
        self.reset_calibration_points_button.setEnabled(True)
        self.save_calibration_button.setEnabled(False)
        self.capture_baseline_button.setEnabled(False)
        self.baseline_value.setText("Not captured")
        self.status_label.setText("Calibration mode: click 4 bed corners.")
        self.log("Calibration mode started. Click the 4 bed corners in the preview.")
        active_view = self.frozen_view if self.freeze_active else self.engine.latest_view
        if active_view is not None:
            self.set_preview_image(active_view.preview_image)

    def reset_calibration_points(self):
        self.calibration_points_px = []
        self.save_calibration_button.setEnabled(False)
        self.status_label.setText("Calibration points reset.")
        self.log("Calibration points reset.")
        active_view = self.frozen_view if self.freeze_active else self.engine.latest_view
        if active_view is not None:
            self.set_preview_image(active_view.preview_image)

    def save_calibration(self):
        if len(self.calibration_points_px) != 4:
            QMessageBox.warning(self, "Calibration", "Click all 4 bed corners before saving.")
            return

        bed_points_mm = self.engine.default_bed_points_mm(
            self.bed_width_input.value(),
            self.bed_height_input.value(),
        )
        snapshot_image = None
        active_view = self.frozen_view if self.freeze_active else self.engine.latest_view
        if active_view is not None:
            snapshot_image = active_view.color_image

        try:
            calibration_path = self.engine.save_calibration(
                self.calibration_points_px,
                bed_points_mm=bed_points_mm,
                snapshot_image=snapshot_image,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Calibration Error", str(exc))
            self.log(f"Calibration save failed: {exc}")
            return

        self.calibration_mode = False
        self.calibration_points_px = []
        self.reset_calibration_points_button.setEnabled(False)
        self.save_calibration_button.setEnabled(False)
        self.capture_baseline_button.setEnabled(True)
        self.refresh_calibration_status()
        self.update_baseline_status()
        self.status_label.setText("Calibration saved. Capture a fresh empty-bed baseline.")
        self.log(f"Calibration saved to {calibration_path}. Baseline cleared; capture a new empty-bed baseline.")
        if active_view is not None:
            self.set_preview_image(active_view.preview_image)

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

    def save_scan(self, force_push=False):
        scan_result = self.active_scan_result()
        if scan_result is None:
            QMessageBox.information(self, "Save Scan", "No valid scan result is available to save.")
            return

        try:
            metadata = self.current_export_metadata()
        except ValueError as exc:
            QMessageBox.warning(self, "Save Scan", str(exc))
            return
        if not metadata["material"]:
            QMessageBox.warning(self, "Save Scan", "Enter a material name before saving to Workshop Hub format.")
            return
        if metadata["thickness_mm"] <= 0:
            QMessageBox.warning(self, "Save Scan", "Enter a thickness greater than 0 mm.")
            return
        should_push = metadata["push_on_save"] or force_push
        metadata["push_on_save"] = should_push

        bundle = build_workshop_bundle(scan_result["payload"], metadata)
        self.persist_export_settings()

        try:
            saved = self.engine.save_scan_result(scan_result, workshop_bundle=bundle)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            self.log(f"Save failed: {exc}")
            return

        push_message = "Sheet push skipped."
        if should_push:
            try:
                response = post_workshop_bundle(metadata["push_url"], bundle)
            except Exception as exc:
                QMessageBox.critical(self, "Sheet Push Error", str(exc))
                self.log(f"Sheet push failed: {exc}")
                return

            response_body = response["body"]
            push_message = f"Sheet push OK (HTTP {response['status_code']})."
            if isinstance(response_body, dict):
                spreadsheet_name = response_body.get("spreadsheet_name")
                inventory_rows = response_body.get("inventory_rows_written")
                shape_rows = response_body.get("shape_rows_written")
                event_rows = response_body.get("event_rows_written")
                preview_rows = response_body.get("preview_rows_written")
                counts = (
                    f"inventory={inventory_rows}, shapes={shape_rows}, "
                    f"events={event_rows}, previews={preview_rows}"
                )
                if spreadsheet_name:
                    push_message = f"Sheet push OK to '{spreadsheet_name}' (HTTP {response['status_code']}; {counts})."
                else:
                    push_message = f"Sheet push OK (HTTP {response['status_code']}; {counts})."
            self.log(f"Sheet push response: {response_body}")

        self.log(f"Saved preview: {saved['image_path']}")
        self.log(f"Saved mask: {saved['mask_path']}")
        self.log(f"Saved json: {saved['json_path']}")
        if saved["workshop_json_path"]:
            self.log(f"Saved Workshop Hub bundle: {saved['workshop_json_path']}")
        QMessageBox.information(
            self,
            "Scan Saved",
            (
                f"Preview: {saved['image_path']}\n"
                f"Mask: {saved['mask_path']}\n"
                f"Scan JSON: {saved['json_path']}\n"
                f"Workshop Bundle: {saved['workshop_json_path']}\n"
                f"{push_message}"
            ),
        )

    def save_and_push_scan(self):
        self.push_on_save_checkbox.setChecked(True)
        self.save_scan(force_push=True)
