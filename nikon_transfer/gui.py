"""Qt (PySide6) GUI for nikon_transfer.

Lets the user browse thumbnails on the D5300 and pick which photos to import.
The PTP/IP session lives in a background QThread so the UI stays responsive.
"""

import logging
import sys
import threading
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import ExifTags, Image
from PIL.Image import Image as PILImage
from PySide6.QtCore import Qt, QObject, QSettings, QThread, QTimer, QUrl, Signal, Slot, QSize
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem,
    QFileDialog, QProgressBar, QSplitter, QStatusBar, QTextEdit, QMessageBox,
    QCheckBox, QComboBox, QDialog, QMenu, QTabWidget,
)

from .client import PtpIpClient
from .log import setup as setup_logging
from .protocol import PTPIP_HOST_DEFAULT, IMAGE_EXTENSIONS
from .utils import format_size, format_storage_size

log = logging.getLogger("nikon_transfer.gui")


_METERING = {0: "Inconnu", 1: "Moyenne", 2: "Pondérée centrale",
             3: "Spot", 4: "Multi-spot", 5: "Matricielle", 6: "Partielle"}
_PROGRAM  = {0: "Indéfini", 1: "Manuel", 2: "Programme normal", 3: "Priorité ouverture",
             4: "Priorité vitesse", 5: "Création", 6: "Action", 7: "Portrait", 8: "Paysage"}
_ORIENT   = {1: "Normale", 3: "180°", 6: "90° CW", 8: "90° CCW"}


def _fmt_exif_date(value) -> str:
    """EXIF dates are 'YYYY:MM:DD HH:MM:SS' — show 'YYYY-MM-DD HH:MM'."""
    s = str(value)
    try:
        dt = datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


def _fmt_shutter(value) -> str:
    """Format an EXIF rational exposure time as e.g. '1/250 s' or '2.5 s'."""
    try:
        num, den = value.numerator, value.denominator
    except AttributeError:
        f = float(value)
        return f"{f:.2f} s" if f >= 1 else f"1/{1/f:.0f} s"
    if den == 0:
        return "?"
    if num == 0:
        return "0 s"
    if den > num:
        return f"{num}/{den} s" if num != 1 else f"1/{den} s"
    return f"{num/den:.2f} s"


_EXIF_FIELDS = [
    # (EXIF tag name, label, formatter)
    ("Make",              "Marque",      str),
    ("Model",             "Modèle",      str),
    ("LensModel",         "Objectif",    str),
    ("DateTimeOriginal",  "Prise de vue", _fmt_exif_date),
    ("ExposureTime",      "Exposition",  lambda v: _fmt_shutter(v)),
    ("FNumber",           "Ouverture",   lambda v: f"f/{float(v):.1f}"),
    ("ISOSpeedRatings",   "ISO",         lambda v: str(v[0] if isinstance(v, tuple) else v)),
    ("FocalLength",       "Focale",      lambda v: f"{float(v):.0f} mm"),
    ("FocalLengthIn35mmFilm", "Focale 24×36", lambda v: f"{int(v)} mm"),
    ("ExposureBiasValue", "Correction",  lambda v: f"{float(v):+.1f} EV"),
    ("MeteringMode",      "Mesure",      lambda v: _METERING.get(int(v), str(v))),
    ("ExposureProgram",   "Mode",        lambda v: _PROGRAM.get(int(v), str(v))),
    ("WhiteBalance",      "Bal. blancs", lambda v: "Auto" if int(v) == 0 else "Manuel"),
    ("Flash",             "Flash",       lambda v: "Déclenché" if int(v) & 1 else "Non déclenché"),
    ("Orientation",       "Orientation", lambda v: _ORIENT.get(int(v), str(v))),
]


def _format_exif(file_head: bytes, filename: str, size: int) -> str:
    """Read EXIF from the head of the file (first ~128KB) and format it."""
    header = f"{filename}\n{format_size(size)}\n" + "─" * 32 + "\n"
    if not file_head:
        return header + "(données indisponibles)"
    try:
        img: PILImage = Image.open(BytesIO(file_head))
        exif = img.getexif()
    except Exception as e:
        return header + f"(EXIF illisible : {e})"
    if not exif:
        return header + "(aucune métadonnée EXIF trouvée)"

    merged = dict(exif)
    for sub_tag in (0x8769, 0xA005):    # Exif IFD, Interop IFD
        try:
            merged.update(exif.get_ifd(sub_tag))
        except Exception:
            pass
    by_name = {ExifTags.TAGS.get(k, hex(k)): v for k, v in merged.items()}

    label_width = max(len(label) for _, label, _ in _EXIF_FIELDS)
    lines = [header.rstrip(), ""]
    for tag, label, fmt in _EXIF_FIELDS:
        if tag not in by_name:
            continue
        try:
            val = fmt(by_name[tag])
        except Exception:
            val = str(by_name[tag])
        if val:
            lines.append(f"{label:<{label_width}} : {val}")
    if len(lines) == 2:
        lines.append("(aucun tag EXIF reconnu)")
    return "\n".join(lines)


class PhotoItem(QListWidgetItem):
    """List item carrying photo metadata and a swappable sort key."""

    SORT_CAMERA = "Ordre caméra"
    SORT_NAME   = "Nom"
    SORT_DATE   = "Date de prise de vue"
    SORT_SIZE   = "Taille"

    def __init__(self, text: str, handle: int, filename: str,
                 size: int, capture_dt: datetime | None, index: int) -> None:
        super().__init__(text)
        self.handle      = handle
        self.filename    = filename
        self.size        = size
        self.capture_dt  = capture_dt
        self.index       = index
        # Cache slots — None = not requested, "loading" = in flight, bytes = ready.
        self.exif_head: bytes | str | None = None
        self.full_data: bytes | str | None = None
        # Decoded thumbnail, kept for the inline preview pane (so we don't
        # round-trip via the icon's pixmap cache).
        self.thumb_pixmap: QPixmap | None = None
        self._sort_key: tuple = (index,)

    def set_sort_key(self, criterion: str) -> None:
        if criterion == self.SORT_NAME:
            self._sort_key = (self.filename.lower(),)
        elif criterion == self.SORT_DATE:
            # Recent first; missing dates last.
            dt = self.capture_dt or datetime.min
            self._sort_key = (-dt.timestamp() if self.capture_dt else float("inf"),
                              self.filename.lower())
        elif criterion == self.SORT_SIZE:
            self._sort_key = (-self.size, self.filename.lower())
        else:
            self._sort_key = (self.index,)

    def __lt__(self, other: "PhotoItem") -> bool:
        return self._sort_key < other._sort_key


