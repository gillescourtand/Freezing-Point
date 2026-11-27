# -*- coding: utf-8 -*-
"""
Video player widgets (pyqtgraph display + transport controls) for FreezingPoint.

Created on Wed Nov 10 14:22:52 2021
@author: courtand

Same public API as video_player_6 (Video, UIVideoDisplay, UIVideoPlayer, signals, widgets).

History
-------
v_2  : rescale button, loadVideoSignal
v_2c : crash at the end of the video fixed
v_2d : signals, playSignal.emit on play
v_3  : keyboard control, rotation (90, -90, 180), configurable frame skip, timeLineMoveSignal
v_4  : pyqtgraph.QtGui replaced by PyQt5.QtWidgets (pyqtgraph 0.13, python 3.10)
v_7  : cleanup / robustness
       - `keyboard` library no longer imported (demo uses QShortcut, no admin rights needed)
       - reader thread: one thread at a time (pause/play used to leave the old one running on the
         same capture), no more 2 s sleeps, skipped frames use grab() instead of a seek per frame
       - GUI no longer blocked by sleep() in play / when the buffer is empty
       - "each n frames" playback starts on the requested frame (was n-1 frames ahead)
       - end of video pauses the player (pauseSignal, play button released)
       - init_video() records vid.path itself
       - capture released when a new video is loaded; failed read keeps the previous frame
       - read buffer bounded (MAX_BUFFER_FRAMES) instead of 2/3 of the free RAM
       - rotation lookup table (unknown value = no rotation instead of UnboundLocalError)
       - slider maximum = last valid frame index (was nbFrames: one unreadable frame at the end)
       - vectorised gamma table, dataclass for Video, dead code / unused widgets removed
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from queue import Full, Queue
from threading import Event, Thread, current_thread
from typing import Any

import cv2
import numpy as np
import psutil
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import QEvent, QObject, pyqtSignal
from PyQt5.QtGui import QIcon
from pyqtgraph.dockarea import Dock, DockArea

log = logging.getLogger(__name__)

NO_ROTATION = "No rotation"
ROTATIONS = {
    "90_clockwise": cv2.ROTATE_90_CLOCKWISE,
    "90_counterclockwise": cv2.ROTATE_90_COUNTERCLOCKWISE,
    "180": cv2.ROTATE_180,
}
VIDEO_EXTENSIONS = "*.mpg *.mp4 *.mov *.mkv *.avi"
MAX_BUFFER_FRAMES = 400  # decoded frames kept ahead of the display (~16 s at 25 fps)
DEFAULT_GAMMA = 1.8


# --------------------------------------------------------------------------- helpers
def resource_path(relative_path: str) -> str:
    """Absolute path of a resource, in development and in a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS  # noqa: SLF001 - PyInstaller
    else:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  # one level up
    return os.path.join(base_path, relative_path)


def open_file(ui, file_type: str, file_format: str) -> str:
    """
    File dialog.  video: ("Video file", "*.avi *.mp4"), data: ("Data file", "*.csv"),
    state: ("Workspace state file", "*.wkp").  Returns "" when cancelled.
    """
    patterns = file_format.strip().strip("()").replace(";", " ")
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        ui, f"Open {file_type}", QtCore.QDir.homePath(), f"{file_type} ({patterns})")
    return path


def extract_fileName(file_path: str) -> str:
    return os.path.split(file_path)[-1]


def set_videoRotation(vid: "Video", frame: np.ndarray) -> np.ndarray:
    """Rotate `frame` according to vid.rotation (unknown value / "No rotation": unchanged)."""
    code = ROTATIONS.get(vid.rotation)
    return frame if code is None else cv2.rotate(frame, code)


def format_timecode(milliseconds: float) -> str:
    """mm:ss.mmm"""
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes):02d}:{int(seconds):02d}.{int(ms):03d}"


