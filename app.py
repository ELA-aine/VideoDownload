import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QRectF, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
import imageio_ffmpeg
from yt_dlp import YoutubeDL


def asset_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


class DragHeader(QWidget):
    def __init__(self):
        super().__init__()
        self.drag_offset = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self.drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self.drag_offset = None
        super().mouseReleaseEvent(event)


class ArtworkThumbnail(QLabel):
    def __init__(self, audio: bool = False):
        super().__init__()
        self.audio = audio
        self.artwork = QPixmap(str(asset_path("kawaii-cat-icon.png")))
        self.has_custom_artwork = False

    def set_audio(self, audio: bool) -> None:
        self.audio = audio
        self.update()

    def set_artwork(self, data: bytes | None) -> None:
        custom = QPixmap()
        self.has_custom_artwork = bool(data and custom.loadFromData(data))
        self.artwork = custom if self.has_custom_artwork else QPixmap(str(asset_path("kawaii-cat-icon.png")))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setClipPath(path)
        if (self.audio and not self.has_custom_artwork) or self.artwork.isNull():
            painter.fillRect(self.rect(), QColor("#f6dfe9" if self.audio else "#839afb"))
            painter.setPen(QColor("#e65d81" if self.audio else "white"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "♫" if self.audio else "↓")
            return
        if not self.has_custom_artwork:
            painter.fillRect(self.rect(), QColor("#ede8fa"))
            size = min(self.width(), self.height())
            target = QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)
            painter.drawPixmap(target, self.artwork, QRectF(self.artwork.rect()))
            return
        ratio = max(self.width() / self.artwork.width(), self.height() / self.artwork.height())
        source_width = self.width() / ratio
        source_height = self.height() / ratio
        source = QRectF((self.artwork.width() - source_width) / 2,
                        (self.artwork.height() - source_height) / 2,
                        source_width, source_height)
        painter.drawPixmap(QRectF(self.rect()), self.artwork, source)


