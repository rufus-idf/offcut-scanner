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
    QMainWindow,
    QMessageBox,
    QCheckBox,
    QComboBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QDoubleSpinBox,
    QSplitter,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QFormLayout,
)

from scanner import (
    DEFAULT_PUSH_URL,
    OffcutScannerEngine,
    build_workshop_bundle,
    fetch_texture_library_materials,
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
    APP_VERSION = "v1.0.0"

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
        self.is_saving = False

        self.preview_label = ClickablePreviewLabel("Camera not started")
        self.preview_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.preview_label.setMinimumSize(960, 720)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet("background-color: #111; color: #ddd; border: 1px solid #444;")
        self.preview_label.clicked.connect(self.handle_preview_click)
        self.preview_image_shape = None
        self.preview_target_rect = None
        self.calibration_mode = False
        self.calibration_points_px = []
        self.zoom_factor = 1.0

        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.shape_value = QLabel("-")
        self.size_value = QLabel("-")
        self.area_value = QLabel("-")
        self.height_value = QLabel("-")
        self.calibration_value = QLabel("Not loaded")
        self.baseline_value = QLabel("Not captured")
        self.export_status_value = QLabel("Not prepared")
        self.version_value = QLabel(self.APP_VERSION)
        self.camera_preflight_value = QLabel("Not started")
        self.calibration_preflight_value = QLabel("Missing")
        self.baseline_preflight_value = QLabel("Missing")
        self.materials_preflight_value = QLabel("Loading")
        self.sheets_preflight_value = QLabel("Not tested")
        self.last_push_value = QLabel("No pushes yet")

        self.json_view = QPlainTextEdit()
        self.json_view.setReadOnly(True)
        self.json_view.setPlaceholderText("Latest Workshop Hub export bundle will appear here.")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(200)

        settings = load_settings()
        self.material_input = QComboBox()
        self.material_input.setEditable(False)
        self.material_input.addItem("Loading materials...")
        self.material_input.setEnabled(False)
        self.saved_material_name = settings["material"]
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
        self.refresh_materials_button = QPushButton("Refresh Materials")
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
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(100, 400)
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setValue(100)
        self.zoom_label = QLabel("100%")
        self.zoom_in_button = QPushButton("Zoom +")
        self.zoom_out_button = QPushButton("Zoom -")
        self.zoom_reset_button = QPushButton("Reset Zoom")

        self.start_button = QPushButton("Start Camera")
        self.stop_button = QPushButton("Stop Camera")
        self.capture_baseline_button = QPushButton("Capture Empty Bed")
        self.freeze_button = QPushButton("Freeze Scan")
        self.resume_button = QPushButton("Resume Live")
        self.save_button = QPushButton("Save Scan")
        self.retry_pending_pushes_button = QPushButton("Retry Pending Pushes")

        self.start_button.clicked.connect(self.start_camera)
        self.stop_button.clicked.connect(self.stop_camera)
        self.capture_baseline_button.clicked.connect(self.capture_baseline)
        self.freeze_button.clicked.connect(self.freeze_scan)
        self.resume_button.clicked.connect(self.resume_live)
        self.save_button.clicked.connect(self.save_scan)
        self.retry_pending_pushes_button.clicked.connect(self.retry_pending_pushes)
        self.push_now_button.clicked.connect(self.save_and_push_scan)
        self.refresh_materials_button.clicked.connect(self.refresh_material_options)
        self.start_calibration_button.clicked.connect(self.start_calibration_mode)
        self.reset_calibration_points_button.clicked.connect(self.reset_calibration_points)
        self.save_calibration_button.clicked.connect(self.save_calibration)
        self.zoom_slider.valueChanged.connect(self.on_zoom_changed)
        self.zoom_in_button.clicked.connect(lambda: self.zoom_slider.setValue(min(400, self.zoom_slider.value() + 25)))
        self.zoom_out_button.clicked.connect(lambda: self.zoom_slider.setValue(max(100, self.zoom_slider.value() - 25)))
        self.zoom_reset_button.clicked.connect(lambda: self.zoom_slider.setValue(100))

        self.stop_button.setEnabled(False)
        self.capture_baseline_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.push_now_button.setEnabled(False)
        self.start_calibration_button.setEnabled(False)
        self.reset_calibration_points_button.setEnabled(False)
        self.save_calibration_button.setEnabled(False)
        self.retry_pending_pushes_button.setEnabled(False)

        self._build_layout()
        self.apply_modern_theme()
        self._connect_export_form()
        self.refresh_material_options()

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
            if hasattr(widget, "currentTextChanged"):
                signal = widget.currentTextChanged
            elif hasattr(widget, "textChanged"):
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
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setWidget(self.preview_label)
        preview_layout.addWidget(self.preview_scroll)

        controls_box = QGroupBox("Controls")
        controls_layout = QVBoxLayout(controls_box)
        for button in [
            self.start_button,
            self.stop_button,
            self.freeze_button,
            self.resume_button,
            self.save_button,
            self.retry_pending_pushes_button,
        ]:
            controls_layout.addWidget(button)
        controls_layout.addStretch(1)

        recalibration_controls_box = QGroupBox("Recalibration Controls")
        recalibration_controls_layout = QVBoxLayout(recalibration_controls_box)
        for button in [
            self.capture_baseline_button,
            self.start_calibration_button,
            self.reset_calibration_points_button,
            self.save_calibration_button,
        ]:
            recalibration_controls_layout.addWidget(button)
        recalibration_controls_layout.addStretch(1)

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

        preflight_box = QGroupBox("Preflight")
        preflight_layout = QGridLayout(preflight_box)
        preflight_rows = [
            ("Camera", self.camera_preflight_value),
            ("Calibration", self.calibration_preflight_value),
            ("Baseline", self.baseline_preflight_value),
            ("Materials", self.materials_preflight_value),
            ("Sheets Push", self.sheets_preflight_value),
            ("App Version", self.version_value),
        ]
        for row, (label_text, widget) in enumerate(preflight_rows):
            preflight_layout.addWidget(QLabel(label_text), row, 0)
            preflight_layout.addWidget(widget, row, 1)

        last_push_box = QGroupBox("Last Push Result")
        self.last_push_value.setWordWrap(True)
        last_push_layout = QVBoxLayout(last_push_box)
        last_push_layout.addWidget(self.last_push_value)

        metadata_box = QGroupBox("Workshop Hub Export")
        metadata_layout = QFormLayout(metadata_box)
        metadata_layout.addRow("Material", self.material_input)
        metadata_layout.addRow("", self.refresh_materials_button)
        metadata_layout.addRow("Thickness (mm)", self.thickness_input)
        metadata_layout.addRow("Qty", self.qty_input)
        metadata_layout.addRow("Notes", self.notes_input)

        calibration_box = QGroupBox("Calibration")
        calibration_layout = QFormLayout(calibration_box)
        calibration_layout.addRow("Bed width (mm)", self.bed_width_input)
        calibration_layout.addRow("Bed height (mm)", self.bed_height_input)
        zoom_buttons_row = QWidget()
        zoom_buttons_layout = QHBoxLayout(zoom_buttons_row)
        zoom_buttons_layout.setContentsMargins(0, 0, 0, 0)
        zoom_buttons_layout.addWidget(self.zoom_out_button)
        zoom_buttons_layout.addWidget(self.zoom_in_button)
        zoom_buttons_layout.addWidget(self.zoom_reset_button)
        calibration_layout.addRow("Calibration Zoom", self.zoom_slider)
        calibration_layout.addRow("Zoom Level", self.zoom_label)
        calibration_layout.addRow("", zoom_buttons_row)
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

        def build_tab(contents, add_stretch=False):
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            for item in contents:
                stretch = 0
                widget = item
                if isinstance(item, tuple):
                    widget, stretch = item
                tab_layout.addWidget(widget, stretch=stretch)
            if add_stretch:
                tab_layout.addStretch(1)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(tab)
            return scroll

        tabs = QTabWidget()
        tabs.addTab(
            build_tab(
                [
                    controls_box,
                    preflight_box,
                    sheets_box,
                    metadata_box,
                    results_box,
                    last_push_box,
                ],
                add_stretch=True,
            ),
            "Main Controls",
        )
        tabs.addTab(
            build_tab(
                [
                    recalibration_controls_box,
                    calibration_box,
                ],
                add_stretch=True,
            ),
            "Recalibration",
        )
        tabs.addTab(
            build_tab(
                [
                    (payload_box, 1),
                    (log_box, 1),
                ]
            ),
            "Session",
        )

        splitter = QSplitter()
        splitter.addWidget(preview_container)
        splitter.addWidget(tabs)
        splitter.setSizes([950, 450])

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.addWidget(splitter)
        self.setCentralWidget(central)

    def apply_modern_theme(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #111827;
                color: #E5E7EB;
                font-size: 13px;
            }
            QGroupBox {
                background-color: #1F2937;
                border: 1px solid #374151;
                border-radius: 14px;
                margin-top: 14px;
                padding: 14px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
                color: #F9FAFB;
            }
            QTabWidget::pane {
                border: 1px solid #374151;
                border-radius: 14px;
                background-color: #111827;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #1E293B;
                color: #CBD5E1;
                border: 1px solid #334155;
                border-bottom: none;
                padding: 10px 16px;
                margin-right: 6px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background-color: #2563EB;
                color: white;
            }
            QTabBar::tab:hover:!selected {
                background-color: #334155;
            }
            QPushButton {
                background-color: #2563EB;
                color: white;
                border: none;
                border-radius: 10px;
                padding: 10px 14px;
                min-height: 18px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3B82F6;
            }
            QPushButton:pressed {
                background-color: #1D4ED8;
            }
            QPushButton:disabled {
                background-color: #374151;
                color: #9CA3AF;
            }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QScrollArea {
                background-color: #0F172A;
                color: #E5E7EB;
                border: 1px solid #334155;
                border-radius: 10px;
                padding: 8px;
                selection-background-color: #2563EB;
            }
            QComboBox::drop-down {
                border: none;
                width: 26px;
            }
            QComboBox QAbstractItemView {
                background-color: #0F172A;
                color: #E5E7EB;
                border: 1px solid #334155;
                selection-background-color: #2563EB;
            }
            QLabel {
                color: #E5E7EB;
            }
            QCheckBox {
                spacing: 10px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 6px;
                border: 1px solid #64748B;
                background-color: #0F172A;
            }
            QCheckBox::indicator:checked {
                background-color: #2563EB;
                border: 1px solid #2563EB;
            }
            QScrollBar:vertical {
                background: #111827;
                width: 12px;
                margin: 4px;
            }
            QScrollBar::handle:vertical {
                background: #475569;
                border-radius: 6px;
                min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QSplitter::handle {
                background-color: #1E293B;
                width: 6px;
            }
            """
        )
        self.preview_label.setStyleSheet(
            """
            background-color: #020617;
            color: #CBD5E1;
            border: 1px solid #334155;
            border-radius: 18px;
            padding: 12px;
            """
        )

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
        viewport_size = self.preview_scroll.viewport().size()
        base_width = max(1, viewport_size.width())
        base_height = max(1, viewport_size.height())
        scaled = pixmap.scaled(
            base_width * self.zoom_factor,
            base_height * self.zoom_factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview_image_shape = (height, width)
        self.preview_target_rect = (0, 0, scaled.width(), scaled.height())
        self.preview_label.resize(scaled.size())
        self.preview_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        active_view = self.frozen_view if self.freeze_active else self.engine.latest_view
        if active_view is not None:
            self.set_preview_image(active_view.preview_image)

    def refresh_calibration_status(self):
        if self.engine.has_calibration():
            self.calibration_value.setText(Path(self.engine.calibration_file).name)
            self.calibration_preflight_value.setText("Ready")
            if self.engine.bed_points_mm is not None and len(self.engine.bed_points_mm) >= 3:
                width_mm = float(self.engine.bed_points_mm[1][0] - self.engine.bed_points_mm[0][0])
                height_mm = float(self.engine.bed_points_mm[2][1] - self.engine.bed_points_mm[1][1])
                self.bed_width_input.setValue(width_mm)
                self.bed_height_input.setValue(height_mm)
        else:
            self.calibration_value.setText("Not calibrated")
            self.calibration_preflight_value.setText("Missing")

    def update_baseline_status(self):
        has_baseline = self.engine.has_baseline()
        self.baseline_value.setText("Captured" if has_baseline else "Not captured")
        self.baseline_preflight_value.setText("Ready" if has_baseline else "Missing")

    def pending_push_dir(self):
        return Path(self.engine.runtime_dir) / "pending_pushes"

    def list_pending_push_files(self):
        pending_dir = self.pending_push_dir()
        if not pending_dir.exists():
            return []
        return sorted(pending_dir.glob("*_pending_push.json"))

    def enqueue_pending_push(self, bundle):
        pending_dir = self.pending_push_dir()
        pending_dir.mkdir(parents=True, exist_ok=True)
        pending_path = pending_dir / f"{self.engine.timestamp_id()}_pending_push.json"
        with pending_path.open("w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2)
        self.log(f"Queued pending push: {pending_path}")
        return pending_path

    def set_saving_state(self, busy):
        self.is_saving = busy
        if busy:
            self.status_label.setText("Saving...")

        if self.engine.pipeline is None:
            self.start_button.setEnabled(not busy)
            self.stop_button.setEnabled(False)
        else:
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(not busy)

        if self.engine.latest_view is not None:
            has_detection = self.engine.latest_view.has_detection
            self.save_button.setEnabled((not busy) and has_detection)
            self.push_now_button.setEnabled((not busy) and has_detection)
        else:
            self.save_button.setEnabled(False)
            self.push_now_button.setEnabled(False)

        self.retry_pending_pushes_button.setEnabled((not busy) and len(self.list_pending_push_files()) > 0)

    def refresh_material_options(self):
        current_value = self.material_input.currentText().strip() or self.saved_material_name
        try:
            materials = fetch_texture_library_materials(DEFAULT_PUSH_URL)
        except Exception as exc:
            self.material_input.clear()
            self.material_input.addItem("Unable to load materials")
            self.material_input.setEnabled(False)
            self.export_status_value.setText("Material list unavailable")
            self.materials_preflight_value.setText("Unavailable")
            self.log(f"Material list refresh failed: {exc}")
            return

        self.material_input.clear()
        for material in materials:
            self.material_input.addItem(material)

        self.material_input.setEnabled(True)
        if current_value and current_value in materials:
            self.material_input.setCurrentText(current_value)
        elif self.saved_material_name and self.saved_material_name in materials:
            self.material_input.setCurrentText(self.saved_material_name)
        elif materials:
            self.material_input.setCurrentIndex(0)
        self.materials_preflight_value.setText(f"Ready ({len(materials)})")
        self.log(f"Loaded {len(materials)} materials from texture_library.")

    def on_zoom_changed(self, value):
        self.zoom_factor = float(value) / 100.0
        self.zoom_label.setText(f"{value}%")
        active_view = self.frozen_view if self.freeze_active else self.engine.latest_view
        if active_view is not None:
            self.set_preview_image(active_view.preview_image)

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
        self.retry_pending_pushes_button.setEnabled(len(self.list_pending_push_files()) > 0)
        self.capture_baseline_button.setEnabled(self.engine.has_calibration())
        self.start_calibration_button.setEnabled(True)
        self.reset_calibration_points_button.setEnabled(False)
        self.save_calibration_button.setEnabled(False)
        self.freeze_button.setEnabled(True)
        self.refresh_calibration_status()
        self.update_baseline_status()
        if self.engine.stream_width and self.engine.stream_height and self.engine.stream_fps:
            self.camera_preflight_value.setText(f"Running ({self.engine.stream_width}x{self.engine.stream_height}@{self.engine.stream_fps})")
        else:
            self.camera_preflight_value.setText("Running")
        if self.engine.has_calibration():
            self.status_label.setText("Camera started.")
            self.log(f"Camera started at {self.camera_preflight_value.text()}.")
        else:
            self.status_label.setText("Camera started. No saved calibration found.")
            self.log(f"Camera started at {self.camera_preflight_value.text()}. No saved calibration found; use in-app calibration.")

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
        self.retry_pending_pushes_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.push_now_button.setEnabled(False)
        self.status_label.setText("Camera stopped.")
        self.camera_preflight_value.setText("Stopped")
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
        self.push_now_button.setEnabled(view.has_detection and not self.is_saving)
        if self.is_saving:
            self.save_button.setEnabled(False)

    def current_export_metadata(self):
        return {
            "material": self.material_input.currentText().strip(),
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
        self.saved_material_name = self.material_input.currentText().strip()
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
        self.set_saving_state(True)

        try:
            saved = self.engine.save_scan_result(scan_result, workshop_bundle=bundle)
        except Exception as exc:
            self.set_saving_state(False)
            QMessageBox.critical(self, "Save Error", str(exc))
            self.log(f"Save failed: {exc}")
            return

        push_message = "Sheet push skipped."
        if should_push:
            try:
                response = post_workshop_bundle(metadata["push_url"], bundle)
            except Exception as exc:
                pending_path = self.enqueue_pending_push(bundle)
                self.sheets_preflight_value.setText("Failed (queued)")
                self.last_push_value.setText(f"Failed and queued: {pending_path.name}")
                self.retry_pending_pushes_button.setEnabled(True)
                self.set_saving_state(False)
                QMessageBox.critical(self, "Sheet Push Error", str(exc))
                self.log(f"Sheet push failed: {exc}")
                return

            response_body = response["body"]
            push_message = f"Sheet push OK (HTTP {response['status_code']})."
            self.sheets_preflight_value.setText("OK")
            if isinstance(response_body, dict):
                spreadsheet_name = response_body.get("spreadsheet_name")
                inventory_rows = response_body.get("inventory_rows_written")
                inventory_merged = response_body.get("inventory_rows_merged")
                shape_rows = response_body.get("shape_rows_written")
                event_rows = response_body.get("event_rows_written")
                preview_rows = response_body.get("preview_rows_written")
                counts = (
                    f"inventory_new={inventory_rows}, inventory_merged={inventory_merged}, shapes={shape_rows}, "
                    f"events={event_rows}, previews={preview_rows}"
                )
                if spreadsheet_name:
                    push_message = f"Sheet push OK to '{spreadsheet_name}' (HTTP {response['status_code']}; {counts})."
                else:
                    push_message = f"Sheet push OK (HTTP {response['status_code']}; {counts})."
            self.log(f"Sheet push response: {response_body}")
            self.last_push_value.setText(push_message)

        self.log(f"Saved preview: {saved['image_path']}")
        self.log(f"Saved mask: {saved['mask_path']}")
        self.log(f"Saved json: {saved['json_path']}")
        self.log(f"Saved dxf: {saved['dxf_path']}")
        if saved["workshop_json_path"]:
            self.log(f"Saved Workshop Hub bundle: {saved['workshop_json_path']}")
        QMessageBox.information(
            self,
            "Scan Saved",
            (
                f"Preview: {saved['image_path']}\n"
                f"Mask: {saved['mask_path']}\n"
                f"Scan JSON: {saved['json_path']}\n"
                f"DXF: {saved['dxf_path']}\n"
                f"Workshop Bundle: {saved['workshop_json_path']}\n"
                f"{push_message}"
            ),
        )
        self.retry_pending_pushes_button.setEnabled(len(self.list_pending_push_files()) > 0)
        self.set_saving_state(False)

    def save_and_push_scan(self):
        self.push_on_save_checkbox.setChecked(True)
        self.save_scan(force_push=True)

    def retry_pending_pushes(self):
        pending_files = self.list_pending_push_files()
        if not pending_files:
            QMessageBox.information(self, "Retry Pending Pushes", "There are no queued pushes.")
            return

        self.set_saving_state(True)
        pushed = 0
        failed = 0
        last_error = ""

        for pending_file in pending_files:
            try:
                with pending_file.open("r", encoding="utf-8") as f:
                    bundle = json.load(f)
                response = post_workshop_bundle(DEFAULT_PUSH_URL, bundle)
                self.log(f"Retried {pending_file.name}: HTTP {response['status_code']}")
                pending_file.unlink()
                pushed += 1
            except Exception as exc:
                failed += 1
                last_error = str(exc)
                self.log(f"Retry failed for {pending_file.name}: {exc}")

        self.set_saving_state(False)
        self.retry_pending_pushes_button.setEnabled(len(self.list_pending_push_files()) > 0)

        if failed == 0:
            self.sheets_preflight_value.setText("OK")
            self.last_push_value.setText(f"Retried queued pushes: {pushed} succeeded.")
            QMessageBox.information(self, "Retry Pending Pushes", f"Successfully pushed {pushed} queued bundles.")
        else:
            self.sheets_preflight_value.setText("Failed (queued)")
            self.last_push_value.setText(f"Retry result: {pushed} succeeded, {failed} failed.")
            QMessageBox.warning(
                self,
                "Retry Pending Pushes",
                f"Retried queued bundles.\nSucceeded: {pushed}\nFailed: {failed}\nLast error: {last_error}",
            )