class ImageProcessor:
    @staticmethod
    def gamma_LUT(gamma: float) -> np.ndarray:
        """256-entry uint8 table applying gamma correction."""
        return ((np.arange(256) / 255.0) ** (1.0 / gamma) * 255).astype("uint8")

    def convert_imageToPyqtgraph(self, img: np.ndarray, vid: "Video") -> np.ndarray:
        """BGR/gray frame -> gamma corrected, transposed array for pyqtgraph (x, y order)."""
        if img.ndim > 2:  # colour image: the displayed image is the colour one
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        vid.currentTFrame = cv2.transpose(cv2.LUT(img, vid.LUT)[::-1, :])
        return vid.currentTFrame


@dataclass(eq=False)
class Video:
    """Video parameters shared by the player, the display and the main application."""

    path: str | None = None
    capture: cv2.VideoCapture | None = None
    firstFrame: int | None = None
    LUT: np.ndarray | None = None
    isAcquiring: bool = False
    buffering: bool = False
    playing: bool = False
    grabber: "FileVideoStream_ToPlay | None" = None
    frameWeight: float = 0
    measuredLivefps: float = 0

    currentGrayFrame: np.ndarray | None = None
    currentFrame: np.ndarray | None = None
    currentTFrame: np.ndarray | None = None  # transposed image displayed in the plot view

    # camera acquisition (not used by the player, kept for the other applications)
    devices: list = field(default_factory=list)
    device: Any = None
    featuresFile: Any = None
    pixFormat: Any = None
    ToRGB: Any = None
    grabFrameRate: Any = None
    offsetX: Any = None
    offsetY: Any = None
    centerX: Any = None
    centerY: Any = None
    gainAuto: Any = None
    gain: Any = None
    gamma: Any = None
    exposure: Any = None
    sensorReadoutMode: Any = None

    # properties of the opened video
    codec: float | None = None
    width: float | None = None
    height: float | None = None
    fps: float | None = None
    nbFrames: float | None = None

    rescale: float = 1
    rotation: str = NO_ROTATION  # or "90_clockwise", "90_counterclockwise", "180"
    pos: Any = None


# --------------------------------------------------------------------------- read-ahead thread
class FileVideoStream_ToPlay:
    """
    Decodes frames in a thread and stores them in a bounded queue ahead of the display.
    Only one reader thread runs at a time: start() stops and joins the previous one.
    """

    def __init__(self, videoStream: cv2.VideoCapture, video: Video, queueSize: int = 200):
        self.stream = videoStream
        self.vid = video
        self.frameStep = 1
        self.Q: Queue = Queue(maxsize=max(int(queueSize), 1))
        self._stop_event = Event()
        self._thread: Thread | None = None

    # ``stopped`` kept as an attribute for code written for video_player_6
    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    @stopped.setter
    def stopped(self, value: bool) -> None:
        self._stop_event.set() if value else self._stop_event.clear()

    def start(self, nframe: int, framerateFactor: int) -> "FileVideoStream_ToPlay":
        """Start reading at frame `nframe`, then every `framerateFactor` frames."""
        self.stop(wait=True)
        self.clear()
        self.frameStep = max(int(framerateFactor), 1)
        self.stream.set(cv2.CAP_PROP_POS_FRAMES, nframe)
        self._stop_event.clear()
        self._thread = Thread(target=self.update, daemon=True)
        self._thread.start()
        return self

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        if self.vid.rescale != 1:
            frame = cv2.resize(frame, None, fx=self.vid.rescale, fy=self.vid.rescale,
                               interpolation=cv2.INTER_CUBIC)
        if self.vid.rotation != NO_ROTATION:
            frame = set_videoRotation(self.vid, frame)
        return frame

    def _put(self, frame: np.ndarray) -> bool:
        """Blocking put that gives up as soon as the stream is stopped."""
        while not self._stop_event.is_set():
            try:
                self.Q.put(frame, timeout=0.05)
                return True
            except Full:
                continue
        return False

    def update(self) -> None:
        """Thread body: read until the last frame, keeping one frame out of `frameStep`."""
        end = int(self.stream.get(cv2.CAP_PROP_FRAME_COUNT))
        position = int(self.stream.get(cv2.CAP_PROP_POS_FRAMES))
        log.debug("reader started at frame %d / %d, step %d", position, end, self.frameStep)
        try:
            while position < end and not self._stop_event.is_set():
                grabbed, frame = self.stream.read()
                if not grabbed:
                    log.warning("frame %d not grabbed", position)
                    return
                position += 1
                if not self._put(self._prepare(frame)):
                    return
                for _ in range(self.frameStep - 1):  # skip: grab() does not convert the image
                    if not self.stream.grab():
                        return
                    position += 1
        finally:
            self._stop_event.set()
            log.debug("reader ended at frame %d", position)

    def read(self) -> np.ndarray:
        """Next frame of the queue (call more() first)."""
        return self.Q.get()

    def more(self) -> bool:
        return self.Q.qsize() > 0

    def finished(self) -> bool:
        """True once the reader thread has ended (end of video, error or stop)."""
        return self._thread is None or not self._thread.is_alive()

    def clear(self) -> None:
        with self.Q.mutex:
            self.Q.queue.clear()

    def stop(self, wait: bool = False) -> None:
        """Ask the thread to stop; `wait` joins it so the capture can safely be used elsewhere."""
        self._stop_event.set()
        thread = self._thread
        if wait and thread is not None and thread is not current_thread():
            thread.join(timeout=2.0)


