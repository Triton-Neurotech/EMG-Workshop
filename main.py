"""
main.py — EMG BCI Workshop · PyQt6 + pyqtgraph
Triton Neurotech · MindRove 4-Channel Armband / OpenBCI Ganglion

Usage:
  python main.py                 auto: LSL → OpenBCI UDP → MindRove SDK → synthetic
  python main.py --ganglion      Ganglion direct over Bluetooth (close the OpenBCI GUI first)
  python main.py --backend udp   OpenBCI GUI Networking widget, UDP/TimeSeries
  python main.py --synthetic     no hardware (fake flexes every few seconds)
  python main.py --keyboard      no hardware; hold SPACE to "flex" — for testing UI and game mechanics

Keys:
  C         calibrate
  G         jump game
  M         main menu
  1-8       select active EMG channel
  SPACE     manual trigger (menu/game)
  ESC       quit
"""

import os, sys, time, math, threading, argparse
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QFrame, QSlider, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath,
    QKeyEvent, QFontDatabase, QLinearGradient, QRadialGradient,
)
import pyqtgraph as pg

from emg_input import EMGInput
from signal_processing import EMGProcessor
from calibration import Calibration
from controller import TriggerController
from game import JumpGameLogic, Player, Obstacle, Bird, Scenery, ERAS, GAME_W, GAME_H, GROUND_Y

# ── pyqtgraph global config ───────────────────────────────────────────────────
pg.setConfigOptions(antialias=True, useOpenGL=False)

# ── Window / timing ───────────────────────────────────────────────────────────
WIN_W, WIN_H    = 1100, 720
TICK_MS         = 16          # ~60 fps update
CAL_DURATION_S  = 10
DISPLAY_SECS_UI = 2.0
DISABLED_CHANNELS: set[int] = set()

# ── Colour palette: "arctic pearl" ───────────────────────────────────────────
# Cool pearl neutrals, one saturated arctic-blue accent for anything that means
# "trigger", slate for signal traces and text.
C_BG         = QColor(0xEE, 0xF3, 0xF8)
C_PANEL      = QColor(0xFF, 0xFF, 0xFF)
C_PANEL2     = QColor(0xDD, 0xE6, 0xEF)
C_BORDER     = QColor(0xC9, 0xD5, 0xE2)
C_TEXT       = QColor(0x1E, 0x2A, 0x38)
C_DIM        = QColor(0x66, 0x77, 0x8B)
C_ACCENT     = QColor(0x2E, 0x96, 0xD6)
C_SIGNAL     = QColor(0x2B, 0x3F, 0x55)
C_SIGNAL_DIM = QColor(0xB5, 0xC4, 0xD3)
C_GREEN      = QColor(0x2F, 0xAF, 0x77)
C_RED        = QColor(0xE0, 0x4E, 0x4E)
C_YELLOW     = C_ACCENT
C_BRAIN      = QColor(0xF2, 0xB3, 0x3D)

# Game: pastel sunrise
G_SKY        = [(0.00, QColor(0xA9, 0xC4, 0xF2)), (0.50, QColor(0xE9, 0xC7, 0xDF)),
                (0.82, QColor(0xFA, 0xCF, 0xBE)), (1.00, QColor(0xFC, 0xE4, 0xC0))]
G_SUN        = QColor(0xFF, 0xE7, 0xA8)
G_SUN_GLOW   = QColor(0xFF, 0xD9, 0x8E)
G_GROUND     = QColor(0xEA, 0xE0, 0xF5)
G_GROUND_LN  = QColor(0x9A, 0x8C, 0xC4)
G_TICK       = QColor(0xC9, 0xBD, 0xE4)
G_CLOUD      = QColor(0xFF, 0xFF, 0xFF, 215)
G_OBST       = QColor(0x5F, 0x7D, 0x90)
G_OVERLAY    = QColor(0xFF, 0xFF, 0xFF, 150)

FONT_UI   = "Manrope"        # bundled in ./fonts, loaded by load_fonts()
FONT_MONO = "DM Mono"
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def load_fonts():
    """Register the bundled fonts; fall back to system fonts if they're missing."""
    global FONT_UI, FONT_MONO
    ok_ui = ok_mono = False
    if os.path.isdir(FONTS_DIR):
        for name in sorted(os.listdir(FONTS_DIR)):
            if not name.lower().endswith(".ttf"):
                continue
            fid = QFontDatabase.addApplicationFont(os.path.join(FONTS_DIR, name))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            ok_ui   |= "Manrope" in fams
            ok_mono |= "DM Mono" in fams
    fams = set(QFontDatabase.families())
    if not ok_ui:
        FONT_UI = next((f for f in ("Avenir Next", "Segoe UI", "Helvetica Neue", "DejaVu Sans") if f in fams), "")
    if not ok_mono:
        FONT_MONO = next((f for f in ("Menlo", "Consolas", "DejaVu Sans Mono") if f in fams), "")


# ── Stylesheet ────────────────────────────────────────────────────────────────
STYLESHEET = """
* { font-family: "__UI__", "Avenir Next", "Helvetica Neue", Arial, sans-serif; }
QMainWindow, QWidget { background-color: #EEF3F8; color: #1E2A38; }
QLabel { background: transparent; color: #1E2A38; }
QPushButton {
    background-color: #FFFFFF;
    border: 1px solid #C9D5E2;
    border-radius: 7px;
    color: #1E2A38;
    padding: 7px 16px;
    min-height: 26px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover   { background-color: #F6F9FC; border-color: #8FA3B8; }
QPushButton:pressed { background-color: #DDE6EF; }
QPushButton:checked { background-color: #2E96D6; border-color: #2E96D6; color: #FFFFFF; }
QPushButton[primary="true"] {
    background-color: #2E96D6; border: 1px solid #2E96D6; color: #FFFFFF;
}
QPushButton[primary="true"]:hover { background-color: #4FAAE3; border-color: #4FAAE3; }
QPushButton[nav="true"] { min-height: 52px; font-size: 14px; }
QLineEdit {
    background-color: #FFFFFF; border: 1px solid #C9D5E2; border-radius: 7px;
    color: #1E2A38; padding: 8px 14px; font-size: 17px;
    selection-background-color: #2E96D6;
}
QLineEdit:focus { border-color: #2E96D6; }
QSlider::groove:horizontal { height: 4px; background: #C9D5E2; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 18px; height: 18px; margin: -7px 0; background: #2E96D6; border-radius: 9px;
}
QSlider::sub-page:horizontal { background: #2E96D6; border-radius: 2px; }
"""


def build_stylesheet() -> str:
    return STYLESHEET.replace("__UI__", FONT_UI)

# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background: #C9D5E2; border: none;")
    return f


def _lbl(text: str, size: int = 13, bold: bool = False,
         color: QColor | None = None) -> QLabel:
    w = QLabel(text)
    f = QFont(FONT_UI, size)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    w.setFont(f)
    if color:
        pal = w.palette()
        pal.setColor(w.foregroundRole(), color)
        w.setPalette(pal)
    return w


def _btn(text: str, primary: bool = False, nav: bool = False,
         width: int | None = None) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", "true")
    if nav:
        b.setProperty("nav", "true")
    if width:
        b.setFixedWidth(width)
    return b


def _set_primary(b: QPushButton, primary: bool):
    if (b.property("primary") == "true") == primary:
        return
    b.setProperty("primary", "true" if primary else "false")
    b.style().unpolish(b)
    b.style().polish(b)


def probe_channel_metrics(emg: EMGInput, settle_s: float = 0.5,
                          window_s: float = 0.6) -> list[tuple[float, float]]:
    """Return (mean_abs, p95_abs) per channel from a short rest window."""
    time.sleep(settle_s)
    n = max(64, int(emg.sampling_rate * window_s))
    disp = emg.peek(n)
    metrics: list[tuple[float, float]] = []
    for ch in range(emg.n_channels):
        if disp.shape[1] == 0:
            metrics.append((0.0, 0.0))
            continue
        sig = np.asarray(disp[ch], dtype=float)
        dc = float(np.mean(sig))
        abs_sig = np.abs(sig - dc)
        metrics.append((float(np.mean(abs_sig)), float(np.percentile(abs_sig, 95))))
    return metrics


def choose_default_channel(metrics: list[tuple[float, float]], n_ch: int) -> int:  # noqa: ARG001
    reasonable = [(i, p) for i, (_, p) in enumerate(metrics) if 1.0 <= p <= 60.0]
    if reasonable:
        return min(reasonable, key=lambda x: x[1])[0]
    nonflat = [(i, p) for i, (_, p) in enumerate(metrics) if p > 1.0]
    if nonflat:
        return min(nonflat, key=lambda x: x[1])[0]
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Shared Widgets
# ─────────────────────────────────────────────────────────────────────────────