class _ImageLabel(QLabel):
    """QLabel that rescales its pixmap to fit on resize, keeping aspect ratio."""

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(280, 200)
        self._source: QPixmap | None = None

    def setSourcePixmap(self, pix: QPixmap) -> None:
        self._source = pix
        self._rescale()

    def clear(self) -> None:
        self._source = None
        super().clear()

    def resizeEvent(self, event) -> None:
        self._rescale()
        super().resizeEvent(event)

    def _rescale(self) -> None:
        if self._source is None or self._source.isNull():
            return
        super().setPixmap(self._source.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))


class _PreviewPane(QWidget):
    """Inline, always-visible preview. Shows the upscaled thumb instantly when
    an item is selected; upgrades to full-resolution if the bytes are cached
    (i.e. the modal preview was opened at some point for this item)."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.image_label = _ImageLabel()
        layout.addWidget(self.image_label, 1)
        self.hint = QLabel("")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.hint)

    def show_item(self, item: "PhotoItem") -> None:
        # Prefer full-resolution bytes if we already have them (cached after
        # a modal preview), otherwise fall back to the thumbnail.
        if isinstance(item.full_data, (bytes, bytearray)) and item.full_data:
            pix = QPixmap()
            if pix.loadFromData(item.full_data):
                self.image_label.setSourcePixmap(pix)
                self.hint.setText(
                    f"{pix.width()}×{pix.height()} · pleine résolution"
                )
                return
        if item.thumb_pixmap is not None:
            self.image_label.setSourcePixmap(item.thumb_pixmap)
            self.hint.setText(
                "Aperçu basse résolution — double-clic sur la miniature pour la pleine résolution"
            )
            return
        self.image_label.clear()
        self.hint.setText("Aucun aperçu disponible")

    def clear(self) -> None:
        self.image_label.clear()
        self.hint.setText("")


class PreviewDialog(QDialog):
    """Modal dialog showing the full-resolution image. Fetched on demand."""

    def __init__(self, parent: QWidget, item: PhotoItem) -> None:
        super().__init__(parent)
        self.item = item
        self.setWindowTitle(item.filename)
        self.resize(1000, 750)

        layout = QVBoxLayout(self)
        self.image_label = _ImageLabel()
        layout.addWidget(self.image_label, 1)
        self.status = QLabel("Téléchargement de l'aperçu …")
        self.status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status)

        # If already cached, display immediately.
        if isinstance(item.full_data, (bytes, bytearray)) and item.full_data:
            self.set_image(item.full_data)

    def set_image(self, data: bytes) -> None:
        pix = QPixmap()
        if not pix.loadFromData(data):
            self.status.setText("Impossible de décoder l'image (NEF non supporté nativement ?)")
            return
        self.image_label.setSourcePixmap(pix)
        self.status.setText(f"{pix.width()}×{pix.height()} · {format_size(len(data))}")

    def set_error(self, msg: str) -> None:
        self.status.setText(f"Erreur : {msg}")


class CameraWorker(QObject):
    """Owns the PtpIpClient. All PTP calls run in this object's thread."""

    connected         = Signal()
    device_info       = Signal(dict)                   # full PTP DeviceInfo dict
    camera_datetime   = Signal(object)                 # datetime | None
    photo_found       = Signal(int, str, int, object, bytes)  # handle, name, size, capture_dt, thumb
    listing_done      = Signal(int)                    # total photos
    storage_info      = Signal(int, int, str)          # free_bytes, free_objects, volume label
    battery_loaded    = Signal(object)                 # int 0–100 or None if unsupported
    exif_loaded       = Signal(int, bytes)             # handle, file_head bytes
    full_loaded       = Signal(int, bytes)             # handle, full file bytes
    download_progress = Signal(str, int, int)          # filename, idx, total
    download_done     = Signal(dict)                   # {transferred, errors, bytes, cancelled}
    error             = Signal(str)
    disconnected      = Signal()
    # Same as `disconnected` but signals that the cause was a network failure
    # that the GUI can/should retry automatically before bothering the user.
    connection_lost   = Signal(str)

    EXIF_HEAD_BYTES = 128 * 1024  # enough for JPEG APP1 and NEF main IFD
    POLL_INTERVAL_MS = 3000       # how often we poll for new handles

    def __init__(self) -> None:
        super().__init__()
        self.client: PtpIpClient | None = None
        self._cancel = threading.Event()
        self._known_handles: set[int] = set()
        self._image_extensions: set[str] = set()
        self._downloading = False
        # Created here so it travels with self in moveToThread(); start/stop is
        # always done from slots running in the worker thread.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_events)

    def cancel(self) -> None:
        """Thread-safe — sets the cancel flag, read between download iterations."""
        self._cancel.set()

    @Slot(str, float)
    def connect_camera(self, host: str, timeout: float) -> None:
        try:
            self.client = PtpIpClient(host, timeout=timeout)
            self.client.connect()
        except Exception as e:
            self.client = None
            self.error.emit(f"Connexion : {e}")
            return
        # Identification + clock are nice-to-haves — failure must not abort connect.
        try:
            self.device_info.emit(self.client.get_device_info())
        except Exception as e:
            log.debug("GetDeviceInfo : %s", e)
        try:
            self.camera_datetime.emit(self.client.get_device_datetime())
        except Exception as e:
            log.debug("GetDeviceDateTime : %s", e)
        self.connected.emit()

    @Slot(list)
    def list_photos(self, extensions: list) -> None:
        if self.client is None:
            self.error.emit("Pas connecté à la caméra")
            return
        try:
            ext_set = {e.lower() for e in extensions}
            self._image_extensions = ext_set
            self._known_handles.clear()
            storage_ids = self.client.get_storage_ids()
            total_free = 0
            total_objects = 0
            labels: list[str] = []
            for sid in storage_ids:
                try:
                    sinfo = self.client.get_storage_info(sid)
                except Exception as e:
                    log.debug("get_storage_info(%#x) erreur : %s", sid, e)
                    continue
                total_free    += sinfo["free_bytes"]
                total_objects += sinfo["free_objects"]
                if sinfo["volume_label"]:
                    labels.append(sinfo["volume_label"])
            self.storage_info.emit(total_free, total_objects, " / ".join(labels))
            self._refresh_battery()
            handles: list[int] = []
            for sid in storage_ids:
                handles.extend(self.client.get_object_handles(sid))

            count = 0
            for h in handles:
                try:
                    info = self.client.get_object_info(h)
                except Exception:
                    continue
                # Remember every handle we've already seen — even non-image ones —
                # so the event poller doesn't try to re-announce them.
                self._known_handles.add(h)
                if Path(info["filename"]).suffix.lower() not in ext_set:
                    continue
                try:
                    thumb = self.client.get_thumb(h)
                except Exception:
                    thumb = b""
                self.photo_found.emit(
                    h, info["filename"], info["size"], info.get("capture_date"), thumb,
                )
                count += 1
            self.listing_done.emit(count)
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        except Exception as e:
            self.error.emit(f"Listing : {e}")

    @Slot()
    def _poll_events(self) -> None:
        """Poll storage for new handles. The D5300 ignores the PTP/IP event
        channel (closes evt_sock right after handshake), so we diff the handle
        list on cmd_sock instead. Lightweight: GetObjectHandles returns a flat
        array of uint32s — even with hundreds of photos it's a few KB."""
        if self.client is None or self._downloading:
            return
        try:
            current: set[int] = set()
            for sid in self.client.get_storage_ids():
                current.update(self.client.get_object_handles(sid))
        except self._DEAD_SOCKET_ERRORS as e:
            self._mark_disconnected("Polling", e)
            return
        except Exception as e:
            log.warning("Polling handles : %s", e)
            return
        # Refresh battery on each tick (single UINT8, ~negligible bandwidth).
        self._refresh_battery()
        new_handles = current - self._known_handles
        if not new_handles:
            return
        # Sort ascending so the newest file (highest handle on Nikon) appears last.
        for h in sorted(new_handles):
            try:
                info = self.client.get_object_info(h)
            except Exception as e:
                log.debug("Nouvel objet %#x : get_object_info erreur %s", h, e)
                continue
            self._known_handles.add(h)
            if Path(info["filename"]).suffix.lower() not in self._image_extensions:
                continue
            try:
                thumb = self.client.get_thumb(h)
            except Exception:
                thumb = b""
            self.photo_found.emit(
                h, info["filename"], info["size"], info.get("capture_date"), thumb,
            )

    @Slot(list, str, bool)
    def download(self, items: list, dest_str: str, date_subdir: bool) -> None:
        if self.client is None:
            self.error.emit("Pas connecté à la caméra")
            return
        self._cancel.clear()
        # Pause event polling: cmd_sock is about to be busy with multi-megabyte
        # downloads; we don't want _poll_events firing get_object_info between
        # download iterations and racing on the cmd channel.
        self._downloading = True
        base = Path(dest_str).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        stats = {"transferred": 0, "errors": 0, "bytes": 0, "cancelled": False}
        total = len(items)
        try:
            for idx, (handle, filename, _size, capture_dt) in enumerate(items, 1):
                if self._cancel.is_set():
                    stats["cancelled"] = True
                    break
                dest_dir = base
                if date_subdir and isinstance(capture_dt, datetime):
                    dest_dir = base / capture_dt.strftime("%Y-%m-%d")
                    dest_dir.mkdir(parents=True, exist_ok=True)
                self.download_progress.emit(filename, idx, total)
                try:
                    data = self.client.get_object(handle)
                    (dest_dir / filename).write_bytes(data)
                    stats["transferred"] += 1
                    stats["bytes"]       += len(data)
                except self._DEAD_SOCKET_ERRORS as e:
                    stats["errors"] += 1
                    self._mark_disconnected(f"Téléchargement {filename}", e)
                    break
                except Exception as e:
                    stats["errors"] += 1
                    self.error.emit(f"{filename} : {e}")
        finally:
            self._downloading = False
        self.download_done.emit(stats)

    # Socket-level failures that mean the PTP session is dead and can't be reused.
    _DEAD_SOCKET_ERRORS = (
        BrokenPipeError, ConnectionResetError, ConnectionAbortedError, EOFError,
    )

    def _refresh_battery(self) -> None:
        """Read BatteryLevel and emit it. Tolerant: failure → emit None."""
        if self.client is None:
            return
        try:
            level = self.client.get_battery_level()
        except self._DEAD_SOCKET_ERRORS as e:
            self._mark_disconnected("Batterie", e)
            return
        except Exception as e:
            log.debug("Batterie : %s", e)
            level = None
        self.battery_loaded.emit(level)

    def _mark_disconnected(self, context: str, err: Exception) -> None:
        """Tear down a dead PTP session and signal connection_lost so the GUI
        can attempt an automatic reconnect before bothering the user."""
        log.warning("%s : socket cassée (%s) — session PTP fermée", context, err)
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.connection_lost.emit(f"{context} : {err}")

    @Slot(int)
    def fetch_full(self, handle: int) -> None:
        if self.client is None:
            self.full_loaded.emit(handle, b"")
            return
        try:
            data = self.client.get_object(handle)
        except self._DEAD_SOCKET_ERRORS as e:
            self._mark_disconnected("Aperçu", e)
            self.full_loaded.emit(handle, b"")
            return
        except Exception as e:
            self.error.emit(f"Aperçu : {e}")
            data = b""
        self.full_loaded.emit(handle, data)

    @Slot(int)
    def fetch_exif(self, handle: int) -> None:
        if self.client is None:
            self.exif_loaded.emit(handle, b"")
            return
        try:
            head = self.client.get_partial_object(handle, 0, self.EXIF_HEAD_BYTES)
        except self._DEAD_SOCKET_ERRORS as e:
            self._mark_disconnected("EXIF", e)
            self.exif_loaded.emit(handle, b"")
            return
        except Exception:
            head = b""
        self.exif_loaded.emit(handle, head)

    @Slot()
    def disconnect_camera(self) -> None:
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        self._known_handles.clear()
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.disconnected.emit()