# --------------------------------------------------------------------------- Qt widgets
def _splitter(widgets, orientation=QtCore.Qt.Horizontal) -> QtWidgets.QSplitter:
    splitter = QtWidgets.QSplitter(orientation)
    for widget in widgets:
        splitter.addWidget(widget)
    return splitter


def _label(text: str, align=None, max_width: int | None = None, min_width: int | None = None) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    if align is not None:
        label.setAlignment(align)
    if max_width is not None:
        label.setMaximumWidth(max_width)
    if min_width is not None:
        label.setMinimumWidth(min_width)
    return label


class ArrowKeyFilter(QObject):
    """Application-wide filter: Left / Right arrows = previous / next frame, wherever the focus is.

    The arrows are left alone when they mean something else: text fields, spin boxes, combo boxes,
    lists and tables (they move their own cursor / selection), modal dialogs, other windows,
    while the video is playing, and with Ctrl / Alt / Shift / Meta held.
    """

    _OWN_ARROWS = (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox, QtWidgets.QTextEdit,
                   QtWidgets.QPlainTextEdit, QtWidgets.QComboBox, QtWidgets.QAbstractItemView,
                   QtWidgets.QTabBar)

    def __init__(self, player: "UIVideoPlayer"):
        super().__init__(player)
        self.player = player

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if event.type() != QEvent.KeyPress or event.key() not in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            return False
        if event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier
                                | QtCore.Qt.ShiftModifier | QtCore.Qt.MetaModifier):
            return False
        player = self.player
        app = QtWidgets.QApplication.instance()
        if (player.vid.capture is None or player.vid.playing or not player.timeLine_slider.isEnabled()
                or app.activeModalWidget() is not None or app.activeWindow() is not player.window()
                or app.activePopupWidget() is not None):
            return False
        if isinstance(app.focusWidget(), self._OWN_ARROWS):
            return False
        if event.key() == QtCore.Qt.Key_Right:
            player.forward_video()
        else:
            player.backward_video()
        return True  # consumed (the slider must not move a second time by itself)