class EMGMultiPlot(QWidget):
    """
    One row per channel: a label column (name + scale) and a trace.
    Active channel: bright line with a soft fill and its threshold.
    Others: dim line. Y-scale auto-ranges per channel, smoothed.
    """

    def __init__(self, n_channels: int = 4, labels: list[str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.n_channels = n_channels
        labels = list(labels) if labels else [f"CH{i + 1}" for i in range(n_channels)]

        self._plots:     list[pg.PlotItem]     = []
        self._curves:    list[pg.PlotDataItem] = []
        self._envs:      list[tuple]           = []
        self._thr_lines: list[pg.InfiniteLine] = []
        self._name_lbls: list[QLabel]          = []
        self._scale_lbls: list[QLabel]         = []
        self._scale:     list[float]           = [60.0] * n_channels
        self._applied:   list[float]           = [0.0] * n_channels
        self._active_prev: int                 = -1
        self._n_prev: int                      = -1

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        for i in range(n_channels):
            row = QHBoxLayout()
            row.setContentsMargins(0, 6, 0, 6)
            row.setSpacing(14)

            col = QVBoxLayout()
            col.setContentsMargins(0, 2, 0, 0)
            col.setSpacing(2)
            name = _lbl(labels[i], 12, color=C_DIM)
            name.setFont(QFont(FONT_MONO, 12))
            scale = _lbl("", 9, color=C_DIM)
            scale.setFont(QFont(FONT_MONO, 9))
            col.addWidget(name)
            col.addWidget(scale)
            col.addStretch()
            colw = QWidget()
            colw.setLayout(col)
            colw.setFixedWidth(76)
            row.addWidget(colw)

            pw = pg.PlotWidget(background=C_BG)
            pw.setFrameStyle(0)
            p = pw.getPlotItem()
            p.setMouseEnabled(False, False)
            p.setMenuEnabled(False)
            p.hideButtons()
            p.hideAxis('bottom')
            p.hideAxis('left')
            p.setDefaultPadding(0.0)
            p.setContentsMargins(0, 0, 0, 0)
            p.getViewBox().setBackgroundColor(C_BG)

            p.addLine(y=0, pen=pg.mkPen(C_BORDER, width=1))
            raw = p.plot(pen=pg.mkPen(C_SIGNAL_DIM, width=1))
            raw.setClipToView(True)
            env_up = p.plot(pen=pg.mkPen(C_SIGNAL, width=1.4))
            env_dn = p.plot(pen=pg.mkPen(C_SIGNAL, width=1.4))
            fill = pg.FillBetweenItem(env_up, env_dn, brush=pg.mkBrush(43, 63, 85, 40))
            p.addItem(fill)
            curve = raw
            thr_line = p.addLine(
                y=0, pen=pg.mkPen(C_ACCENT, width=1, style=Qt.PenStyle.DashLine)
            )
            thr_line.setVisible(False)
            row.addWidget(pw, stretch=1)

            v.addLayout(row, stretch=1)
            if i < n_channels - 1:
                v.addWidget(_sep())

            self._plots.append(p)
            self._curves.append(curve)
            self._envs.append((env_up, env_dn, fill))
            self._thr_lines.append(thr_line)
            self._name_lbls.append(name)
            self._scale_lbls.append(scale)

    def _style_curve(self, i: int, active: bool):
        raw = self._curves[i]
        env_up, env_dn, fill = self._envs[i]
        if active:
            raw.setPen(pg.mkPen(QColor(0x2B, 0x3F, 0x55, 38), width=1))
            env_up.setPen(pg.mkPen(C_SIGNAL, width=1.4))
            env_dn.setPen(pg.mkPen(C_SIGNAL, width=1.4))
            fill.setBrush(pg.mkBrush(46, 150, 214, 55))
            self._name_lbls[i].setStyleSheet("color: #1E2A38;")
        else:
            raw.setPen(pg.mkPen(QColor(0xB5, 0xC4, 0xD3, 70), width=1))
            env_up.setPen(pg.mkPen(C_SIGNAL_DIM, width=1))
            env_dn.setPen(pg.mkPen(C_SIGNAL_DIM, width=1))
            fill.setBrush(pg.mkBrush(181, 196, 211, 50))
            self._name_lbls[i].setStyleSheet("color: #66778B;")
            self._scale_lbls[i].setText("")

    def update_data(self, data: np.ndarray, active_ch: int,
                    threshold: float = 0.0):
        n_avail = min(self.n_channels, data.shape[0])
        if active_ch != self._active_prev:
            for i in range(self.n_channels):
                self._style_curve(i, i == active_ch)
            self._active_prev = active_ch

        n = data.shape[1]
        if n != self._n_prev and n > 1:
            for p in self._plots:
                p.setXRange(0, n - 1, padding=0)
            self._n_prev = n

        for i in range(self.n_channels):
            p, curve, thr_line = self._plots[i], self._curves[i], self._thr_lines[i]
            is_active = (i == active_ch)

            if i >= n_avail or n == 0:
                curve.setData([])
                thr_line.setVisible(False)
                continue

            sig = data[i] - float(np.mean(data[i]))

            # Smoothed rectified envelope (same ~90 ms window the trigger uses).
            # The window length is derived from the trace length (DISPLAY_SECS_UI).
            k   = max(3, int(n / DISPLAY_SECS_UI * 0.09))
            env = np.convolve(np.abs(sig), np.ones(k) / k, mode='same')

            peak   = float(np.percentile(env, 99)) if n > 10 else 0.0
            target = max(15.0, peak * 2.2)
            if is_active and threshold > 0:
                target = max(target, threshold * 1.5)
            cur = self._scale[i]
            cur += (target - cur) * (0.12 if target > cur else 0.02)
            self._scale[i] = cur

            curve.setData(sig, skipFiniteCheck=True)
            env_up, env_dn, _ = self._envs[i]
            env_up.setData(env, skipFiniteCheck=True)
            env_dn.setData(-env, skipFiniteCheck=True)
            if abs(cur - self._applied[i]) > cur * 0.015:
                p.setYRange(-cur, cur, padding=0)
                self._applied[i] = cur
                if is_active:
                    self._scale_lbls[i].setText(f"±{cur:.0f} µV")

            show_thr = is_active and threshold > 0 and threshold < cur * 0.98
            if show_thr:
                thr_line.setValue(threshold)
            if thr_line.isVisible() != show_thr:
                thr_line.setVisible(show_thr)


class ActivationBarWidget(QWidget):
    """Thin horizontal activation meter with threshold tick and µV readout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._activation = 0.0
        self._threshold  = 50.0
        self._max_v      = 100.0
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, activation: float, threshold: float):
        if self._activation != activation or self._threshold != threshold:
            self._activation = activation
            self._threshold  = threshold
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a, thr = self._activation, self._threshold
        is_act = a >= thr

        text_w = 84
        bar_w  = self.width() - text_w
        bar_h  = 6.0
        by     = (self.height() - bar_h) / 2

        target = max(thr * 2.2, a * 1.1, 40.0)
        self._max_v += (target - self._max_v) * (0.3 if target > self._max_v else 0.03)
        max_v = self._max_v

        track = QPainterPath()
        track.addRoundedRect(QRectF(0, by, bar_w, bar_h), 3, 3)
        p.fillPath(track, C_PANEL2)

        fill_w = bar_w * min(1.0, a / max_v)
        if fill_w > 2:
            fp = QPainterPath()
            fp.addRoundedRect(QRectF(0, by, fill_w, bar_h), 3, 3)
            p.fillPath(fp, C_ACCENT if is_act else C_SIGNAL)

        if thr > 0:
            tx = bar_w * min(1.0, thr / max_v)
            p.setPen(QPen(C_ACCENT, 2))
            p.drawLine(QPointF(tx, by - 5), QPointF(tx, by + bar_h + 5))

        p.setFont(QFont(FONT_MONO, 11))
        p.setPen(QPen(C_TEXT if is_act else C_DIM, 1))
        p.drawText(QRectF(bar_w + 12, 0, text_w - 12, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{a:5.1f} µV")
        p.end()


class ChannelSelectorWidget(QWidget):
    """Row of checkable channel chips — one per channel the source exposes."""
    channel_changed = pyqtSignal(int)

    def __init__(self, n_channels: int, disabled: set[int] | None = None,
                 labels: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.n_channels = n_channels
        self._disabled  = set(disabled or set())
        self._buttons: list[QPushButton] = []
        labels = list(labels) if labels else [f"CH{i + 1}" for i in range(n_channels)]

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(_lbl("CHANNEL", 10, color=C_DIM))
        row.addSpacing(4)

        for i in range(n_channels):
            btn = QPushButton(labels[i])
            btn.setCheckable(True)
            btn.setFixedSize(50, 24)
            btn.setEnabled(i not in self._disabled)
            btn.setFont(QFont(FONT_MONO, 10))
            btn.setStyleSheet(
                "QPushButton { padding: 0; min-height: 0; font-size: 11px; color: #66778B; }"
                "QPushButton:checked { color: #FFFFFF; }"
                "QPushButton:hover:!checked { color: #1E2A38; }"
            )
            btn.clicked.connect(lambda _, idx=i: self.channel_changed.emit(idx))
            self._buttons.append(btn)
            row.addWidget(btn)

        if n_channels == 1:
            row.addSpacing(6)
            row.addWidget(_lbl("single channel", 10, color=C_DIM))
        row.addStretch()

    def set_active(self, ch: int):
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == ch)


# ─────────────────────────────────────────────────────────────────────────────
# Page: Main Menu
# ─────────────────────────────────────────────────────────────────────────────

class NavCard(QFrame):
    """Clickable card: title, one-line description, keyboard key badge."""
    clicked = pyqtSignal()

    def __init__(self, title: str, subtitle: str, key: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("navcard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(74)
        self._primary = None

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 0, 16, 0)
        row.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self._title = _lbl(title, 15, bold=True)
        self._sub   = _lbl(subtitle, 11, color=C_DIM)
        col.addWidget(self._title)
        col.addWidget(self._sub)
        row.addLayout(col, stretch=1)

        self._arrow = QLabel("→")
        self._arrow.setFont(QFont(FONT_UI, 16))
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow.setFixedSize(30, 30)
        row.addWidget(self._arrow)

        self.set_primary(False)

    def set_primary(self, primary: bool):
        if self._primary == primary:
            return
        self._primary = primary
        border = "#2E96D6" if primary else "#C9D5E2"
        hover  = "#4FAAE3" if primary else "#66778B"
        self.setStyleSheet(
            f"QFrame#navcard {{ background:#FFFFFF; border:1px solid {border}; border-radius:8px; }}"
            f"QFrame#navcard:hover {{ border-color:{hover}; }}"
            "QLabel { border: none; background: transparent; }"
        )
        self._arrow.setStyleSheet(f"color: {'#2E96D6' if primary else '#66778B'};")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class MenuPage(QWidget):
    nav_calibrate   = pyqtSignal()
    nav_game        = pyqtSignal()
    channel_changed = pyqtSignal(int)

    def __init__(self, emg: EMGInput, ctrl: TriggerController,
                 cal: Calibration, parent=None):
        super().__init__(parent)
        self._emg  = emg
        self._ctrl = ctrl
        self._cal  = cal
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(12)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.addWidget(_lbl("EMG Workshop", 22, bold=True))
        title_col.addWidget(_lbl("Triton Neurotech", 11, color=C_DIM))
        hdr.addLayout(title_col)
        hdr.addStretch()
        self._status_lbl = _lbl("● connecting", 11, color=C_DIM)
        self._status_lbl.setFont(QFont(FONT_MONO, 11))
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hdr.addWidget(self._status_lbl)
        root.addLayout(hdr)
        root.addSpacing(4)

        # ── Channel chips + flex pill ───────────────────────────────────────
        top = QHBoxLayout()
        self._ch_sel = ChannelSelectorWidget(
            self._emg.n_channels, DISABLED_CHANNELS, self._emg.channel_labels
        )
        self._ch_sel.channel_changed.connect(self.channel_changed)
        top.addWidget(self._ch_sel, stretch=1)
        self._pill = _lbl("rest", 11)
        self._pill.setFont(QFont(FONT_MONO, 11))
        self._pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pill.setFixedSize(72, 24)
        top.addWidget(self._pill)
        root.addLayout(top)

        # ── Live EMG plot ───────────────────────────────────────────────────
        self._plot = EMGMultiPlot(self._emg.n_channels, self._emg.channel_labels)
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        root.addWidget(self._plot, stretch=3)

        # ── Activation row ──────────────────────────────────────────────────
        act_row = QHBoxLayout()
        act_row.setSpacing(14)
        act_lbl = _lbl("ACTIVATION", 10, color=C_DIM)
        act_lbl.setFixedWidth(76)
        act_row.addWidget(act_lbl)
        self._act_bar = ActivationBarWidget()
        act_row.addWidget(self._act_bar, stretch=1)
        root.addLayout(act_row)

        root.addSpacing(6)
        root.addWidget(_sep())
        root.addSpacing(2)

        # ── Calibration status + nav cards ──────────────────────────────────
        self._cal_lbl = _lbl("Not calibrated — run calibration before playing",
                             12, color=C_DIM)
        root.addWidget(self._cal_lbl)

        nav = QHBoxLayout()
        nav.setSpacing(12)
        self._cal_card  = NavCard("Calibrate", "Record rest and flex, then set the threshold")
        self._game_card = NavCard("Jump game", "Flex to jump over cacti and birds")
        self._cal_card.clicked.connect(self.nav_calibrate)
        self._game_card.clicked.connect(self.nav_game)
        nav.addWidget(self._cal_card)
        nav.addWidget(self._game_card)
        root.addLayout(nav)

        self._set_pill(False)
        self._mode_note: str = ""

    def set_mode_note(self, text: str):
        self._mode_note = text

    def _set_pill(self, active: bool):
        if active:
            self._pill.setText("FLEX")
            self._pill.setStyleSheet(
                "border-radius:4px; background:#2E96D6; color:#FFFFFF; font-weight:600;")
        else:
            self._pill.setText("rest")
            self._pill.setStyleSheet(
                "border-radius:4px; background:transparent; color:#66778B; border:1px solid #C9D5E2;")

    def update_frame(self, activation: float, active_ch: int):
        n    = max(90, int(self._emg.sampling_rate * DISPLAY_SECS_UI))
        disp = self._emg.peek_smooth(n)
        self._plot.update_data(disp, active_ch, self._ctrl.threshold)
        self._act_bar.set_values(activation, self._ctrl.threshold)
        self._ch_sel.set_active(active_ch)

        is_act = activation >= self._ctrl.threshold
        if is_act != (self._pill.text() == "FLEX"):
            self._set_pill(is_act)

        calibrated = self._cal.is_calibrated()
        if calibrated:
            txt = f"Calibrated   ·   {self._cal.summary()}"
            col = "#2FAF77"
        else:
            txt = "Not calibrated — run calibration before playing"
            col = "#66778B"
        if self._cal_lbl.text() != txt:
            self._cal_lbl.setText(txt)
            self._cal_lbl.setStyleSheet(f"color: {col};")
        self._cal_card.set_primary(not calibrated)
        self._game_card.set_primary(calibrated)

        bk = self._emg.backend
        if self._mode_note:
            txt, col = f"● {self._mode_note}", "#2E96D6"
        elif bk == 'synthetic':
            txt, col = "● synthetic signal — no device found", "#E04E4E"
        elif not self._emg.is_alive():
            txt, col = f"● {self._emg.source_name[:30]} — NO DATA", "#E04E4E"
        else:
            txt, col = f"● {self._emg.source_name[:34]}  {self._emg.sampling_rate} Hz", "#2FAF77"
        if self._status_lbl.text() != txt:
            self._status_lbl.setText(txt)
            self._status_lbl.setStyleSheet(f"color: {col};")


# ─────────────────────────────────────────────────────────────────────────────
# Page: Calibration
# ─────────────────────────────────────────────────────────────────────────────

class CalibrationPage(QWidget):
    nav_back     = pyqtSignal()
    nav_game     = pyqtSignal()
    channel_changed = pyqtSignal(int)

    STEPS = ["Instructions", "Record REST", "Record FLEX", "Review Threshold"]
    SLIDER_MIN, SLIDER_MAX = 5.0, 600.0

    def __init__(self, emg: EMGInput, proc: EMGProcessor,
                 cal: Calibration, ctrl: TriggerController, parent=None):
        super().__init__(parent)
        self._emg  = emg
        self._proc = proc
        self._cal  = cal
        self._ctrl = ctrl

        self.step       = 0
        self._recording = False
        self._progress  = 0.0
        self._kind      = ""
        self._frame     = 0

        self._build()

    def reset(self):
        self.step       = 0
        self._recording = False
        self._progress  = 0.0
        self._kind      = ""
        self._update_step_view()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 14)
        root.setSpacing(10)

        # ── Top bar ─────────────────────────────────────────────────────────
        top = QHBoxLayout()
        back_btn = _btn("← Menu", width=90)
        back_btn.clicked.connect(self.nav_back)
        top.addWidget(back_btn)
        top.addSpacing(12)
        top.addWidget(_lbl("Calibration", 19, bold=True))
        top.addStretch()
        self._step_lbl = _lbl("Step 1 / 4", 12, color=C_DIM)
        top.addWidget(self._step_lbl)
        root.addLayout(top)

        # Step progress dots
        dots_row = QHBoxLayout()
        dots_row.addStretch()
        self._step_dots: list[QLabel] = []
        for _ in range(4):
            d = QLabel("●")
            d.setFixedSize(22, 22)
            d.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dots_row.addWidget(d)
            self._step_dots.append(d)
        dots_row.addStretch()
        root.addLayout(dots_row)

        root.addWidget(_sep())

        # ── Channel selector ─────────────────────────────────────────────────
        self._ch_sel = ChannelSelectorWidget(
            self._emg.n_channels, DISABLED_CHANNELS, self._emg.channel_labels
        )
        self._ch_sel.channel_changed.connect(self.channel_changed)
        root.addWidget(self._ch_sel)

        # ── Live EMG plot ────────────────────────────────────────────────────
        self._plot = EMGMultiPlot(self._emg.n_channels, self._emg.channel_labels)
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        root.addWidget(self._plot, stretch=2)

        # ── Activation bar ───────────────────────────────────────────────────
        act_row = QHBoxLayout()
        act_row.addWidget(_lbl("ACTIVATION", 10, color=C_DIM))
        act_row.addSpacing(8)
        self._act_bar = ActivationBarWidget()
        act_row.addWidget(self._act_bar, stretch=1)
        root.addLayout(act_row)

        root.addWidget(_sep())

        # ── Step content area ────────────────────────────────────────────────
        self._step_stack = QStackedWidget()
        root.addWidget(self._step_stack, stretch=2)

        self._build_step0()
        self._build_step1()
        self._build_step2()
        self._build_step3()

        self._update_step_view()

    # ── Step 0: Instructions ─────────────────────────────────────────────────
    def _build_step0(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(10)

        v.addWidget(_lbl("How calibration works", 15, bold=True,
                         color=C_TEXT))
        for line in [
            f"1   Relax your arm completely for {CAL_DURATION_S} s",
            f"2   Flex your forearm firmly for {CAL_DURATION_S} s",
            "3   Review and adjust the activation threshold",
        ]:
            v.addWidget(_lbl(f"   {line}", 12, color=C_DIM))

        v.addSpacing(8)
        start_btn = _btn("Start calibration", primary=True, nav=True, width=300)
        start_btn.clicked.connect(self._space_pressed)
        v.addWidget(start_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        self._step_stack.addWidget(w)

    # ── Step 1: REST recording ───────────────────────────────────────────────
    def _build_step1(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(10)

        self._step1_heading = _lbl("Relax your arm", 20, bold=True,
                                    color=C_RED)
        self._step1_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._step1_heading)

        self._step1_sub = _lbl(
            "Keep your forearm completely still and relaxed", 12, color=C_DIM
        )
        self._step1_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._step1_sub)

        v.addSpacing(6)

        # Progress bar (custom drawn)
        self._rest_prog = _ProgressBar()
        self._rest_prog.setFixedHeight(28)
        v.addWidget(self._rest_prog)

        self._step1_status = _lbl("Ready — click Record rest",
                                   12, color=C_ACCENT)
        self._step1_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._step1_status)

        start_btn = _btn("Record rest", primary=True, width=240)
        start_btn.clicked.connect(self._space_pressed)
        v.addWidget(start_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._step_stack.addWidget(w)

    # ── Step 2: FLEX recording ───────────────────────────────────────────────
    def _build_step2(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(10)

        self._step2_heading = _lbl("Flex your arm", 20, bold=True,
                                    color=C_GREEN)
        self._step2_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._step2_heading)

        self._step2_sub = _lbl(
            "Contract your forearm muscle and hold it firmly", 12, color=C_DIM
        )
        self._step2_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._step2_sub)

        v.addSpacing(6)

        self._flex_prog = _ProgressBar(color=C_GREEN)
        self._flex_prog.setFixedHeight(28)
        v.addWidget(self._flex_prog)

        self._step2_status = _lbl("Ready — click Record flex",
                                   12, color=C_ACCENT)
        self._step2_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._step2_status)

        start_btn = _btn("Record flex", primary=True, width=240)
        start_btn.clicked.connect(self._space_pressed)
        v.addWidget(start_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._step_stack.addWidget(w)

    # ── Step 3: Threshold review ─────────────────────────────────────────────
    def _build_step3(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignTop)
        v.setSpacing(8)
        v.setContentsMargins(20, 10, 20, 10)

        thr_row = QHBoxLayout()
        self._thr_val_lbl = _lbl("Threshold:  50.0 µV", 13, bold=True)
        thr_row.addWidget(self._thr_val_lbl)
        thr_row.addStretch()

        self._trigger_badge = _lbl("not triggered", 12)
        self._trigger_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._trigger_badge.setFixedSize(124, 26)
        self._trigger_badge.setStyleSheet(
            "border-radius:13px; background:#DDE6EF; color:#66778B;"
            "border:1px solid #C9D5E2;"
        )
        thr_row.addWidget(self._trigger_badge)
        v.addLayout(thr_row)

        self._thr_slider = QSlider(Qt.Orientation.Horizontal)
        self._thr_slider.setMinimum(int(self.SLIDER_MIN * 10))
        self._thr_slider.setMaximum(int(self.SLIDER_MAX * 10))
        self._thr_slider.setValue(500)   # 50.0
        self._thr_slider.valueChanged.connect(self._on_slider)
        v.addWidget(self._thr_slider)

        # REST / FLEX marker labels below slider
        markers = QHBoxLayout()
        self._rest_marker = _lbl("REST: —", 10, color=C_DIM)
        self._flex_marker = _lbl("FLEX: —", 10, color=C_GREEN)
        markers.addWidget(self._rest_marker)
        markers.addStretch()
        markers.addWidget(self._flex_marker)
        v.addLayout(markers)

        self._cal_summary = _lbl("", 11, color=C_DIM)
        self._cal_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._cal_summary)

        self._quality_lbl = _lbl("", 11, color=C_GREEN)
        self._quality_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._quality_lbl)

        btn_row = QHBoxLayout()
        redo_btn = _btn("Redo flex")
        redo_btn.clicked.connect(self._space_pressed)
        game_btn = _btn("Jump game", primary=True)
        game_btn.clicked.connect(self.nav_game)
        btn_row.addWidget(redo_btn)
        btn_row.addWidget(game_btn)
        v.addLayout(btn_row)

        self._step_stack.addWidget(w)

    # ── Internal logic ───────────────────────────────────────────────────────

    def _on_slider(self, val: int):
        thr = val / 10.0
        self._cal.threshold = thr
        self._ctrl.set_threshold(thr)
        self._thr_val_lbl.setText(f"Threshold:  {thr:.1f} µV")

    def handle_space(self):
        self._space_pressed()

    def _space_pressed(self):
        if self._recording:
            return
        if self.step == 0:
            self._start_record('rest')
        elif self.step == 1:
            self._start_record('rest')
        elif self.step == 2:
            self._start_record('flex')
        elif self.step == 3:
            self._start_record('flex')   # redo flex

    def _start_record(self, kind: str):
        if kind == 'rest':
            self.step = 1
            self._cal.reset()
            self._ctrl.reset()
        else:
            self.step = 2

        self._recording = True
        self._progress  = 0.0
        self._kind      = kind
        self._update_step_view()

        smooth_samples = self._proc.smooth_samples
        ch = self._proc.channel

        def do_record():
            start = time.monotonic()
            activations = []
            while time.monotonic() - start < CAL_DURATION_S:
                disp = self._emg.peek(smooth_samples)
                act  = EMGProcessor.compute_activation_from_window(
                    disp, ch, smooth_samples
                )
                activations.append(act)
                self._progress = min(
                    1.0, (time.monotonic() - start) / CAL_DURATION_S
                )
                time.sleep(0.04)

            if kind == 'rest':
                self._cal.record_rest(np.array(activations))
                self.step = 2
            else:
                self._cal.record_flex(np.array(activations))
                self._cal.compute_threshold(sensitivity=0.45)
                self._ctrl.set_threshold(self._cal.threshold)
                self.step = 3

            self._recording = False
            self._progress  = 1.0

        threading.Thread(target=do_record, daemon=True).start()

    def _update_step_view(self):
        # Step dots
        for i, d in enumerate(self._step_dots):
            if i < self.step:
                d.setStyleSheet("color: #2FAF77; font-size: 14px;")
            elif i == self.step:
                d.setStyleSheet("color: #2E96D6; font-size: 18px;")
            else:
                d.setStyleSheet("color: #C9D5E2; font-size: 14px;")
        label = self.STEPS[min(self.step, len(self.STEPS) - 1)]
        self._step_lbl.setText(
            f"Step {min(self.step + 1, 4)} / 4  —  {label}"
        )
        # Show the right step widget
        idx = min(self.step, self._step_stack.count() - 1)
        self._step_stack.setCurrentIndex(idx)

    def update_frame(self, activation: float, active_ch: int):
        self._frame += 1
        n    = max(90, int(self._emg.sampling_rate * DISPLAY_SECS_UI))
        disp = self._emg.peek_smooth(n)
        self._plot.update_data(disp, active_ch, 0.0)
        self._act_bar.set_values(activation, self._ctrl.threshold)
        self._ch_sel.set_active(active_ch)
        self._update_step_view()

        # Update recording progress bars
        if self.step == 1:
            if self._recording:
                self._rest_prog.set_progress(self._progress)
                secs = CAL_DURATION_S * (1 - self._progress)
                self._step1_status.setText(
                    f"Recording rest   {secs:.1f} s"
                )
                self._step1_status.setStyleSheet("color: #E04E4E;")
            else:
                self._rest_prog.set_progress(0.0)
                self._step1_status.setText("Ready — click Record rest")
                self._step1_status.setStyleSheet("color: #2E96D6;")

        elif self.step == 2:
            if self._recording:
                self._flex_prog.set_progress(self._progress)
                secs = CAL_DURATION_S * (1 - self._progress)
                self._step2_status.setText(
                    f"Recording flex   {secs:.1f} s"
                )
                self._step2_status.setStyleSheet("color: #2FAF77;")
            else:
                self._flex_prog.set_progress(0.0)
                self._step2_status.setText("Ready — click Record flex")
                self._step2_status.setStyleSheet("color: #2E96D6;")

        elif self.step == 3:
            thr = self._cal.threshold
            self._thr_slider.blockSignals(True)
            self._thr_slider.setValue(int(thr * 10))
            self._thr_slider.blockSignals(False)
            self._thr_val_lbl.setText(f"Threshold:  {thr:.1f} µV")

            is_act = activation >= thr
            if is_act:
                self._trigger_badge.setText("TRIGGERED")
                self._trigger_badge.setStyleSheet(
                    "border-radius:13px; background:#2FAF77; color:#FFFFFF;"
                    " font-weight:bold; border:none;"
                )
            else:
                self._trigger_badge.setText("not triggered")
                self._trigger_badge.setStyleSheet(
                    "border-radius:13px; background:#DDE6EF; color:#66778B;"
                    " border:1px solid #C9D5E2;"
                )

            if self._cal.is_calibrated():
                self._rest_marker.setText(
                    f"REST p99: {self._cal.rest_p99:.1f} µV"
                )
                self._flex_marker.setText(
                    f"FLEX p25: {self._cal.flex_p25:.1f} µV"
                )
                self._cal_summary.setText(self._cal.summary())
                if self._cal.quality_margin >= 8.0:
                    quality = "Good separation"
                else:
                    quality = ("Weak separation — press the electrodes firmly over the muscle "
                               "belly, re-wet them, clench hard, then redo flex")
                qc = "#2FAF77" if self._cal.quality_margin >= 8.0 else "#2E96D6"
                self._quality_lbl.setText(quality)
                self._quality_lbl.setStyleSheet(f"color: {qc};")


class _ProgressBar(QWidget):
    """Simple horizontal progress bar used in calibration steps."""

    def __init__(self, color: QColor = C_RED, parent=None):
        super().__init__(parent)
        self._color    = color
        self._progress = 0.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_progress(self, v: float):
        if self._progress != v:
            self._progress = v
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r    = QRectF(0, 0, w, h)
        rad  = h / 2
        bg   = QPainterPath()
        bg.addRoundedRect(r, rad, rad)
        p.fillPath(bg, C_PANEL2)
        if self._progress > 0.01:
            fp = QPainterPath()
            fp.addRoundedRect(QRectF(0, 0, w * self._progress, h), rad, rad)
            p.fillPath(fp, self._color)
        p.setPen(QPen(C_BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(bg)

        # Percentage text
        f2 = QFont()
        f2.setPointSize(10)
        p.setFont(f2)
        p.setPen(QPen(C_TEXT, 1))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                   f"{int(self._progress * 100)}%")
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
# Page: Jump Game
# ─────────────────────────────────────────────────────────────────────────────

class GameCanvas(QWidget):
    """Renders the jump game with QPainter, scaled to the widget size."""
    menu_clicked    = pyqtSignal()
    restart_clicked = pyqtSignal()

    BTN_W, BTN_H = 170, 44

    def __init__(self, logic: JumpGameLogic, parent=None):
        super().__init__(parent)
        self._logic      = logic
        self._activation = 0.0
        self._threshold  = 50.0
        self._max_v      = 100.0
        self._buttons: list[tuple[str, QRectF]] = []     # (action, rect in game coords)
        self._hover: str = ""
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMinimumSize(400, 300)

    def set_emg(self, activation: float, threshold: float):
        self._activation = activation
        self._threshold  = threshold

    # ── mouse ──────────────────────────────────────────────────────────────

    def _to_game(self, pos) -> QPointF:
        return QPointF(pos.x() * GAME_W / max(1, self.width()),
                       pos.y() * GAME_H / max(1, self.height()))

    def _hit(self, pos) -> str:
        gp = self._to_game(pos)
        for action, rect in self._buttons:
            if rect.contains(gp):
                return action
        return ""

    def mouseMoveEvent(self, event):
        h = self._hit(event.position())
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        action = self._hit(event.position())
        if action == "menu":
            self.menu_clicked.emit()
        elif action in ("restart", "start"):
            self.restart_clicked.emit()

    # ── drawing helpers ────────────────────────────────────────────────────

    @staticmethod
    def _font(size: int, mono: bool = False, bold: bool = False) -> QFont:
        f = QFont(FONT_MONO if mono else FONT_UI, size)
        f.setBold(bold)
        return f

    def _text_center(self, p: QPainter, text: str, cx: float, y: float):
        tw = p.fontMetrics().horizontalAdvance(text)
        p.drawText(QPointF(cx - tw / 2, y), text)

    def _button(self, p: QPainter, action: str, label: str, x: float, y: float,
                primary: bool = False):
        rect = QRectF(x, y, self.BTN_W, self.BTN_H)
        self._buttons.append((action, rect))
        hovered = self._hover == action
        path = QPainterPath()
        path.addRoundedRect(rect, 7, 7)
        if primary:
            p.fillPath(path, QColor(0x4F, 0xAA, 0xE3) if hovered else C_ACCENT)
            p.setPen(QPen(C_PANEL))
        else:
            p.fillPath(path, C_PANEL2 if hovered else C_PANEL)
            p.setPen(QPen(C_DIM if hovered else C_BORDER, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setPen(QPen(C_TEXT))
        p.setFont(self._font(14, bold=primary))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_cactus(self, p: QPainter, obs: Obstacle):
        stems = obs.stems
        gap   = 4
        sw    = (obs.w - gap * (stems - 1)) / stems
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(G_OBST))
        for k in range(stems):
            x = obs.x + k * (sw + gap)
            h = obs.h - (6 if (k % 2 == 1 and stems > 1) else 0)
            y = GROUND_Y - h
            p.drawRoundedRect(QRectF(x, y, sw, h + 2), sw * 0.35, sw * 0.35)
            if h >= 34:
                arm_w = max(4.0, sw * 0.45)
                arm_h = h * 0.3
                ay    = y + h * 0.28
                p.drawRoundedRect(QRectF(x - arm_w - 1, ay, arm_w, arm_h), 2, 2)
                p.drawRoundedRect(QRectF(x - arm_w - 1, ay, arm_w + 3, arm_w), 2, 2)
                p.drawRoundedRect(QRectF(x + sw + 1, ay + 6, arm_w, arm_h), 2, 2)
                p.drawRoundedRect(QRectF(x + sw - 2, ay + 6, arm_w + 3, arm_w), 2, 2)

    def _draw_bird(self, p: QPainter, b: Bird):
        x, y, w, h = b.x, b.y, b.w, b.h
        cy = y + h * 0.6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(G_OBST))
        # body + beak (flying left)
        p.drawRoundedRect(QRectF(x + 8, cy - 6, w - 14, 12), 6, 6)
        beak = QPainterPath()
        beak.moveTo(x + 9, cy - 3); beak.lineTo(x, cy); beak.lineTo(x + 9, cy + 3)
        p.fillPath(beak, G_OBST)
        # wing: flaps between up and down
        wing = QPainterPath()
        wx = x + w * 0.55
        if b.wing_up:
            wing.moveTo(wx - 8, cy - 2); wing.lineTo(wx + 2, cy - h * 0.75); wing.lineTo(wx + 8, cy - 2)
        else:
            wing.moveTo(wx - 8, cy + 2); wing.lineTo(wx + 2, cy + h * 0.6); wing.lineTo(wx + 8, cy + 2)
        wing.closeSubpath()
        p.fillPath(wing, G_OBST)
        # tail
        tail = QPainterPath()
        tail.moveTo(x + w - 7, cy - 4); tail.lineTo(x + w + 2, cy - 7); tail.lineTo(x + w - 5, cy + 2)
        p.fillPath(tail, G_OBST)

    def _draw_player(self, p: QPainter, pl: Player):
        """A little brain with legs, facing right."""
        x, y, w, h = pl.x, pl.y, pl.W, pl.H
        p.setPen(Qt.PenStyle.NoPen)

        # legs (run cycle on the ground, tucked in the air)
        p.setBrush(QBrush(C_BRAIN))
        if pl.on_ground:
            phase = int(pl.run_phase) % 2
            l1 = 7 if phase == 0 else 3
            l2 = 3 if phase == 0 else 7
            p.drawRoundedRect(QRectF(x + 8,  y + h - 10, 7, 4 + l1), 2, 2)
            p.drawRoundedRect(QRectF(x + 20, y + h - 10, 7, 4 + l2), 2, 2)
        else:
            p.drawRoundedRect(QRectF(x + 8,  y + h - 10, 7, 7), 2, 2)
            p.drawRoundedRect(QRectF(x + 20, y + h - 10, 7, 7), 2, 2)

        # brain body: two lobes
        body = QRectF(x - 3, y + 4, w + 6, h - 16)
        p.setBrush(QBrush(C_BRAIN))
        p.drawEllipse(body)
        # cerebellum bump at the back-bottom
        p.drawEllipse(QRectF(x - 2, y + h - 22, 14, 10))

        # folds: midline + gyri, drawn in the background colour
        pen = QPen(QColor(0x9A, 0x62, 0x14), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx = x + w / 2
        mid = QPainterPath()
        mid.moveTo(cx, y + 5)
        mid.cubicTo(cx - 3, y + 14, cx + 3, y + 22, cx, y + h - 13)
        p.drawPath(mid)
        for (ax, ay, aw, ah, start) in [
            (x + 1,  y + 9,  12, 8,  20),  (x + 3,  y + 18, 12, 8, 200),
            (x + 4,  y + 26, 10, 7,  30),  (x + 19, y + 9,  12, 8,  20),
            (x + 18, y + 18, 12, 8, 200),  (x + 20, y + 26, 10, 7,  30),
        ]:
            p.drawArc(QRectF(ax, ay, aw, ah), start * 16, 160 * 16)

        # eye
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(C_TEXT))
        p.drawEllipse(QRectF(x + w - 9, y + 17, 6, 6))
        p.setBrush(QBrush(C_PANEL))
        p.drawEllipse(QRectF(x + w - 7, y + 18, 2.5, 2.5))

        # held-jump indicator
        if not pl.on_ground and pl.jump_held:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(C_ACCENT, 2))
            p.drawArc(QRectF(x + 4, y - 14, w - 8, 14), 20 * 16, 140 * 16)

    # ── background scenery (one drawing routine per era) ───────────────────

    SIL  = QColor(0x5F, 0x7D, 0x90, 95)     # distant silhouette
    SIL2 = QColor(0x5F, 0x7D, 0x90, 140)    # nearer / detail

    def _draw_scenery(self, p: QPainter, sc: Scenery):
        x, g = sc.x, GROUND_Y
        p.setPen(Qt.PenStyle.NoPen)
        era = sc.era
        if era == 0:        # Stone age: campfire, rocks, a standing stone
            p.setBrush(QBrush(self.SIL))
            p.drawRoundedRect(QRectF(x + 120, g - 70, 26, 70), 8, 8)       # menhir
            p.drawEllipse(QRectF(x - 10, g - 14, 30, 14))
            p.drawEllipse(QRectF(x + 70, g - 10, 22, 10))
            p.setBrush(QBrush(self.SIL2))
            p.drawRoundedRect(QRectF(x + 22, g - 9, 40, 6), 3, 3)          # logs
            p.drawRoundedRect(QRectF(x + 26, g - 14, 32, 6), 3, 3)
            fl = 1.0 + 0.25 * math.sin(sc.phase * 2.3)
            flame = QPainterPath()
            flame.moveTo(x + 30, g - 12); flame.quadTo(x + 34, g - 24 * fl, x + 42, g - 40 * fl)
            flame.quadTo(x + 50, g - 24 * fl, x + 54, g - 12); flame.closeSubpath()
            p.fillPath(flame, QColor(0xF6, 0x9A, 0x4B, 220))
            inner = QPainterPath()
            inner.moveTo(x + 36, g - 12); inner.quadTo(x + 40, g - 20 * fl, x + 42, g - 28 * fl)
            inner.quadTo(x + 46, g - 20 * fl, x + 48, g - 12); inner.closeSubpath()
            p.fillPath(inner, QColor(0xFF, 0xD7, 0x6B, 230))
        elif era == 1:      # First villages: thatched huts
            for k, (hx, hw, hh) in enumerate([(0, 56, 34), (74, 44, 28), (132, 62, 38)]):
                p.setBrush(QBrush(self.SIL if k != 1 else self.SIL2))
                p.drawRect(QRectF(x + hx, g - hh, hw, hh))
                roof = QPainterPath()
                roof.moveTo(x + hx - 8, g - hh); roof.lineTo(x + hx + hw / 2, g - hh - hw * 0.55)
                roof.lineTo(x + hx + hw + 8, g - hh); roof.closeSubpath()
                p.fillPath(roof, self.SIL2)
                p.setBrush(QBrush(QColor(0xFC, 0xE4, 0xC0, 200)))
                p.drawRoundedRect(QRectF(x + hx + hw / 2 - 5, g - 16, 10, 16), 4, 4)   # door
        elif era == 2:      # Ancient Egypt: pyramids
            for (px, pw, ph, col) in [(0, 150, 110, self.SIL), (120, 100, 72, self.SIL2), (210, 60, 44, self.SIL)]:
                tri = QPainterPath()
                tri.moveTo(x + px, g); tri.lineTo(x + px + pw / 2, g - ph); tri.lineTo(x + px + pw, g)
                tri.closeSubpath(); p.fillPath(tri, col)
        elif era == 3:      # Classical Rome: temple
            p.setBrush(QBrush(self.SIL2))
            p.drawRect(QRectF(x, g - 12, 170, 12))                                  # steps
            p.drawRect(QRectF(x + 6, g - 90, 158, 8))                              # architrave
            ped = QPainterPath()
            ped.moveTo(x - 2, g - 90); ped.lineTo(x + 85, g - 122); ped.lineTo(x + 172, g - 90); ped.closeSubpath()
            p.fillPath(ped, self.SIL2)
            p.setBrush(QBrush(self.SIL))
            for k in range(6):
                p.drawRoundedRect(QRectF(x + 14 + k * 28, g - 84, 10, 72), 3, 3)
        elif era == 4:      # Middle ages: castle
            p.setBrush(QBrush(self.SIL))
            p.drawRect(QRectF(x + 20, g - 60, 150, 60))                           # wall
            for k in range(7):
                p.drawRect(QRectF(x + 20 + k * 22, g - 70, 12, 10))              # crenellations
            p.setBrush(QBrush(self.SIL2))
            for tx in (x, x + 160):
                p.drawRect(QRectF(tx, g - 100, 30, 100))
                cone = QPainterPath()
                cone.moveTo(tx - 4, g - 100); cone.lineTo(tx + 15, g - 134); cone.lineTo(tx + 34, g - 100); cone.closeSubpath()
                p.fillPath(cone, self.SIL2)
            p.setBrush(QBrush(QColor(0xFC, 0xE4, 0xC0, 200)))
            p.drawRoundedRect(QRectF(x + 84, g - 30, 22, 30), 11, 11)             # gate
        elif era == 5:      # Industrial age: factory + chimneys + smoke
            p.setBrush(QBrush(self.SIL))
            p.drawRect(QRectF(x, g - 58, 180, 58))
            saw = QPainterPath()
            saw.moveTo(x, g - 58)
            for k in range(4):
                saw.lineTo(x + k * 45 + 30, g - 84); saw.lineTo(x + k * 45 + 45, g - 58)
            saw.closeSubpath(); p.fillPath(saw, self.SIL)
            p.setBrush(QBrush(self.SIL2))
            p.drawRect(QRectF(x + 120, g - 140, 14, 82))
            p.drawRect(QRectF(x + 150, g - 120, 12, 62))
            p.setBrush(QBrush(QColor(255, 255, 255, 150)))
            for k in range(3):
                t = (sc.phase * 0.5 + k * 0.33) % 1.0
                p.drawEllipse(QPointF(x + 127 + t * 18, g - 150 - t * 40), 8 + t * 8, 8 + t * 8)
        elif era == 6:      # Modern city: skyline with windows
            for k, (bx, bw, bh) in enumerate([(0, 36, 110), (42, 50, 160), (98, 30, 90), (134, 56, 200), (196, 40, 130)]):
                p.setBrush(QBrush(self.SIL if k % 2 else self.SIL2))
                p.drawRect(QRectF(x + bx, g - bh, bw, bh))
                p.setBrush(QBrush(QColor(0xFC, 0xE4, 0xC0, 170)))
                for r in range(int(bh // 22)):
                    for c in range(int(bw // 14)):
                        if (r + c + k) % 3:
                            p.drawRect(QRectF(x + bx + 5 + c * 14, g - bh + 8 + r * 22, 5, 8))
        else:               # Space age: rocket on its pad + dish
            p.setBrush(QBrush(self.SIL))
            p.drawRect(QRectF(x + 130, g - 150, 10, 150))                         # gantry
            for k in range(6):
                p.drawRect(QRectF(x + 118, g - 140 + k * 24, 22, 4))
            p.setBrush(QBrush(self.SIL2))
            p.drawRoundedRect(QRectF(x + 80, g - 120, 34, 120), 6, 6)             # rocket body
            nose = QPainterPath()
            nose.moveTo(x + 80, g - 120); nose.lineTo(x + 97, g - 156); nose.lineTo(x + 114, g - 120); nose.closeSubpath()
            p.fillPath(nose, self.SIL2)
            fin = QPainterPath()
            fin.moveTo(x + 80, g - 30); fin.lineTo(x + 66, g); fin.lineTo(x + 80, g); fin.closeSubpath()
            p.fillPath(fin, self.SIL2)
            fin2 = QPainterPath()
            fin2.moveTo(x + 114, g - 30); fin2.lineTo(x + 128, g); fin2.lineTo(x + 114, g); fin2.closeSubpath()
            p.fillPath(fin2, self.SIL2)
            p.setBrush(QBrush(self.SIL))
            p.drawRect(QRectF(x + 10, g - 40, 6, 40))                             # dish mast
            p.setPen(QPen(self.SIL2, 5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(x - 14, g - 70, 54, 40), 200 * 16, 140 * 16)
            p.setPen(Qt.PenStyle.NoPen)

    def _draw_era_banner(self, p: QPainter):
        lg = self._logic
        if lg.era_banner <= 0 or lg.state != 'playing':
            return
        alpha = min(1.0, lg.era_banner / 40.0)
        col = QColor(C_TEXT); col.setAlphaF(alpha)
        p.setPen(QPen(col))
        p.setFont(self._font(13, mono=True))
        self._text_center(p, f"ERA {lg.era + 1}", GAME_W / 2, 92)
        p.setFont(self._font(22, bold=True))
        self._text_center(p, ERAS[lg.era], GAME_W / 2, 124)

    def _draw_hud(self, p: QPainter):
        lg = self._logic
        p.setFont(self._font(15, mono=True))
        hi  = f"HI {lg.high_score:05d}"
        cur = f"{lg.score:05d}"
        fm  = p.fontMetrics()
        p.setPen(QPen(C_TEXT))
        p.drawText(QPointF(GAME_W - 24 - fm.horizontalAdvance(cur), 36), cur)
        p.setPen(QPen(C_DIM))
        p.drawText(QPointF(GAME_W - 24 - fm.horizontalAdvance(cur) - 18 - fm.horizontalAdvance(hi), 36), hi)
        if lg.state == 'playing':
            p.setFont(self._font(11))
            era_txt = f"era {lg.era + 1} · {ERAS[lg.era]}"
            p.drawText(QPointF(GAME_W - 24 - p.fontMetrics().horizontalAdvance(era_txt), 56), era_txt)

        # EMG meter, bottom-left
        a, thr = self._activation, self._threshold
        is_act = a >= thr
        target = max(thr * 2.2, a * 1.1, 40.0)
        self._max_v += (target - self._max_v) * (0.3 if target > self._max_v else 0.03)
        max_v = self._max_v

        bx, by, bw, bh = 24.0, GAME_H - 44.0, 240.0, 6.0
        track = QPainterPath(); track.addRoundedRect(QRectF(bx, by, bw, bh), 3, 3)
        p.fillPath(track, QColor(255, 255, 255, 170))
        fw = bw * min(1.0, a / max_v)
        if fw > 2:
            fp = QPainterPath(); fp.addRoundedRect(QRectF(bx, by, fw, bh), 3, 3)
            p.fillPath(fp, C_ACCENT if is_act else C_SIGNAL)
        if thr > 0:
            tx = bx + bw * min(1.0, thr / max_v)
            p.setPen(QPen(C_ACCENT, 2))
            p.drawLine(QPointF(tx, by - 5), QPointF(tx, by + bh + 5))

        p.setFont(self._font(10, mono=True))
        p.setPen(QPen(C_DIM))
        p.drawText(QPointF(bx, by - 10), "EMG")
        p.setPen(QPen(C_TEXT if is_act else C_DIM))
        p.drawText(QPointF(bx + bw + 12, by + bh + 1), f"{a:5.1f} µV")

        # top-left menu link (always clickable)
        p.setFont(self._font(12))
        rect = QRectF(20, 16, 90, 28)
        self._buttons.append(("menu", rect))
        p.setPen(QPen(C_TEXT if self._hover == "menu" else C_DIM))
        p.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "←  Menu")

    # ── paint ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        lg = self._logic
        self._buttons = []

        p.save()
        p.scale(self.width() / GAME_W, self.height() / GAME_H)

        # Sky: pastel sunrise gradient
        sky = QLinearGradient(0, 0, 0, GROUND_Y)
        for pos, col in G_SKY:
            sky.setColorAt(pos, col)
        p.fillRect(QRectF(0, 0, GAME_W, GROUND_Y + 2), QBrush(sky))

        # Sun with a soft glow, sitting low near the horizon
        sun_c = QPointF(GAME_W * 0.74, GROUND_Y - 78)
        glow = QRadialGradient(sun_c, 150)
        g0 = QColor(G_SUN_GLOW); g0.setAlpha(140)
        g1 = QColor(G_SUN_GLOW); g1.setAlpha(0)
        glow.setColorAt(0.0, g0); glow.setColorAt(1.0, g1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(sun_c, 150, 150)
        p.setBrush(QBrush(G_SUN))
        p.drawEllipse(sun_c, 46, 46)

        # Clouds (parallax)
        p.setBrush(QBrush(G_CLOUD))
        for cx, cy, _ in lg.clouds:
            p.drawRoundedRect(QRectF(cx, cy, 64, 12), 6, 6)
            p.drawRoundedRect(QRectF(cx + 14, cy - 9, 32, 14), 7, 7)

        # Ground: pastel fill, horizon line, scrolling ticks
        p.fillRect(QRectF(0, GROUND_Y, GAME_W, GAME_H - GROUND_Y), G_GROUND)
        p.setPen(QPen(G_GROUND_LN, 2))
        p.drawLine(QPointF(0, GROUND_Y + 1), QPointF(GAME_W, GROUND_Y + 1))
        p.setPen(QPen(G_TICK, 2))
        x = -lg.ground_offset
        k = 0
        while x < GAME_W + 48:
            seg = 14 if k % 2 == 0 else 6
            yy  = GROUND_Y + 16 if k % 3 == 0 else GROUND_Y + 24
            p.drawLine(QPointF(x, yy), QPointF(x + seg, yy))
            x += 48
            k += 1

        for sc in lg.scenery:
            self._draw_scenery(p, sc)
        for obs in lg.obstacles:
            if isinstance(obs, Bird):
                self._draw_bird(p, obs)
            else:
                self._draw_cactus(p, obs)
        self._draw_player(p, lg.player)
        self._draw_hud(p)
        self._draw_era_banner(p)

        if lg.state in ('ready', 'dead'):
            p.fillRect(QRectF(0, 0, GAME_W, GAME_H), G_OVERLAY)
            cx = GAME_W / 2
            p.setPen(QPen(C_TEXT))
            p.setFont(self._font(30, bold=True))
            if lg.state == 'ready':
                self._text_center(p, "FLEX TO START", cx, GAME_H / 2 - 30)
                p.setFont(self._font(14))
                p.setPen(QPen(C_DIM))
                self._text_center(p, "quick clench → short hop      hold the clench → long jump",
                                  cx, GAME_H / 2 + 6)
                self._text_center(p, "low birds: jump      high birds: stay down",
                                  cx, GAME_H / 2 + 28)
                self._button(p, "start", "Start", cx - self.BTN_W - 8, GAME_H / 2 + 56, primary=True)
                self._button(p, "menu",  "Main menu", cx + 8,         GAME_H / 2 + 56)
            else:
                self._text_center(p, "GAME OVER", cx, GAME_H / 2 - 40)
                p.setFont(self._font(15, mono=True))
                p.setPen(QPen(C_DIM))
                self._text_center(p, f"score {lg.score:05d}     best {lg.high_score:05d}",
                                  cx, GAME_H / 2 - 4)
                self._button(p, "restart", "Play again", cx - self.BTN_W - 8, GAME_H / 2 + 28, primary=True)
                self._button(p, "menu",    "Main menu",  cx + 8,              GAME_H / 2 + 28)
                p.setFont(self._font(12))
                p.setPen(QPen(C_DIM))
                self._text_center(p, "or flex to play again", cx, GAME_H / 2 + 98)

        p.restore()
        p.end()


class GamePage(QWidget):
    nav_back   = pyqtSignal()
    HOLD_RATIO = 0.85     # clench counts as "held" while activation ≥ threshold × this
    STEP       = 1.0 / 60.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic       = JumpGameLogic()
        self._activation = 0.0
        self._threshold  = 50.0
        self._last_t: float | None = None
        self._acc: float = 0.0
        self._build()

    def _build(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._canvas = GameCanvas(self.logic)
        self._canvas.menu_clicked.connect(self.nav_back)
        self._canvas.restart_clicked.connect(self._restart)
        row.addWidget(self._canvas, stretch=1)

    def _restart(self):
        if self.logic.state in ('dead', 'ready'):
            self.logic.trigger_jump(0.0)     # ready/dead → playing

    def reset_clock(self):
        self._last_t = None
        self._acc = 0.0

    def trigger_jump(self):
        thr = max(1.0, self._threshold)
        strength = (self._activation - thr) / thr        # 0 at threshold, 1 at 2× threshold
        self.logic.trigger_jump(strength)

    def update_frame(self, activation: float, threshold: float, held: bool | None = None):
        self._activation = activation
        self._threshold  = threshold
        if held is None:
            held = activation >= threshold * self.HOLD_RATIO

        # Fixed-step simulation driven by wall-clock time, so the game runs at the
        # same speed whether the UI manages 60 fps or 30 fps.
        now = time.monotonic()
        if self._last_t is None:
            self._last_t = now
        self._acc += min(0.25, now - self._last_t)
        self._last_t = now
        steps = 0
        while self._acc >= self.STEP and steps < 4:
            self.logic.update(held)
            self._acc -= self.STEP
            steps += 1

        self._canvas.set_emg(activation, threshold)
        self._canvas.update()


# ─────────────────────────────────────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────────────────────────────────────

class EMGWorkshopWindow(QMainWindow):

    KEYBOARD_FLEX_UV = 120.0     # activation reported while SPACE is held in keyboard mode
    KEYBOARD_REST_UV = 4.0

    def __init__(self, emg: EMGInput, proc: EMGProcessor,
                 cal: Calibration, ctrl: TriggerController,
                 default_ch: int, keyboard_mode: bool = False):
        super().__init__()
        self._emg        = emg
        self._proc       = proc
        self._cal        = cal
        self._ctrl       = ctrl
        self._active_ch  = default_ch
        self._manual_trig = False
        self._keyboard_mode = keyboard_mode
        self._space_down = False

        self.setWindowTitle("EMG BCI Workshop — Triton Neurotech")
        self.setMinimumSize(800, 560)
        self.resize(WIN_W, WIN_H)
        self.setStyleSheet(build_stylesheet())

        # Pages
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._menu_page = MenuPage(emg, ctrl, cal)
        if keyboard_mode:
            self._menu_page.set_mode_note("keyboard test mode — hold SPACE to flex")
        self._cal_page  = CalibrationPage(emg, proc, cal, ctrl)
        self._game_page = GamePage()

        self._stack.addWidget(self._menu_page)   # 0
        self._stack.addWidget(self._cal_page)    # 1
        self._stack.addWidget(self._game_page)   # 2

        # Wire navigation signals
        self._menu_page.nav_calibrate.connect(self._goto_cal)
        self._menu_page.nav_game.connect(self._goto_game)
        self._menu_page.channel_changed.connect(self._set_channel)

        self._cal_page.nav_back.connect(self._goto_menu)
        self._cal_page.nav_game.connect(self._goto_game)
        self._cal_page.channel_changed.connect(self._set_channel)

        self._game_page.nav_back.connect(self._goto_menu)

        # Update timer (precise: the default coarse timer drifts to ~30 fps)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        # Set initial channel selection
        self._menu_page._ch_sel.set_active(default_ch)
        self._cal_page._ch_sel.set_active(default_ch)

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _goto_menu(self):
        self._stack.setCurrentIndex(0)

    def _goto_cal(self):
        self._cal_page.reset()
        self._stack.setCurrentIndex(1)

    def _goto_game(self):
        self._game_page.reset_clock()
        self._stack.setCurrentIndex(2)

    def _set_channel(self, ch: int):
        self._active_ch = ch
        self._proc.set_channel(ch)
        self._menu_page._ch_sel.set_active(ch)
        self._cal_page._ch_sel.set_active(ch)

    # ── Per-frame update ───────────────────────────────────────────────────────

    def _tick(self):
        # Compute activation (always from peek — never from pull)
        _peek_n = max(self._proc.smooth_samples * 4, 64)
        _disp   = self._emg.peek(_peek_n)
        activation = EMGProcessor.compute_activation_from_window(
            _disp, self._active_ch, self._proc.smooth_samples
        )
        if self._keyboard_mode:
            # SPACE replaces the muscle: a held key is a held clench, with a little
            # noise so the envelope/plot code sees something realistic.
            base = self.KEYBOARD_FLEX_UV if self._space_down else self.KEYBOARD_REST_UV
            activation = base + float(np.random.randn()) * base * 0.08

        triggered         = self._ctrl.update(activation) or self._manual_trig
        self._manual_trig = False

        idx = self._stack.currentIndex()

        if idx == 0:
            self._menu_page.update_frame(activation, self._active_ch)
        elif idx == 1:
            self._active_ch = self._proc.channel
            self._cal_page.update_frame(activation, self._active_ch)
        elif idx == 2:
            if triggered:
                self._game_page.trigger_jump()
            self._game_page.update_frame(activation, self._ctrl.threshold,
                                         held=self._ctrl.is_held(GamePage.HOLD_RATIO))

    # ── Keyboard ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        idx = self._stack.currentIndex()

        if k == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.close()
        elif k == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        elif k == Qt.Key.Key_M:
            self._goto_menu()
        elif k == Qt.Key.Key_C:
            self._goto_cal()
        elif k == Qt.Key.Key_G:
            self._goto_game()
        elif Qt.Key.Key_1 <= k <= Qt.Key.Key_8:
            ch = k - Qt.Key.Key_1
            if ch < self._emg.n_channels and ch not in DISABLED_CHANNELS:
                self._set_channel(ch)
        elif k == Qt.Key.Key_Space:
            if self._keyboard_mode:
                if not event.isAutoRepeat():
                    self._space_down = True
            elif idx == 1:
                self._cal_page.handle_space()
            else:
                self._manual_trig = True

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
        super().keyReleaseEvent(event)

    def closeEvent(self, event):
        self._timer.stop()
        self._emg.stop()
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def parse_args(argv):
    ap = argparse.ArgumentParser(description="EMG BCI Workshop")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "lsl", "udp", "sdk", "ganglion", "synthetic"],
                    help="EMG source (default: auto = lsl → udp → sdk → synthetic)")
    ap.add_argument("--synthetic", action="store_true", help="shortcut for --backend synthetic")
    ap.add_argument("--keyboard", action="store_true",
                    help="test mode: no EMG, hold SPACE to flex (drives the same trigger/game logic)")
    ap.add_argument("--ganglion", action="store_true",
                    help="shortcut for --backend ganglion (direct BLE via BrainFlow)")
    ap.add_argument("--mac", default="", help="Ganglion BLE MAC/UUID (optional)")
    ap.add_argument("--serial", default="", help="Ganglion BLE name, e.g. Ganglion-1234 (optional)")
    ap.add_argument("--serial-port", default="",
                    help="BLED112 dongle port, e.g. /dev/cu.usbmodem11 (uses non-native Ganglion driver)")
    ap.add_argument("--timeout", type=int, default=15, help="Ganglion BLE connect timeout (s)")
    ap.add_argument("--udp-host", default="127.0.0.1", help="OpenBCI GUI UDP host")
    ap.add_argument("--udp-port", type=int, default=12345, help="OpenBCI GUI UDP port")
    ap.add_argument("--channels", default="",
                    help="only show these channels, 1-based, e.g. --channels 1 or --channels 1,3")
    # Qt may receive the remaining args
    return ap.parse_known_args(argv)


def main():
    args, qt_argv = parse_args(sys.argv[1:])
    app = QApplication([sys.argv[0]] + qt_argv)
    app.setApplicationName("EMG BCI Workshop")
    load_fonts()
    app.setFont(QFont(FONT_UI, 13))

    backend = args.backend
    if args.synthetic or args.keyboard:
        backend = "synthetic"
    elif args.ganglion:
        backend = "ganglion"

    emg = EMGInput(
        synthetic=(backend == "synthetic"),
        quiet=args.keyboard,
        preferred_backend=backend,
        udp_host=args.udp_host, udp_port=args.udp_port,
        ganglion_mac=args.mac, ganglion_serial=args.serial,
        ganglion_serial_port=args.serial_port, ganglion_timeout=args.timeout,
    )
    emg.start()

    if args.channels:
        wanted = [int(c) - 1 for c in args.channels.replace(" ", "").split(",") if c]
        emg.select_channels(wanted)

    channel_metrics = probe_channel_metrics(emg)
    if not args.channels:
        # Drop channels that carry nothing at all (switched off in the OpenBCI
        # GUI, or padding channels in an LSL stream) so the UI only shows real inputs.
        flat = [i for i, (m, p95) in enumerate(channel_metrics) if p95 < 0.25 and m < 0.15]
        if flat and len(flat) < emg.n_channels:
            keep = [i for i in range(emg.n_channels) if i not in flat]
            print(f"[main] Hiding flat channels: {', '.join(emg.channel_labels[i] for i in flat)}")
            emg.select_channels(keep)
            channel_metrics = [channel_metrics[i] for i in keep]
    default_ch = choose_default_channel(channel_metrics, emg.n_channels)

    proc = EMGProcessor(
        sampling_rate=emg.sampling_rate, smooth_ms=90, channel=default_ch
    )
    cal  = Calibration()
    ctrl = TriggerController(
        threshold=50.0, refractory_ms=250, release_ratio=0.65, hold_ms=70
    )

    print("=" * 54)
    print("  EMG BCI Workshop  |  Triton Neurotech")
    print(f"  Backend: {emg.backend}  |  {emg.n_channels} ch @ {emg.sampling_rate} Hz"
          f"  ({', '.join(emg.channel_labels)})")
    metrics_str = "  ".join(
        f"CH{i+1}:{m:.1f}/{p:.1f}"
        for i, (m, p) in enumerate(channel_metrics)
    )
    print(f"  Rest probe mean/p95 µV: {metrics_str}")
    print(f"  Default channel: CH{default_ch + 1}")
    print("=" * 54)

    win = EMGWorkshopWindow(emg, proc, cal, ctrl, default_ch,
                            keyboard_mode=args.keyboard)
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