class MainWindow(QMainWindow):
    request_connect    = Signal(str, float)
    request_list       = Signal(list)
    request_exif       = Signal(int)
    request_full       = Signal(int)
    request_download   = Signal(list, str, bool)
    request_disconnect = Signal()

    _BASE_TITLE = "Nikon D5300 — Transfert"
    _CLOCK_DRIFT_WARN_SECONDS = 5 * 60   # > 5 min → warn user once per session
    _MAX_RECONNECT_ATTEMPTS = 3
    _RECONNECT_BACKOFF_MS   = [2000, 4000, 8000]   # exponential between tries

    # Stylesheets shared between widgets. Kept here so the structure of
    # _build_ui stays readable.
    _PRIMARY_BTN_CSS = """
        QPushButton {
            background-color: palette(highlight);
            color: palette(highlighted-text);
            border: 1px solid palette(highlight);
            border-radius: 6px;
            padding: 6px 14px;
            font-weight: 600;
        }
        QPushButton:hover    { background-color: palette(highlight); }
        QPushButton:disabled {
            background-color: palette(mid);
            color: palette(window);
            border-color: palette(mid);
        }
    """
    _STATUS_STRIP_CSS = """
        #statusStrip {
            background-color: palette(alternate-base);
            border: 1px solid palette(mid);
            border-radius: 6px;
        }
        #statusStrip QLabel { color: palette(text); font-weight: 500; }
        #stateDot { font-size: 14pt; }
    """
    _LIST_CSS = "QListWidget { color: palette(text); }"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self._BASE_TITLE)
        self.resize(1020, 760)
        self._clock_warned = False
        self._reconnect_attempt = 0
        self._reconnect_pending = False
        self._user_initiated_disconnect = False
        self._was_connected = False

        self._thread = QThread(self)
        self._worker = CameraWorker()
        self._worker.moveToThread(self._thread)
        self._thread.start()

        self._worker.connected.connect(self._on_connected)
        self._worker.device_info.connect(self._on_device_info)
        self._worker.camera_datetime.connect(self._on_camera_datetime)
        self._worker.photo_found.connect(self._on_photo_found)
        self._worker.listing_done.connect(self._on_listing_done)
        self._worker.storage_info.connect(self._on_storage_info)
        self._worker.battery_loaded.connect(self._on_battery_loaded)
        self._worker.exif_loaded.connect(self._on_exif_loaded)
        self._worker.full_loaded.connect(self._on_full_loaded)
        self._worker.download_progress.connect(self._on_download_progress)
        self._worker.download_done.connect(self._on_download_done)
        self._worker.error.connect(self._on_error)
        self._worker.disconnected.connect(self._on_disconnected)
        self._worker.connection_lost.connect(self._on_connection_lost)

        self.request_connect.connect(self._worker.connect_camera)
        self.request_list.connect(self._worker.list_photos)
        self.request_exif.connect(self._worker.fetch_exif)
        self.request_full.connect(self._worker.fetch_full)
        self.request_download.connect(self._worker.download)
        self.request_disconnect.connect(self._worker.disconnect_camera)

        self._photos_total = 0
        self._items_by_handle: dict[int, PhotoItem] = {}
        self._next_index = 0
        self._preview: PreviewDialog | None = None
        self._downloaded_names: set[str] = set()
        self._settings = QSettings("nikon-transfer", "nikon-transfer")
        self._build_ui()
        self._load_settings()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)

        layout.addLayout(self._build_header_rows())
        layout.addWidget(self._build_status_strip())
        layout.addLayout(self._build_options_row())
        layout.addWidget(self._build_splitter(), 1)
        layout.addLayout(self._build_action_bar())

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Prêt")

        self._install_shortcuts()

    def _build_header_rows(self) -> QVBoxLayout:
        """Two rows: camera connection + destination folder.

        Splitting the original cramped single row into two gives each input
        breathing room and lets the primary 'Connecter' button stand out.
        """
        rows = QVBoxLayout()
        rows.setSpacing(6)

        # Row 1 — camera + connect.
        cam_row = QHBoxLayout()
        cam_row.addWidget(QLabel("Caméra :"))
        self.host_input = QLineEdit(PTPIP_HOST_DEFAULT)
        self.host_input.setFixedWidth(160)
        cam_row.addWidget(self.host_input)
        cam_row.addStretch(1)
        self.connect_btn = QPushButton("Connecter")
        self.connect_btn.setDefault(True)
        self.connect_btn.setAutoDefault(True)
        self.connect_btn.setStyleSheet(self._PRIMARY_BTN_CSS)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        cam_row.addWidget(self.connect_btn)
        rows.addLayout(cam_row)

        # Row 2 — destination + browse + open.
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Destination :"))
        self.dest_input = QLineEdit(str(Path.home() / "Pictures" / "Nikon_D5300"))
        self.dest_input.editingFinished.connect(self._apply_filter)
        dest_row.addWidget(self.dest_input, 1)
        self.browse_btn = QPushButton("Parcourir…")
        self.browse_btn.setToolTip("Choisir le dossier de destination")
        self.browse_btn.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.browse_btn)
        self.open_dest_btn = QPushButton("Voir dossier")
        self.open_dest_btn.setToolTip("Ouvrir le dossier de destination dans le Finder")
        self.open_dest_btn.clicked.connect(self._open_dest_folder)
        dest_row.addWidget(self.open_dest_btn)
        rows.addLayout(dest_row)

        return rows

    def _build_status_strip(self) -> QWidget:
        """High-contrast strip under the header showing connection state,
        storage, and battery — promoted from the bottom status bar so the
        user can see at a glance whether the camera is alive."""
        strip = QWidget()
        strip.setObjectName("statusStrip")
        strip.setStyleSheet(self._STATUS_STRIP_CSS)
        h = QHBoxLayout(strip)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(10)

        self.state_dot = QLabel("●")
        self.state_dot.setObjectName("stateDot")
        h.addWidget(self.state_dot)
        self.state_label = QLabel("Déconnecté")
        h.addWidget(self.state_label)
        h.addStretch(1)
        self.storage_label = QLabel("")
        h.addWidget(self.storage_label)
        self._strip_sep = QLabel("·")
        self._strip_sep.setVisible(False)
        h.addWidget(self._strip_sep)
        self.battery_label = QLabel("")
        h.addWidget(self.battery_label)

        self._set_state("disconnected", "Déconnecté")
        return strip

    def _build_options_row(self) -> QHBoxLayout:
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Trier par :"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            PhotoItem.SORT_CAMERA, PhotoItem.SORT_NAME,
            PhotoItem.SORT_DATE,   PhotoItem.SORT_SIZE,
        ])
        self.sort_combo.currentTextChanged.connect(self._resort)
        opts.addWidget(self.sort_combo)
        opts.addSpacing(16)
        self.date_subdir_cb = QCheckBox("Ranger par date (YYYY-MM-DD)")
        opts.addWidget(self.date_subdir_cb)
        opts.addSpacing(16)
        self.hide_downloaded_cb = QCheckBox("Masquer les déjà téléchargées")
        self.hide_downloaded_cb.setToolTip(
            "Cache les photos dont le nom de fichier existe déjà dans le dossier "
            "de destination (récursivement)."
        )
        self.hide_downloaded_cb.stateChanged.connect(lambda _: self._apply_filter())
        opts.addWidget(self.hide_downloaded_cb)
        opts.addStretch(1)
        self.toggle_panel_btn = QPushButton("◀ Aperçu")
        self.toggle_panel_btn.setCheckable(True)
        self.toggle_panel_btn.setChecked(True)
        self.toggle_panel_btn.setToolTip("Afficher / masquer le panneau aperçu + EXIF")
        self.toggle_panel_btn.toggled.connect(self._on_toggle_panel)
        opts.addWidget(self.toggle_panel_btn)

        # Filename filter (live).
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filtrer par nom de fichier…  (⌘F)")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(lambda _: self._apply_filter())
        opts.addSpacing(16)
        opts.addWidget(self.filter_input, 2)
        return opts

    def _build_splitter(self) -> QSplitter:
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.IconMode)
        self.list_widget.setIconSize(QSize(180, 120))
        self.list_widget.setResizeMode(QListWidget.Adjust)
        self.list_widget.setMovement(QListWidget.Static)
        self.list_widget.setSpacing(8)
        # Default item text uses dimmed colors in some macOS dark themes —
        # pin it to the regular text palette role so filenames stay legible.
        self.list_widget.setStyleSheet(self._LIST_CSS)
        self.list_widget.itemDoubleClicked.connect(self._open_preview)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        self.list_widget.currentItemChanged.connect(self._on_current_item_changed)

        self.exif_panel = QTextEdit()
        self.exif_panel.setReadOnly(True)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(11)
        self.exif_panel.setFont(mono)
        self.exif_panel.setMinimumWidth(280)
        self.exif_panel.setPlaceholderText(
            "Cliquez sur une miniature pour afficher ses informations EXIF."
        )

        self.preview_pane = _PreviewPane()

        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self.preview_pane, "Aperçu")
        self.right_tabs.addTab(self.exif_panel, "EXIF")

        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.addWidget(self.list_widget)
        self._splitter.addWidget(self.right_tabs)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        return self._splitter

    def _build_action_bar(self) -> QHBoxLayout:
        bottom = QHBoxLayout()
        self.all_btn = QPushButton("Tout cocher")
        self.all_btn.clicked.connect(lambda: self._set_all_checked(True))
        bottom.addWidget(self.all_btn)
        self.none_btn = QPushButton("Tout décocher")
        self.none_btn.clicked.connect(lambda: self._set_all_checked(False))
        bottom.addWidget(self.none_btn)
        self.select_menu_btn = QPushButton("Sélection ▾")
        select_menu = QMenu(self.select_menu_btn)
        act_nef = QAction("Toutes les NEF (RAW)", self)
        act_nef.triggered.connect(lambda: self._select_by_extensions({".nef", ".nrw"}))
        select_menu.addAction(act_nef)
        act_jpg = QAction("Toutes les JPEG", self)
        act_jpg.triggered.connect(lambda: self._select_by_extensions({".jpg", ".jpeg"}))
        select_menu.addAction(act_jpg)
        act_today = QAction("Photos d'aujourd'hui", self)
        act_today.triggered.connect(self._select_today)
        select_menu.addAction(act_today)
        select_menu.addSeparator()
        act_invert = QAction("Inverser la sélection", self)
        act_invert.triggered.connect(self._invert_selection)
        select_menu.addAction(act_invert)
        self.select_menu_btn.setMenu(select_menu)
        bottom.addWidget(self.select_menu_btn)
        self.selection_label = QLabel("0 photo cochée")
        self.selection_label.setStyleSheet("color: palette(text);")
        bottom.addSpacing(12)
        bottom.addWidget(self.selection_label)
        bottom.addStretch(1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        bottom.addWidget(self.progress, 2)
        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        bottom.addWidget(self.cancel_btn)
        self.download_btn = QPushButton("Télécharger la sélection")
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet(self._PRIMARY_BTN_CSS)
        self.download_btn.clicked.connect(self._on_download_clicked)
        bottom.addWidget(self.download_btn)
        return bottom

    def _set_state(self, state: str, text: str) -> None:
        """Update the colored dot + label in the status strip.

        state ∈ {connected, connecting, reconnecting, disconnected, error}."""
        colors = {
            "connected":    "#2ecc71",   # green
            "connecting":   "#f1c40f",   # amber
            "reconnecting": "#e67e22",   # orange
            "disconnected": "palette(mid)",
            "error":        "#e74c3c",   # red
        }
        color = colors.get(state, "palette(mid)")
        self.state_dot.setStyleSheet(f"color: {color}; font-size: 14pt;")
        self.state_label.setText(text)

    def _on_toggle_panel(self, checked: bool) -> None:
        self.right_tabs.setVisible(checked)
        self.toggle_panel_btn.setText("◀ Aperçu" if checked else "Aperçu ▶")
        self._settings.setValue("show_right_panel", checked)

    def _install_shortcuts(self) -> None:
        """Keyboard shortcuts. Space/Return only fire when the grid has focus
        so they don't interfere with typing in the filter or destination input."""
        sp = QShortcut(QKeySequence(Qt.Key_Space), self.list_widget)
        sp.setContext(Qt.WidgetShortcut)
        sp.activated.connect(self._toggle_current_check)
        for key in (Qt.Key_Return, Qt.Key_Enter):
            sh = QShortcut(QKeySequence(key), self.list_widget)
            sh.setContext(Qt.WidgetShortcut)
            sh.activated.connect(self._open_current_preview)
        sel_all  = QShortcut(QKeySequence.SelectAll, self)
        sel_all.activated.connect(lambda: self._set_all_checked(True))
        sel_none = QShortcut(QKeySequence("Ctrl+Shift+A"), self)
        sel_none.activated.connect(lambda: self._set_all_checked(False))
        find = QShortcut(QKeySequence.Find, self)
        find.activated.connect(self._focus_filter)
        dl   = QShortcut(QKeySequence("Ctrl+D"), self)
        dl.activated.connect(self._on_download_clicked)
        conn = QShortcut(QKeySequence("Ctrl+R"), self)
        conn.activated.connect(self._on_connect_clicked)
        panel = QShortcut(QKeySequence("Ctrl+P"), self)
        panel.activated.connect(lambda: self.toggle_panel_btn.toggle())

        # Surface every shortcut via tooltips so they're discoverable.
        nat = QKeySequence.NativeText
        sa  = QKeySequence(QKeySequence.SelectAll).toString(nat)
        sna = QKeySequence("Ctrl+Shift+A").toString(nat)
        self.all_btn.setToolTip(f"Tout cocher ({sa})")
        self.none_btn.setToolTip(f"Tout décocher ({sna})")
        self.download_btn.setToolTip(
            f"Télécharger la sélection ({QKeySequence('Ctrl+D').toString(nat)})"
        )
        self.connect_btn.setToolTip(
            f"Connecter à la caméra ({QKeySequence('Ctrl+R').toString(nat)})"
        )
        self.toggle_panel_btn.setToolTip(
            f"Afficher / masquer le panneau aperçu + EXIF "
            f"({QKeySequence('Ctrl+P').toString(nat)})"
        )

    # ── User actions ─────────────────────────────────────────────────────────

    def _browse_dest(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Dossier de destination", self.dest_input.text()
        )
        if d:
            self.dest_input.setText(d)
            self._apply_filter()

    def _open_dest_folder(self) -> None:
        dest = Path(self.dest_input.text().strip()).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(dest)))

    # ── Persistent settings ──────────────────────────────────────────────────

    def _load_settings(self) -> None:
        host = self._settings.value("host", PTPIP_HOST_DEFAULT, type=str)
        dest = self._settings.value(
            "dest", str(Path.home() / "Pictures" / "Nikon_D5300"), type=str,
        )
        hide_dl     = self._settings.value("hide_downloaded", False, type=bool)
        show_panel  = self._settings.value("show_right_panel", True, type=bool)
        self.host_input.setText(host)
        self.dest_input.setText(dest)
        self.hide_downloaded_cb.setChecked(hide_dl)
        self.toggle_panel_btn.setChecked(show_panel)
        # toggle() emits toggled(); when starting checked we still need to
        # apply the visibility manually since setChecked(True) on a button
        # that's already checked is a no-op.
        self._on_toggle_panel(show_panel)

    def _save_settings(self) -> None:
        self._settings.setValue("host", self.host_input.text().strip())
        self._settings.setValue("dest", self.dest_input.text().strip())
        self._settings.setValue("hide_downloaded", self.hide_downloaded_cb.isChecked())
        self._settings.setValue("show_right_panel", self.toggle_panel_btn.isChecked())

    # ── Filtering & selection ────────────────────────────────────────────────

    def _refresh_downloaded_set(self) -> None:
        """Walk the destination folder once and remember every existing filename."""
        self._downloaded_names = set()
        dest = Path(self.dest_input.text().strip()).expanduser()
        if not dest.exists():
            return
        try:
            for p in dest.rglob("*"):
                if p.is_file():
                    self._downloaded_names.add(p.name)
        except OSError as e:
            log.debug("Scan destination : %s", e)

    def _apply_filter_to_item(self, item: "PhotoItem") -> None:
        needle  = self.filter_input.text().strip().lower()
        hide_dl = self.hide_downloaded_cb.isChecked()
        match_name = (not needle) or (needle in item.filename.lower())
        not_downloaded = (not hide_dl) or (item.filename not in self._downloaded_names)
        item.setHidden(not (match_name and not_downloaded))

    def _apply_filter(self) -> None:
        if self.hide_downloaded_cb.isChecked():
            self._refresh_downloaded_set()
        else:
            self._downloaded_names = set()
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if isinstance(it, PhotoItem):
                self._apply_filter_to_item(it)

    def _select_by_extensions(self, exts: set[str]) -> None:
        """Cocher tous les items visibles dont l'extension est dans `exts`."""
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if (isinstance(it, PhotoItem) and not it.isHidden()
                    and Path(it.filename).suffix.lower() in exts):
                it.setCheckState(Qt.Checked)

    def _select_today(self) -> None:
        today = datetime.now().date()
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if (isinstance(it, PhotoItem) and not it.isHidden()
                    and it.capture_dt and it.capture_dt.date() == today):
                it.setCheckState(Qt.Checked)

    def _invert_selection(self) -> None:
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if isinstance(it, PhotoItem) and not it.isHidden():
                it.setCheckState(
                    Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked
                )

    def _on_connect_clicked(self) -> None:
        # User-initiated → reset the reconnect counter so the next disconnect
        # gets a fresh budget of retries.
        self._reconnect_attempt = 0
        self._reconnect_pending = False
        self._user_initiated_disconnect = False
        self._fresh_session_reset()
        self.statusBar().showMessage("Connexion …")
        self._set_state("connecting", "Connexion …")
        self.request_connect.emit(self.host_input.text().strip(), 10.0)

    def _fresh_session_reset(self) -> None:
        """Reset session-scoped state. Called on user connect (full wipe)
        and on automatic reconnect (so the grid is rebuilt cleanly)."""
        self.connect_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.list_widget.clear()
        self._items_by_handle.clear()
        self.exif_panel.clear()
        self._photos_total = 0
        self._next_index = 0
        # New session → allow the clock-drift warning to fire again if needed.
        self._clock_warned = False
        if self.hide_downloaded_cb.isChecked():
            self._refresh_downloaded_set()
        else:
            self._downloaded_names = set()
        self._update_selection_count()

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        # Operate on visible items only — if a filter hides items, the user
        # almost certainly doesn't want to check what they can't see.
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if not it.isHidden():
                it.setCheckState(state)

    def _toggle_current_check(self) -> None:
        it = self.list_widget.currentItem()
        if isinstance(it, PhotoItem):
            it.setCheckState(
                Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked
            )

    def _open_current_preview(self) -> None:
        it = self.list_widget.currentItem()
        if isinstance(it, PhotoItem):
            self._open_preview(it)

    def _focus_filter(self) -> None:
        self.filter_input.setFocus()
        self.filter_input.selectAll()

    def _resort(self, criterion: str) -> None:
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if isinstance(it, PhotoItem):
                it.set_sort_key(criterion)
        self.list_widget.sortItems()

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_selection_count()

    def _update_selection_count(self) -> None:
        n = 0
        total_size = 0
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.checkState() == Qt.Checked and isinstance(it, PhotoItem):
                n += 1
                total_size += it.size
        if n == 0:
            self.selection_label.setText("0 photo cochée")
        else:
            self.selection_label.setText(
                f"{n} photo{'s' if n > 1 else ''} cochée{'s' if n > 1 else ''} · {format_size(total_size)}"
            )

    def _on_download_clicked(self) -> None:
        items: list = []
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.checkState() == Qt.Checked and isinstance(it, PhotoItem):
                items.append((it.handle, it.filename, it.size, it.capture_dt))
        if not items:
            QMessageBox.information(self, "Sélection vide", "Aucune photo cochée.")
            return
        self.download_btn.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setMaximum(len(items))
        self.progress.setValue(0)
        self.request_download.emit(
            items, self.dest_input.text(), self.date_subdir_cb.isChecked(),
        )

    def _on_cancel_clicked(self) -> None:
        self._worker.cancel()
        self.cancel_btn.setEnabled(False)
        self.statusBar().showMessage("Annulation demandée …")

    def _open_preview(self, item: QListWidgetItem) -> None:
        if not isinstance(item, PhotoItem):
            return
        self._preview = PreviewDialog(self, item)
        if isinstance(item.full_data, (bytes, bytearray)) and item.full_data:
            pass  # PreviewDialog displays from cache in its __init__
        else:
            if item.full_data != "loading":
                item.full_data = "loading"
                self.request_full.emit(item.handle)
        self._preview.exec()
        self._preview = None

    # ── Worker callbacks ─────────────────────────────────────────────────────

    @Slot()
    def _on_connected(self) -> None:
        self._was_connected = True
        self._reconnect_attempt = 0
        self.statusBar().showMessage("Connecté — chargement des miniatures …")
        self._set_state("connected", "Connecté")
        self.request_list.emit(list(IMAGE_EXTENSIONS))

    @Slot(str)
    def _on_connection_lost(self, reason: str) -> None:
        """Socket died mid-session. Try silently a few times before giving up.

        Backoff 2/4/8 s — the camera typically takes a couple of seconds to
        accept a fresh PTP/IP handshake once the previous session is GC'd."""
        if self._user_initiated_disconnect:
            return
        if self._reconnect_attempt >= self._MAX_RECONNECT_ATTEMPTS:
            self._set_state("error", "Connexion perdue")
            self.statusBar().showMessage(f"Erreur : {reason}")
            self.connect_btn.setEnabled(True)
            self.download_btn.setEnabled(False)
            QMessageBox.warning(
                self, "Connexion perdue",
                f"{reason}\n\nLes tentatives de reconnexion automatique ont échoué.\n"
                f"Vérifie le Wi-Fi de la caméra puis clique sur Connecter.",
            )
            self._reconnect_attempt = 0
            return
        delay_ms = self._RECONNECT_BACKOFF_MS[
            min(self._reconnect_attempt, len(self._RECONNECT_BACKOFF_MS) - 1)
        ]
        self._reconnect_attempt += 1
        self._reconnect_pending = True
        msg = (f"Reconnexion {self._reconnect_attempt}/{self._MAX_RECONNECT_ATTEMPTS} "
               f"dans {delay_ms // 1000} s …")
        self._set_state("reconnecting", msg)
        self.statusBar().showMessage(msg)
        QTimer.singleShot(delay_ms, self._auto_reconnect)

    @Slot()
    def _auto_reconnect(self) -> None:
        if self._user_initiated_disconnect:
            self._reconnect_pending = False
            return
        self._reconnect_pending = False
        self._fresh_session_reset()
        self._set_state("connecting",
                        f"Reconnexion {self._reconnect_attempt}/"
                        f"{self._MAX_RECONNECT_ATTEMPTS} …")
        self.request_connect.emit(self.host_input.text().strip(), 10.0)

    @Slot(int, str, int, object, bytes)
    def _on_photo_found(self, handle: int, filename: str, size: int,
                        capture_dt, thumb: bytes) -> None:
        # After an auto-reconnect, the worker re-lists everything — skip handles
        # the grid already shows so we don't duplicate items.
        if handle in self._items_by_handle:
            return
        # Label: filename + date (if known) + size — date helps the user when sorted.
        if isinstance(capture_dt, datetime):
            label = f"{filename}\n{capture_dt.strftime('%Y-%m-%d %H:%M')} · {format_size(size)}"
        else:
            label = f"{filename}\n{format_size(size)}"
        item = PhotoItem(label, handle, filename, size, capture_dt, self._next_index)
        self._next_index += 1
        item.set_sort_key(self.sort_combo.currentText())
        if thumb:
            pix = QPixmap()
            if pix.loadFromData(thumb):
                item.thumb_pixmap = pix
                item.setIcon(QIcon(pix))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Unchecked)
        self.list_widget.addItem(item)
        self._items_by_handle[handle] = item
        self._photos_total += 1
        # Apply the active filter to the brand-new item so it doesn't pop up
        # when "hide downloaded" is on or a name filter is set.
        self._apply_filter_to_item(item)
        self.statusBar().showMessage(f"{self._photos_total} photo(s) chargée(s) …")

    @Slot(QListWidgetItem, QListWidgetItem)
    def _on_current_item_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None or not isinstance(current, PhotoItem):
            self.exif_panel.clear()
            self.preview_pane.clear()
            return
        # Inline preview — instant from thumb cache, upgrades when full bytes arrive.
        self.preview_pane.show_item(current)
        cached = current.exif_head
        if isinstance(cached, bytes):
            self.exif_panel.setPlainText(_format_exif(cached, current.filename, current.size))
            return
        header = f"{current.filename}\n{format_size(current.size)}\n" + "─" * 32 + "\nChargement EXIF …"
        self.exif_panel.setPlainText(header)
        if cached is None:
            current.exif_head = "loading"
            self.request_exif.emit(current.handle)

    @Slot(int, bytes)
    def _on_exif_loaded(self, handle: int, head: bytes) -> None:
        item = self._items_by_handle.get(handle)
        if item is None:
            return
        item.exif_head = head
        if self.list_widget.currentItem() is item:
            self.exif_panel.setPlainText(_format_exif(head, item.filename, item.size))

    @Slot(int, bytes)
    def _on_full_loaded(self, handle: int, data: bytes) -> None:
        item = self._items_by_handle.get(handle)
        if item is None:
            return
        item.full_data = data if data else None
        # Refresh the inline pane if it's showing this item.
        if data and self.list_widget.currentItem() is item:
            self.preview_pane.show_item(item)
        if self._preview is not None and self._preview.item is item:
            if data:
                self._preview.set_image(data)
            else:
                self._preview.set_error("téléchargement impossible")

    @Slot(dict)
    def _on_device_info(self, info: dict) -> None:
        model = (info.get("model") or "").strip()
        fw    = (info.get("device_version") or "").strip()
        title = self._BASE_TITLE
        if model:
            title = f"{model} — Transfert"
            if fw:
                title += f"  (firmware {fw})"
        self.setWindowTitle(title)

    @Slot(object)
    def _on_camera_datetime(self, cam_dt) -> None:
        if cam_dt is None or self._clock_warned:
            return
        drift = (datetime.now() - cam_dt).total_seconds()
        if abs(drift) < self._CLOCK_DRIFT_WARN_SECONDS:
            return
        self._clock_warned = True
        # Sign helps the user: positive = camera retarde sur l'ordinateur.
        delta_min = int(round(drift / 60))
        sense = "en retard" if delta_min > 0 else "en avance"
        QMessageBox.warning(
            self, "Horloge caméra décalée",
            f"La caméra est {sense} de {abs(delta_min)} min "
            f"par rapport à cet ordinateur.\n\n"
            f"Heure caméra   : {cam_dt:%Y-%m-%d %H:%M:%S}\n"
            f"Heure ordinateur : {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
            f"Les prochaines photos auront cette date dans leurs EXIF.\n"
            f"Tu peux la régler dans Menu → Setup ⚙ → Time zone and date.",
        )

    @Slot(object)
    def _on_battery_loaded(self, level) -> None:
        if level is None:
            self.battery_label.setText("")
            self._refresh_strip_separator()
            return
        # Petit indicateur visuel : ▰ pleine, ▱ vide (5 segments).
        filled = max(0, min(5, round(level / 20)))
        bars = "▰" * filled + "▱" * (5 - filled)
        self.battery_label.setText(f"Batterie {bars} {level} %")
        self._refresh_strip_separator()

    @Slot(int, int, str)
    def _on_storage_info(self, free_bytes: int, free_objects: int, label: str) -> None:
        parts = [f"{format_storage_size(free_bytes)} libres"]
        if free_objects:
            # Insère une espace fine insécable comme séparateur de milliers (FR).
            parts.append(f"~{free_objects:,} photos restantes".replace(",", " "))
        if label:
            parts.insert(0, label)
        self.storage_label.setText("Carte : " + "  ·  ".join(parts))
        self._refresh_strip_separator()

    def _refresh_strip_separator(self) -> None:
        """Show the separator dot only when both labels carry text."""
        self._strip_sep.setVisible(
            bool(self.storage_label.text()) and bool(self.battery_label.text())
        )

    @Slot(int)
    def _on_listing_done(self, total: int) -> None:
        self.statusBar().showMessage(
            f"{total} photo(s) — coche celles à importer puis Télécharger."
        )
        self.connect_btn.setEnabled(True)
        self.download_btn.setEnabled(total > 0)

    @Slot(str, int, int)
    def _on_download_progress(self, filename: str, idx: int, total: int) -> None:
        self.progress.setValue(idx)
        self.statusBar().showMessage(f"↓ {filename}  ({idx}/{total})")

    @Slot(dict)
    def _on_download_done(self, stats: dict) -> None:
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(True)  # ready for next time
        self.connect_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        # Just-transferred files now exist in dest — if hide-downloaded is on,
        # they should disappear from the grid.
        if self.hide_downloaded_cb.isChecked():
            self._apply_filter()
        prefix = "Transfert annulé — " if stats.get("cancelled") else ""
        msg = (
            f"{prefix}Transférés : {stats['transferred']}   "
            f"Erreurs : {stats['errors']}   "
            f"Volume : {format_size(stats['bytes'])}"
        )
        self.statusBar().showMessage(msg)
        title = "Transfert annulé" if stats.get("cancelled") else "Transfert terminé"
        QMessageBox.information(self, title, msg)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self.connect_btn.setEnabled(True)
        self.statusBar().showMessage(f"Erreur : {msg}")
        self._set_state("error", "Erreur")
        QMessageBox.warning(self, "Erreur", msg)

    @Slot()
    def _on_disconnected(self) -> None:
        self.connect_btn.setEnabled(True)
        self.statusBar().showMessage("Déconnecté")
        self.battery_label.clear()
        self.storage_label.clear()
        self._refresh_strip_separator()
        self.setWindowTitle(self._BASE_TITLE)
        self._set_state("disconnected", "Déconnecté")
        self._was_connected = False
        self._reconnect_attempt = 0

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._user_initiated_disconnect = True
        self._save_settings()
        self.request_disconnect.emit()
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)


def main() -> int:
    setup_logging(debug=False, auto_log=False)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