class UIVideoPlayer(pg.LayoutWidget):
    analysisSignal = pyqtSignal(int)
    loadVideoSignal = pyqtSignal()
    playSignal = pyqtSignal()
    pauseSignal = pyqtSignal()
    updatePlotSignal = pyqtSignal()
    updateTrackMapSignal = pyqtSignal()
    playFramerateSignal = pyqtSignal()
    timeLineMoveSignal = pyqtSignal(int)

    def __init__(self, videoDisplay: "UIVideoDisplay", video: Video, icon_path, parent=None):
        super().__init__()
        self.vid = video
        self.vidDisplay = videoDisplay
        self.image_processor = ImageProcessor()
        self.framerateFactor = 1
        self.idxResultArray = 0

        centered = QtCore.Qt.AlignCenter

        self.loadVideo_btn = QtWidgets.QPushButton("Load video")
        self.loadVideo_btn.clicked.connect(self.load_video)
        self.lb_videoName = _label(" No video ", max_width=200, min_width=100)

        self.playVideo_btn = QtWidgets.QPushButton()
        self.playVideo_btn.setCheckable(True)
        self.playVideo_btn.setChecked(False)
        self.playVideo_btn.setEnabled(False)
        self.playVideo_btn.clicked.connect(self.playpause_video)
        self.playVideo_btn.setIcon(QIcon(resource_path(os.path.join("Imagys_blue", "play_button_off.png"))))
        self.playVideo_btn.setMaximumWidth(80)
        self.playVideo_btn.setMinimumWidth(40)
        self.playVideo_btn.setMaximumHeight(20)

        self.stepFwdVideo_btn = self._step_button(">", self.forward_video)
        self.stepBwdVideo_btn = self._step_button("<", self.backward_video)

        # frame number / total, timecode / total duration
        self.numFrame_label = _label("00", centered, max_width=60)
        self.numTotalFrame_label = _label("00 frame", centered, max_width=60)
        splitter_numFrame = _splitter([self.numFrame_label, _label("/", centered, max_width=3),
                                       self.numTotalFrame_label])
        self.currentTime_label = _label("00", centered, max_width=60)
        self.totalTime_label = _label("00 sec.", centered, max_width=60)
        splitter_timeCode = _splitter([self.currentTime_label, _label("/", centered, max_width=3),
                                       self.totalTime_label])
        splitter_frame_time = _splitter([splitter_numFrame, splitter_timeCode], QtCore.Qt.Vertical)

        self.timeLine_slider = QtWidgets.QSlider()
        self.timeLine_slider.setPageStep(100)
        self.timeLine_slider.setProperty("value", 1)
        self.timeLine_slider.setOrientation(QtCore.Qt.Horizontal)
        self.timeLine_slider.valueChanged.connect(self.slider_move)
        self.timeLine_slider.valueChanged["int"].connect(self.numFrame_label.setNum)
        self.timeLine_slider.setEnabled(False)
        splitter_frameplayBtn = _splitter([self.timeLine_slider, self.playVideo_btn,
                                           self.stepBwdVideo_btn, self.stepFwdVideo_btn])

        # player options: playback step, display scale, rotation
        self.playSpeed_spinbox = self._spinbox(1, 200, self.set_framerateFactor)
        self.splitter_playSpeed = _splitter([_label("each n frame", max_width=80), self.playSpeed_spinbox])
        self.videoScale_spinbox = self._spinbox(1, 6, self.set_videoScale)
        splitter_videoScale = _splitter([_label("video scale 1/", max_width=80, min_width=80),
                                         self.videoScale_spinbox])
        self.videoRotation_comboBox = QtWidgets.QComboBox()
        self.videoRotation_comboBox.addItems([NO_ROTATION, *ROTATIONS])
        self.videoRotation_comboBox.activated.connect(self.apply_frameRotation)
        splitter_playerOptions = _splitter([self.splitter_playSpeed, splitter_videoScale,
                                            self.videoRotation_comboBox])

        # layout
        self.addWidget(splitter_frameplayBtn, row=0, col=0, colspan=6)
        self.addWidget(self.loadVideo_btn, row=1, col=0)
        self.addWidget(self.lb_videoName, row=1, col=1, colspan=3)
        self.addWidget(splitter_playerOptions, row=1, col=4, colspan=2)
        self.addWidget(splitter_frame_time, row=0, col=6, rowspan=2)

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.video_play_framerate)

    @staticmethod
    def _step_button(text: str, slot) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(slot)
        button.setEnabled(False)
        button.setFixedWidth(40)
        return button

    @staticmethod
    def _spinbox(minimum: int, maximum: int, slot) -> QtWidgets.QSpinBox:
        spinbox = QtWidgets.QSpinBox()
        spinbox.setMaximumWidth(50)
        spinbox.setMinimum(minimum)
        spinbox.setMaximum(maximum)
        spinbox.setValue(1)
        spinbox.valueChanged.connect(slot)
        return spinbox

    # ---- loading
    def load_video(self) -> None:
        path = open_file(None, "Video file", VIDEO_EXTENSIONS)
        if not path:
            log.info("no video selected")
            return
        self.init_video(path)

    def init_video(self, videoPath: str) -> None:
        """Open the video, read its properties, show its first frame and prepare the read buffer."""
        if self.vid.playing:
            self.pause_video()
        self.vid.path = videoPath
        self.lb_videoName.setText(extract_fileName(videoPath))

        if self.vid.capture is not None:
            self.vid.capture.release()
        try:
            capture = cv2.VideoCapture(videoPath)
            opened = capture.isOpened()
        except cv2.error as exc:
            log.error("cv2 error: %s", exc)
            QtWidgets.QMessageBox.warning(None, "Tracking", str(exc), QtWidgets.QMessageBox.Ok)
            return
        if not opened:
            capture.release()
            self.vid.capture = None
            QtWidgets.QMessageBox.warning(None, "Tracking", "video error", QtWidgets.QMessageBox.Ok)
            return

        self.vid.capture = capture
        self.vid.codec = capture.get(cv2.CAP_PROP_FOURCC)  # platform dependent, see fourcc.org
        self.vid.ToRGB = capture.get(cv2.CAP_PROP_CONVERT_RGB)
        self.vid.width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        self.vid.height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        self.vid.fps = capture.get(cv2.CAP_PROP_FPS)
        self.vid.nbFrames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        self.numTotalFrame_label.setText(str(int(self.vid.nbFrames)))
        if self.vid.fps:
            self.totalTime_label.setText(f"{self.vid.nbFrames / self.vid.fps:.1f} sec.")
        self.vid.LUT = self.image_processor.gamma_LUT(DEFAULT_GAMMA)
        self.vid.firstFrame = 1
        log.info("codec %s, %d frames at %s fps, %dx%d", self.vid.codec, self.vid.nbFrames,
                 self.vid.fps, self.vid.width, self.vid.height)

        self.update_toolTip(self.lb_videoName)
        self.loadVideoSignal.emit()
        self.activate_interface("load video")
        self.play_frame(self.vid.firstFrame)  # the slider is not modified yet: show the frame explicitly
        self.init_player()

    def init_player(self) -> None:
        """Slider range and read buffer size (as many frames as fit in RAM, at most MAX_BUFFER_FRAMES)."""
        if self.vid.currentFrame is None:
            return
        self.timeLine_slider.setMaximum(max(int(self.vid.nbFrames) - 1, 1))  # last valid frame index
        self.timeLine_slider.setValue(int(self.vid.firstFrame))

        available_mb = psutil.virtual_memory().available / 1024.0 ** 2
        self.vid.frameWeight = self.vid.currentFrame.nbytes / 1024 / 1024  # MB per frame
        frames_in_ram = int(2 / 3 * available_mb / self.vid.frameWeight)
        log.info("available memory %.0f MB, frame size %.2f MB", available_mb, self.vid.frameWeight)
        self.vid.grabber = FileVideoStream_ToPlay(self.vid.capture, self.vid,
                                                  max(1, min(frames_in_ram, MAX_BUFFER_FRAMES)))

    def update_toolTip(self, item: QtWidgets.QWidget) -> None:
        item.setToolTip(f"{self.vid.path} \n size : {int(self.vid.width)}, {int(self.vid.height)} \n "
                        f"frames : {int(self.vid.nbFrames)} at {int(self.vid.fps)} fps")

    # ---- transport
    def playpause_video(self) -> None:
        if self.vid.path is None:
            self.playVideo_btn.setChecked(False)
            QtWidgets.QMessageBox.warning(None, "no video", "You have to open a video first",
                                          QtWidgets.QMessageBox.Ok)
        elif self.playVideo_btn.isChecked():
            self.vid.playing = True
            self.playSignal.emit()
            self.splitter_playSpeed.setEnabled(False)
            self.vid.grabber.start(self.timeLine_slider.value(), self.framerateFactor)
            # frames are displayed as soon as the reader has queued them (no blocking prefill)
            self.timer.start(int(1000 / (self.vid.fps or 25)))
        else:
            self.pause_video()

    def pause_video(self) -> None:
        self.timer.stop()
        self.vid.playing = False
        if self.vid.grabber is not None:
            self.vid.grabber.stop(wait=True)  # the capture is used by play_frame from now on
            self.vid.grabber.clear()
        self.playVideo_btn.setChecked(False)
        self.splitter_playSpeed.setEnabled(True)
        self.pauseSignal.emit()

    def enable_arrow_keys(self) -> None:
        """Left / Right arrows step through the video from anywhere in the application window."""
        if getattr(self, "_arrow_filter", None) is None:
            self._arrow_filter = ArrowKeyFilter(self)
            QtWidgets.QApplication.instance().installEventFilter(self._arrow_filter)

    def forward_video(self) -> None:
        self.timeLine_slider.setValue(self.timeLine_slider.value() + self.framerateFactor)

    def backward_video(self) -> None:
        self.timeLine_slider.setValue(self.timeLine_slider.value() - self.framerateFactor)

    def slider_move(self) -> None:
        if self.vid.capture is not None and not self.playVideo_btn.isChecked():
            self.play_frame(self.timeLine_slider.value())
            self.timeLineMoveSignal.emit(self.timeLine_slider.value())

    def apply_frameRotation(self) -> None:
        self.vid.rotation = self.videoRotation_comboBox.currentText()
        self._refresh_frame()

    def set_framerateFactor(self, i: int) -> None:
        self.framerateFactor = i

    def set_videoScale(self, i: int) -> None:
        self.vid.rescale = 1 / i
        self._refresh_frame()

    def _refresh_frame(self) -> None:
        """Redraw the current frame (while playing, the reader applies the new settings itself)."""
        if self.vid.capture is not None and not self.vid.playing:
            self.play_frame(self.timeLine_slider.value())

    # ---- display
    def play_frame(self, iframe: int) -> None:
        """Seek to `iframe`, read it and display it."""
        capture = self.vid.capture
        if capture is None or not capture.set(cv2.CAP_PROP_POS_FRAMES, iframe):
            log.warning("wrong frame index %s", iframe)
            return
        grabbed, frame = capture.read()
        if not grabbed:
            log.warning("can't read frame %s", iframe)
            return
        label_time = format_timecode(capture.get(cv2.CAP_PROP_POS_MSEC))

        if self.vid.rescale != 1:
            frame = cv2.resize(frame, None, fx=self.vid.rescale, fy=self.vid.rescale,
                               interpolation=cv2.INTER_CUBIC)
        if self.videoRotation_comboBox.currentText() != NO_ROTATION:
            frame = set_videoRotation(self.vid, frame)
        self.vid.currentFrame = frame

        self.vidDisplay.img.setImage(self.image_processor.convert_imageToPyqtgraph(frame, self.vid),
                                     autoLevels=False)
        self.numFrame_label.setNum(iframe)
        self.currentTime_label.setText(label_time)
        self.analysisSignal.emit(iframe)  # history of the object-tracking zones

    def video_play_framerate(self) -> None:
        """Timer slot: display the next buffered frame."""
        grabber = self.vid.grabber
        reader_done = grabber.finished()  # read before more(): nothing can be queued once it is done
        if not grabber.more():
            if reader_done:
                log.info("end of video")
                self.pause_video()
            return  # otherwise the reader is late: wait for the next tick (GUI stays responsive)

        self.vid.currentFrame = grabber.read()
        self.vidDisplay.show_frame_in_pyqtgraph(self.vid.currentFrame)
        self.analysisSignal.emit(self.timeLine_slider.value())

        self.timeLine_slider.setValue(self.timeLine_slider.value() + self.framerateFactor)
        self.currentTime_label.setText(format_timecode(self.timeLine_slider.value() / (self.vid.fps or 25) * 1000))
        QtWidgets.QApplication.instance().processEvents()  # redraw the plots of the analysis

    def activate_interface(self, context: str) -> None:
        """Enable the transport controls once a video is loaded."""
        if context == "load video":
            self.playVideo_btn.setEnabled(True)
            self.playVideo_btn.setChecked(False)
            self.timeLine_slider.setEnabled(True)
            self.stepFwdVideo_btn.setEnabled(True)
            self.stepBwdVideo_btn.setEnabled(True)