class ChevronComboBox(QComboBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#596589"), 2))
        x = self.width() - 24
        y = self.height() // 2 - 2
        painter.drawLine(x - 5, y, x, y + 5)
        painter.drawLine(x, y + 5, x + 5, y)


class StatusBadge(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(32, 32)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#34b66e"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 30, 30)
        painter.setPen(QPen(QColor("white"), 2.5))
        painter.drawLine(9, 16, 14, 21)
        painter.drawLine(14, 21, 23, 11)


def is_youtube_link(link: str) -> bool:
    parsed = urlparse(link.strip())
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and (hostname == "youtube.com" or hostname.endswith(".youtube.com") or hostname == "youtu.be")
        and bool(parsed.path.strip("/"))
    )


def javascript_runtimes() -> dict:
    locations = {
        "deno": [Path.home() / ".deno/bin/deno", Path("/opt/homebrew/bin/deno"), Path("/usr/local/bin/deno")],
        "node": [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")],
    }
    runtimes = {}
    for name, candidates in locations.items():
        executable = shutil.which(name)
        if executable is None:
            executable = next((str(path) for path in candidates if path.is_file()), None)
        if executable is not None:
            runtimes[name] = {"path": executable}
    return runtimes


def capture_thumbnail(source: Path) -> bytes | None:
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-nostdin", "-ss", "1", "-i", str(source), "-frames:v", "1",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 and result.stdout else None


class DownloadWorker(QThread):
    progress_changed = Signal(int)
    status_changed = Signal(str)
    title_changed = Signal(str)
    completed = Signal(str, str)
    failed = Signal(str)

    def __init__(self, link: str, destination: Path, audio_only: bool, quality: int | None):
        super().__init__()
        self.link = link
        self.destination = destination
        self.audio_only = audio_only
        self.quality = quality
        self.display_name = "YouTube video"
        self.output_detail = f"MP3 · {quality} kbps" if audio_only else f"MP4 · {quality}p" if quality else "MP4 · Best available"
        self.thumbnail_data = None

    def report_progress(self, update: dict) -> None:
        title = update.get("info_dict", {}).get("title")
        if title and title != self.display_name:
            self.display_name = title
            self.title_changed.emit(title)
        if update.get("status") == "downloading":
            total = update.get("total_bytes") or update.get("total_bytes_estimate")
            if total:
                percent = min(100, int(update.get("downloaded_bytes", 0) * 100 / total))
                self.progress_changed.emit(percent)
            self.status_changed.emit("Downloading audio…" if self.audio_only else "Downloading video…")
        elif update.get("status") == "finished":
            self.status_changed.emit("Finishing audio…" if self.audio_only else "Finishing video…")

    def run(self) -> None:
        try:
            runtimes = javascript_runtimes()
            if not runtimes:
                raise RuntimeError("Install Node.js or Deno to download YouTube videos.")
            video_format = "bv*+ba/b" if self.quality is None else f"bv*[height<={self.quality}]+ba/b[height<={self.quality}]"
            options = {
                "format": "bestaudio/best" if self.audio_only else video_format,
                "js_runtimes": runtimes,
                "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
                "outtmpl": str(self.destination / "%(title).200B [%(id)s].%(ext)s"),
                "noplaylist": True,
                "progress_hooks": [self.report_progress],
                "quiet": True,
                "noprogress": True,
                "no_warnings": True,
            }
            if self.audio_only:
                options["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(self.quality),
                }]
            else:
                options["merge_output_format"] = "mp4"
            self.status_changed.emit("Fetching video information…")
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(self.link, download=True)
            if not info:
                raise RuntimeError("The download did not complete.")
            if not self.audio_only:
                video_id = info.get("id")
                if video_id:
                    files = [file for file in self.destination.iterdir()
                             if file.is_file() and f"[{video_id}]" in file.name
                             and file.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}]
                    if files:
                        self.thumbnail_data = capture_thumbnail(max(files, key=lambda file: file.stat().st_mtime))
        except Exception as error:
            self.failed.emit(str(error))
        else:
            label = "Audio" if self.audio_only else "Video"
            self.completed.emit(str(self.destination), f"{label} saved to {self.destination}.")


def available_output_path(destination: Path, source: Path) -> Path:
    output = destination / f"{source.stem}.mp3"
    number = 2
    while output.exists():
        output = destination / f"{source.stem} ({number}).mp3"
        number += 1
    return output


class ConvertWorker(QThread):
    completed = Signal(str, str)
    failed = Signal(str)

    def __init__(self, source: Path, destination: Path, bitrate: int):
        super().__init__()
        self.source = source
        self.destination = destination
        self.bitrate = bitrate
        self.display_name = source.stem
        self.output_detail = f"MP3 · {bitrate} kbps"
        self.thumbnail_data = None

    def run(self) -> None:
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.destination, suffix=".mp3", delete=False) as temporary:
                temporary_path = Path(temporary.name)
            command = [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(self.source), "-map", "0:a:0", "-vn",
                "-c:a", "libmp3lame", "-b:a", f"{self.bitrate}k", str(temporary_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Could not convert this file to MP3.")
            output = available_output_path(self.destination, self.source)
            temporary_path.replace(output)
            self.thumbnail_data = capture_thumbnail(self.source)
            self.completed.emit(str(self.destination), f"MP3 saved as {output.name}.")
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.last_destination = None
        self.download_format_index = 0
        self.recent_rows = []
        self.folder_path = Path.home() / "Downloads"
        self.setWindowTitle("Video Download")
        self.setWindowIcon(QIcon(str(asset_path("kawaii-cat-icon.png"))))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(min(800, screen.width() - 40), min(680, screen.height() - 40))
        self.resize(min(1120, int(screen.width() * 0.86)),
                    min(780, int(screen.height() * 0.88)))
        self.move(screen.x() + (screen.width() - self.width()) // 2,
                  screen.y() + (screen.height() - self.height()) // 2)

        shell = QFrame()
        shell.setObjectName("shell")
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)

        header_widget = DragHeader()
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(16)
        window_controls = QWidget()
        window_controls.setFixedWidth(143)
        window_buttons = QHBoxLayout(window_controls)
        window_buttons.setContentsMargins(2, 0, 0, 0)
        window_buttons.setSpacing(11)
        for name, action in (("close_dot", self.close), ("minimize_dot", self.showMinimized),
                             ("maximize_dot", self.toggle_maximize)):
            button = QPushButton()
            button.setObjectName(name)
            button.setFixedSize(18, 18)
            button.clicked.connect(action)
            window_buttons.addWidget(button)
        window_buttons.addStretch()
        header.addWidget(window_controls)
        header.addStretch()
        logo = QLabel()
        logo.setObjectName("logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(62, 62)
        logo.setPixmap(QPixmap(str(asset_path("kawaii-cat-icon.png"))).scaled(
            62, 62, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        header.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(2)
        title = QLabel("Video Downloader")
        title.setObjectName("title")
        brand.addWidget(title)
        subtitle = QLabel("Save your favorite videos, easily.")
        subtitle.setObjectName("muted")
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()

        header_tools = QWidget()
        header_tools.setFixedWidth(143)
        tool_layout = QHBoxLayout(header_tools)
        tool_layout.setContentsMargins(0, 0, 0, 0)
        tool_layout.setSpacing(10)
        tool_layout.addStretch()
        self.settings_button = QPushButton("⚙")
        self.settings_button.setObjectName("header_tool")
        self.settings_button.setToolTip("Download settings and mode")
        self.settings_button.setFixedSize(48, 48)
        self.settings_menu = QMenu(self)
        self.download_action = self.settings_menu.addAction("Download a link")
        self.convert_action = self.settings_menu.addAction("Convert a local video to MP3")
        for action in (self.download_action, self.convert_action):
            action.setCheckable(True)
        self.download_action.setChecked(True)
        self.download_action.triggered.connect(lambda: self.change_mode(0))
        self.convert_action.triggered.connect(lambda: self.change_mode(1))
        self.settings_menu.addSeparator()
        self.settings_menu.addAction("Choose output folder…", self.choose_folder)
        self.settings_button.setMenu(self.settings_menu)
        tool_layout.addWidget(self.settings_button)
        self.more_button = QPushButton("•••")
        self.more_button.setObjectName("header_tool")
        self.more_button.setToolTip("More options")
        self.more_button.setFixedSize(48, 48)
        more_menu = QMenu(self)
        more_menu.addAction("Open output folder", self.open_output_folder)
        more_menu.addAction("About Video Download", self.show_about)
        self.more_button.setMenu(more_menu)
        tool_layout.addWidget(self.more_button)
        header.addWidget(header_tools)
        layout.addWidget(header_widget)

        main_card, main_layout = self.make_card()
        main_card.setObjectName("download_card")
        main_card.setMinimumHeight(236)
        main_layout.setContentsMargins(18, 12, 18, 12)
        main_layout.setSpacing(10)
        self.stack = QStackedWidget()
        download_page = QWidget()
        download_layout = QHBoxLayout(download_page)
        download_layout.setContentsMargins(0, 0, 0, 0)
        link_icon = QLabel("↗")
        link_icon.setObjectName("input_icon")
        download_layout.addWidget(link_icon)
        self.link_input = QLineEdit()
        self.link_input.setPlaceholderText("Paste video link here…")
        self.link_input.returnPressed.connect(self.start_action)
        download_layout.addWidget(self.link_input)
        self.paste_button = QPushButton("Paste")
        self.paste_button.setObjectName("soft")
        self.paste_button.clicked.connect(self.paste_link)
        download_layout.addWidget(self.paste_button)
        self.stack.addWidget(download_page)

        convert_page = QWidget()
        convert_layout = QHBoxLayout(convert_page)
        convert_layout.setContentsMargins(0, 0, 0, 0)
        file_icon = QLabel("♫")
        file_icon.setObjectName("input_icon")
        convert_layout.addWidget(file_icon)
        self.file_input = QLineEdit()
        self.file_input.setReadOnly(True)
        self.file_input.setPlaceholderText("Choose a video to convert to MP3…")
        convert_layout.addWidget(self.file_input)
        self.file_browse_button = QPushButton("Choose file")
        self.file_browse_button.setObjectName("soft")
        self.file_browse_button.clicked.connect(self.choose_file)
        convert_layout.addWidget(self.file_browse_button)
        self.stack.addWidget(convert_page)
        input_frame = QFrame()
        input_frame.setObjectName("input_frame")
        input_layout = QVBoxLayout(input_frame)
        input_layout.setContentsMargins(10, 5, 9, 5)
        input_layout.addWidget(self.stack)
        main_layout.addWidget(input_frame)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(20)
        format_column = QVBoxLayout()
        format_column.setSpacing(7)
        format_column.addWidget(self.setting_heading("▣", "Format", "format_icon"))
        self.format_input = ChevronComboBox()
        self.format_input.addItems(["MP4 (Video)", "MP3 (Audio)"])
        format_column.addWidget(self.format_input)
        settings_row.addLayout(format_column, 1)

        quality_column = QVBoxLayout()
        quality_column.setSpacing(7)
        quality_column.addWidget(self.setting_heading("✿", "Quality", "quality_icon"))
        self.quality_input = ChevronComboBox()
        quality_column.addWidget(self.quality_input)
        settings_row.addLayout(quality_column, 1)

        folder_column = QVBoxLayout()
        folder_column.setSpacing(7)
        folder_column.addWidget(self.setting_heading("▰", "Output Folder", "output_icon"))
        folder_row = QHBoxLayout()
        folder_row.setSpacing(7)
        self.folder_input = QLineEdit(self.folder_path.name)
        self.folder_input.setPlaceholderText("Choose a folder")
        self.folder_input.setToolTip(str(self.folder_path))
        folder_row.addWidget(self.folder_input)
        self.browse_button = QPushButton("📁")
        self.browse_button.setObjectName("folder_button")
        self.browse_button.setToolTip("Choose output folder")
        self.browse_button.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.browse_button)
        folder_column.addLayout(folder_row)
        settings_row.addLayout(folder_column, 1)
        main_layout.addLayout(settings_row)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.action_button = QPushButton("↓   Download")
        self.action_button.setObjectName("primary")
        self.action_button.clicked.connect(self.start_action)
        self.action_button.setMinimumWidth(340)
        action_row.addWidget(self.action_button)
        action_row.addStretch()
        main_layout.addLayout(action_row)
        layout.addWidget(main_card)

        self.format_input.currentIndexChanged.connect(self.update_quality_options)
        self.update_quality_options()

        activity_card, activity_layout = self.make_card()
        activity_card.setMinimumHeight(102)
        activity_row = QHBoxLayout()
        activity_row.setSpacing(16)
        self.active_icon = ArtworkThumbnail()
        self.active_icon.setObjectName("active_icon")
        self.active_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.active_icon.setFixedSize(74, 74)
        activity_row.addWidget(self.active_icon)
        activity_detail = QVBoxLayout()
        self.active_title = QLabel("Ready for your next download")
        self.active_title.setObjectName("activity_title")
        activity_detail.addWidget(self.active_title)
        self.status_label = QLabel("Paste a link or choose a video file to get started.")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        activity_detail.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        activity_detail.addWidget(self.progress_bar)
        activity_row.addLayout(activity_detail, 1)
        activity_right = QVBoxLayout()
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("percent")
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        activity_right.addWidget(self.percent_label)
        self.open_folder_button = QPushButton("▣")
        self.open_folder_button.setObjectName("flat")
        self.open_folder_button.setToolTip("Open output folder")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self.open_folder)
        activity_right.addWidget(self.open_folder_button)
        activity_row.addLayout(activity_right)
        activity_layout.addLayout(activity_row)
        layout.addWidget(activity_card)

        recent_card, recent_card_layout = self.make_card()
        recent_header = QHBoxLayout()
        recent_header.addWidget(self.section_label("◷   Recent Downloads"))
        recent_header.addStretch()
        self.clear_button = QPushButton("Clear All")
        self.clear_button.setObjectName("flat")
        self.clear_button.clicked.connect(self.clear_recent)
        recent_header.addWidget(self.clear_button)
        recent_card_layout.addLayout(recent_header)
        separator = QFrame()
        separator.setObjectName("separator")
        separator.setFixedHeight(1)
        recent_card_layout.addWidget(separator)
        recent_scroll = QScrollArea()
        recent_scroll.setWidgetResizable(True)
        recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        recent_scroll.viewport().setStyleSheet("background: transparent;")
        recent_content = QWidget()
        recent_content.setStyleSheet("background: transparent;")
        self.recent_layout = QVBoxLayout(recent_content)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_layout.setSpacing(8)
        self.empty_recent = QLabel("Completed downloads and conversions appear here.")
        self.empty_recent.setObjectName("empty")
        self.empty_recent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_layout.addWidget(self.empty_recent)
        self.recent_layout.addStretch()
        recent_scroll.setWidget(recent_content)
        recent_scroll.setMinimumHeight(80)
        recent_card_layout.addWidget(recent_scroll)
        layout.addWidget(recent_card, 1)

        self.progress_bar.valueChanged.connect(self.update_progress_label)

        self.setCentralWidget(shell)
        self.setStyleSheet("""
            QMainWindow, QWidget { color: #252d50; font-size: 14px; }
            QFrame#shell { background: #f7f6fb; border: none; border-radius: 22px; }
            QPushButton#close_dot, QPushButton#minimize_dot, QPushButton#maximize_dot {
                border-radius: 9px; min-width: 18px; max-width: 18px;
                min-height: 18px; max-height: 18px; padding: 0; }
            QPushButton#close_dot { background: #f36a66; border: 1px solid #dd514d; }
            QPushButton#minimize_dot { background: #f8c453; border: 1px solid #e2ac39; }
            QPushButton#maximize_dot { background: #45c866; border: 1px solid #30b44e; }
            QPushButton#header_tool { background: #f8f8fc; color: #505a7c;
                border: 1px solid #e4e5ef; border-radius: 16px;
                padding: 0; font-size: 21px; }
            QPushButton#header_tool::menu-indicator { image: none; width: 0; }
            QMenu { background: white; color: #293252; border: 1px solid #e3e5ef;
                border-radius: 12px; padding: 7px; }
            QMenu::item { padding: 8px 20px; border-radius: 7px; }
            QMenu::item:selected { background: #e9edff; color: #4668d8; }
            QLabel#logo { background: transparent; border: none; }
            QLabel#title { color: #242b4d; font-size: 25px; font-weight: 700; }
            QLabel#muted { color: #7b83a2; }
            QLabel#section { color: #515a7b; font-size: 14px; font-weight: 700; }
            QLabel#setting_label { color: #596180; font-size: 13px; font-weight: 700; }
            QLabel#format_icon, QLabel#quality_icon, QLabel#output_icon {
                min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
                border-radius: 11px; font-size: 19px; font-weight: 700; }
            QLabel#format_icon { background: #eceaff; color: #6559ed; }
            QLabel#quality_icon { background: #e3f6ed; color: #2ca876; }
            QLabel#output_icon { background: #fff3df; color: #e2a229; }
            QLabel#activity_title { color: #212946; font-size: 18px; font-weight: 700; }
            QLabel#percent { color: #447bfa; font-size: 19px; font-weight: 700; }
            QLabel#empty { color: #969db5; padding: 34px; }
            QLabel#input_icon { color: #70799d; font-size: 23px; padding: 0 8px; }
            QLabel#active_icon, QLabel#recent_icon { border: none; background: transparent; }
            QLabel#recent_name { color: #222944; font-weight: 700; }
            QLabel#recent_detail { color: #858da8; }
            QLabel#recent_done { color: #293352; font-weight: 600; }
            QFrame#card, QFrame#download_card { background: rgba(255, 255, 255, 246);
                border: 1px solid #ebebf3; border-radius: 21px; }
            QFrame#separator { background: #e9eaf2; border: none; }
            QFrame#input_frame { background: #ffffff; border: 1px solid #dbe0ee;
                border-radius: 17px; }
            QFrame#recent_row { background: transparent; border: none;
                border-bottom: 1px solid #ebedf4; }
            QLineEdit, QComboBox { background: #ffffff; color: #27304f;
                border: 1px solid #dce0ee; border-radius: 12px;
                padding: 9px 12px; min-height: 26px; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #829afa; }
            QFrame#input_frame QLineEdit { border: none; background: transparent;
                font-size: 16px; padding-left: 3px; }
            QComboBox QAbstractItemView { background: white; color: #27304f; }
            QComboBox::drop-down { border: none; width: 25px; }
            QPushButton { background: white; color: #4d567b; border: 1px solid #dfe2ef;
                border-radius: 12px; padding: 9px 15px; font-weight: 600; }
            QPushButton:hover { background: #f1f3ff; }
            QPushButton#soft { background: #e9edff; color: #4165df;
                border: none; padding: 9px 17px; }
            QPushButton#soft:hover { background: #dce4ff; }
            QPushButton#folder_button { min-width: 28px; font-size: 18px; }
            QPushButton#primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #77a5ff, stop:0.52 #798df8, stop:1 #a071f0); color: white;
                border: none; border-radius: 22px; font-size: 18px;
                padding: 14px 30px; }
            QPushButton#primary:hover { background: #7889f8; }
            QPushButton#flat { background: #fbfbfe; color: #667195;
                border: 1px solid #e0e3ef; padding: 7px 12px; }
            QPushButton#row_tool { background: #f8f9fc; color: #56617f;
                border: 1px solid #e4e7f1; border-radius: 13px;
                padding: 0; font-size: 18px; }
            QPushButton#row_tool::menu-indicator { image: none; width: 0; }
            QPushButton:disabled { color: #a7acc3; background: #f1f2f7;
                border-color: #e7e8f0; }
            QProgressBar { border: none; border-radius: 5px; background: #e7eaf3;
                min-height: 9px; max-height: 9px; text-align: center; }
            QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #7eabff, stop:1 #5387fb); border-radius: 5px; }
            QScrollArea { background: transparent; border: none; }
        """)

    @staticmethod
    def make_card() -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.setSpacing(12)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(26)
        shadow.setColor(QColor(76, 70, 136, 20))
        shadow.setOffset(0, 6)
        card.setGraphicsEffect(shadow)
        return card, card_layout

    @staticmethod
    def section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    @staticmethod
    def setting_heading(symbol: str, title: str, icon_name: str) -> QWidget:
        heading = QWidget()
        row = QHBoxLayout(heading)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        icon = QLabel(symbol)
        icon.setObjectName(icon_name)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(icon)
        label = QLabel(title)
        label.setObjectName("setting_label")
        row.addWidget(label)
        row.addStretch()
        return heading

    def change_mode(self, mode: int) -> None:
        if mode == self.stack.currentIndex():
            self.download_action.setChecked(mode == 0)
            self.convert_action.setChecked(mode == 1)
            return
        if mode == 1:
            self.download_format_index = self.format_input.currentIndex()
            self.format_input.setCurrentIndex(1)
        else:
            self.format_input.setCurrentIndex(self.download_format_index)
        self.stack.setCurrentIndex(mode)
        self.download_action.setChecked(mode == 0)
        self.convert_action.setChecked(mode == 1)
        self.format_input.setEnabled(mode == 0)
        self.update_quality_options()

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def open_output_folder(self) -> None:
        folder = self.selected_destination()
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_about(self) -> None:
        QMessageBox.information(self, "About Video Download",
                                "Download YouTube videos as MP4 or MP3, or convert a local video to MP3.")

    def update_quality_options(self, _index: int | None = None) -> None:
        self.quality_input.clear()
        if self.format_input.currentIndex() == 1:
            for bitrate in (320, 192, 128):
                self.quality_input.addItem(f"{bitrate} kbps", bitrate)
        else:
            self.quality_input.addItem("Best available", None)
            for height, description in ((1080, "Full HD"), (720, "HD"), (480, "SD")):
                self.quality_input.addItem(f"{height}p ({description})", height)
        self.quality_input.setCurrentIndex(0 if self.format_input.currentIndex() == 1 else 1)
        self.update_action_text()

    def update_action_text(self) -> None:
        if self.stack.currentIndex() == 1:
            self.action_button.setText("♫   Convert to MP3")
        else:
            self.action_button.setText("↓   Download")

    def paste_link(self) -> None:
        self.link_input.setText(QApplication.clipboard().text().strip())

    def choose_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Choose a video", str(Path.home()),
            "Video files (*.mp4 *.mov *.mkv *.webm *.avi *.m4v);;All files (*)",
        )
        if selected:
            self.file_input.setText(selected)

    def choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose download folder", str(self.selected_destination()))
        if selected:
            self.folder_path = Path(selected)
            self.folder_input.setText(self.folder_path.name)
            self.folder_input.setToolTip(str(self.folder_path))

    def selected_destination(self) -> Path:
        entry = self.folder_input.text().strip()
        if entry == self.folder_path.name:
            return self.folder_path
        return Path(entry).expanduser()

    def set_busy(self, busy: bool) -> None:
        for control in (
            self.settings_button, self.link_input, self.paste_button,
            self.format_input, self.quality_input, self.file_browse_button, self.folder_input,
            self.browse_button, self.action_button,
        ):
            control.setEnabled(not busy)
        if not busy and self.stack.currentIndex() == 1:
            self.format_input.setEnabled(False)

    def update_progress_label(self, value: int) -> None:
        self.percent_label.setText("…" if self.progress_bar.maximum() == 0 else f"{max(0, value)}%")

    def start_action(self) -> None:
        if self.worker is not None:
            return

        destination = self.selected_destination()
        if not destination.is_dir():
            self.status_label.setText("Choose an existing output folder.")
            return

        if self.stack.currentIndex() == 0:
            link = self.link_input.text().strip()
            if not is_youtube_link(link):
                self.status_label.setText("Enter a valid YouTube video link.")
                return
            self.worker = DownloadWorker(
                link, destination, self.format_input.currentIndex() == 1,
                self.quality_input.currentData(),
            )
            self.progress_bar.setRange(0, 100)
            self.worker.progress_changed.connect(self.progress_bar.setValue)
            self.worker.status_changed.connect(self.status_label.setText)
            self.worker.title_changed.connect(self.active_title.setText)
            self.active_title.setText("Preparing your download")
            self.active_icon.set_artwork(None)
            self.active_icon.set_audio(self.worker.audio_only)
        else:
            source = Path(self.file_input.text())
            if not source.is_file():
                self.status_label.setText("Choose a video file to convert.")
                return
            self.worker = ConvertWorker(source, destination, self.quality_input.currentData())
            self.progress_bar.setRange(0, 0)
            self.active_title.setText(source.name)
            self.active_icon.set_artwork(None)
            self.active_icon.set_audio(True)
            self.status_label.setText("Converting to MP3…")

        self.set_busy(True)
        self.open_folder_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.update_progress_label(self.progress_bar.value())
        self.worker.completed.connect(self.task_complete)
        self.worker.failed.connect(self.task_failed)
        self.worker.finished.connect(self.task_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def task_finished(self) -> None:
        self.set_busy(False)
        self.worker = None

    def task_complete(self, destination: str, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(message)
        self.active_title.setText(self.worker.display_name)
        self.active_icon.set_artwork(self.worker.thumbnail_data)
        self.last_destination = Path(destination)
        self.open_folder_button.setEnabled(True)
        self.add_recent(self.worker.display_name, self.worker.output_detail,
                        self.last_destination, self.worker.thumbnail_data)

    def task_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.active_title.setText("Couldn't finish")
        self.status_label.setText("Could not finish. See the error for details.")
        QMessageBox.warning(self, "Could not finish", message)

    def add_recent(self, name: str, detail: str, folder: Path, thumbnail: bytes | None = None) -> None:
        self.empty_recent.hide()
        row = QFrame()
        row.setObjectName("recent_row")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 4, 8, 4)
        row_layout.setSpacing(12)
        icon = ArtworkThumbnail(detail.startswith("MP3"))
        icon.set_artwork(thumbnail)
        icon.setObjectName("recent_icon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(112, 64)
        row_layout.addWidget(icon)
        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        name_label = QLabel(name)
        name_label.setObjectName("recent_name")
        text_column.addWidget(name_label)
        detail_label = QLabel(detail)
        detail_label.setObjectName("recent_detail")
        text_column.addWidget(detail_label)
        row_layout.addLayout(text_column, 1)
        done_icon = StatusBadge()
        row_layout.addWidget(done_icon)
        done_column = QVBoxLayout()
        done_column.setSpacing(1)
        done = QLabel("Completed")
        done.setObjectName("recent_done")
        done_column.addWidget(done)
        completed_at = QLabel(datetime.now().strftime("Today · %I:%M %p"))
        completed_at.setObjectName("recent_detail")
        done_column.addWidget(completed_at)
        row_layout.addLayout(done_column)
        open_button = QPushButton("▣")
        open_button.setObjectName("row_tool")
        open_button.setFixedSize(46, 46)
        open_button.setToolTip(str(folder))
        open_button.clicked.connect(lambda _checked=False, selected=folder: QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected))))
        row_layout.addWidget(open_button)
        more_button = QPushButton("•••")
        more_button.setObjectName("row_tool")
        more_button.setFixedSize(46, 46)
        more_button.setToolTip("More actions")
        row_menu = QMenu(more_button)
        row_menu.addAction("Open output folder", lambda selected=folder: QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected))))
        row_menu.addAction("Remove from list", lambda selected=row: self.remove_recent(selected))
        more_button.setMenu(row_menu)
        row_layout.addWidget(more_button)
        self.recent_layout.insertWidget(0, row)
        self.recent_rows.insert(0, row)
        if len(self.recent_rows) > 10:
            oldest = self.recent_rows.pop()
            self.recent_layout.removeWidget(oldest)
            oldest.deleteLater()

    def clear_recent(self) -> None:
        for row in self.recent_rows:
            self.recent_layout.removeWidget(row)
            row.deleteLater()
        self.recent_rows.clear()
        self.empty_recent.show()

    def remove_recent(self, row: QFrame) -> None:
        if row in self.recent_rows:
            self.recent_rows.remove(row)
            self.recent_layout.removeWidget(row)
            row.deleteLater()
        if not self.recent_rows:
            self.empty_recent.show()

    def open_folder(self) -> None:
        if self.last_destination is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_destination)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None:
            self.status_label.setText("Please wait for the current task to finish before closing.")
            event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    application = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(application.exec())