class UIVideoDisplay(pg.GraphicsLayoutWidget):
    def __init__(self, video: Video, parent=None):
        super().__init__()
        self.vid = video
        self.image_processor = ImageProcessor()

        self.gviewBox = pg.ViewBox()  # specific view box (interactive ROI drawing)
        self.plotView = self.addPlot(viewBox=self.gviewBox)
        self.plotView.setAspectLocked(True)  # square pixels

        self.img = pg.ImageItem()
        self.gviewBox.addItem(self.img)

    def show_frame_in_pyqtgraph(self, cv2frame: np.ndarray) -> None:
        """Convert a cv2 frame to the pyqtgraph orientation and display it."""
        self.img.setImage(self.image_processor.convert_imageToPyqtgraph(cv2frame, self.vid), autoLevels=False)

    def contextMenuEvent(self, event) -> None:
        menu = QtWidgets.QMenu(self)
        load_action = QtWidgets.QAction("Load video", self)
        load_action.triggered.connect(self.load_video)
        menu.addAction(load_action)
        menu.exec_(event.globalPos())

    def load_video(self) -> None:
        """Hook for the context menu (the application connects its own loader)."""
        log.info("load video requested from the display context menu")


# --------------------------------------------------------------------------- stand-alone demo
class PyqtgWindow(QtWidgets.QMainWindow):
    """Dock window used to run this module on its own."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowIcon(QIcon(resource_path(os.path.join("Imagys_blue", "logoAnimotion-square-112.png"))))
        self.area = DockArea()
        self.setCentralWidget(self.area)
        self.resize(1500, 800)
        self.setWindowTitle("Video Player - beta")

        self.d1 = Dock("Image", size=(1000, 500))
        self.d9 = Dock("Video player", size=(500, 200))
        self.area.addDock(self.d1, "left")
        self.area.addDock(self.d9, "bottom", self.d1)

        try:  # optional qss skin
            sys.path.append(resource_path("Imagys_blue"))
            from qsshelper import QSSHelper
            self.setStyleSheet(QSSHelper.open_qss(resource_path(os.path.join("Imagys_blue", "Imagys-blue.qss"))))
        except Exception as exc:  # noqa: BLE001 - the skin is cosmetic
            log.info("no qss skin: %s", exc)
        self.show()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    video = Video()
    display = UIVideoDisplay(video)
    player = UIVideoPlayer(display, video, resource_path(os.path.join("Imagys_blue", "freezed_mouse-4-300x400.jpg")))
    window = PyqtgWindow()
    window.d1.addWidget(display)
    window.d9.addWidget(player)

    player.enable_arrow_keys()  # left / right arrows = previous / next frame

    code = app.exec_()
    log.info("quit app ...")
    if video.grabber is not None:
        video.grabber.stop(wait=True)
    if video.capture is not None:
        video.capture.release()
    return code


if __name__ == "__main__":
    sys.exit(main())
