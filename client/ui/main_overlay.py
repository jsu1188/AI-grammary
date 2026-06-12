from __future__ import annotations

from pathlib import Path
import json
import math
import time

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QIcon, QPainter
from PyQt5.QtWidgets import QApplication, QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QTextEdit, QVBoxLayout, QWidget

from client.ui.tone_prompt import TonePrompt

try:
    import win32gui
except Exception:  # pragma: no cover - optional Windows dependency
    win32gui = None

_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_MAIN_OVERLAY_LOG_PATH = _LOG_DIR / "main_overlay.log"
_NOTIFICATION_LOG_PATH = _LOG_DIR / "notifications.jsonl"
_NOTIFICATION_LOG_PATH.touch(exist_ok=True)




class BusySpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._step = 0
        self.setFixedSize(30, 30)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_step(self, step):
        self._step = int(step or 0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        radius = 10
        active = self._step % 8
        for i in range(8):
            distance = abs(i - active)
            distance = min(distance, 8 - distance)
            pulse = max(0.0, 1.0 - (distance / 3.0))
            dot_radius = 2.2 + (3.2 * pulse)
            alpha = int(85 + (170 * pulse))
            color = QColor(184, 106, 60, min(255, alpha))
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            angle = (i * 45) * math.pi / 180.0
            x = center.x() + radius * math.cos(angle)
            y = center.y() + radius * math.sin(angle)
            size = int(round(dot_radius * 2))
            painter.drawEllipse(int(round(x - dot_radius)), int(round(y - dot_radius)), size, size)

class EvaluationScoreOverlay(QWidget):
    reason_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Score")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(174, 42)
        self.setStyleSheet(
            """
            QFrame#scoreCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 14px;
            }
            QLabel#scoreLabelText {
                color: #3f2f26;
                padding: 0 4px;
                font-size: 12px;
                font-weight: 900;
            }
            QPushButton#scoreReasonButton {
                border: 0;
                border-radius: 10px;
                padding: 5px 9px;
                background: #ead7cf;
                color: #3f2f26;
                font-size: 12px;
                font-weight: 900;
            }
            QPushButton#scoreReasonButton:hover {
                background: #dcc1a7;
            }
            QLabel#scoreValueBox {
                background: #b86a3c;
                color: #fff8f2;
                border-radius: 10px;
                padding: 5px 9px;
                font-size: 12px;
                font-weight: 900;
            }
            """
        )
        card = QFrame(self)
        card.setObjectName("scoreCard")
        card.setGeometry(0, 0, 174, 42)
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 7, 7, 7)
        row.setSpacing(4)
        label = QLabel("\uc810\uc218")
        label.setObjectName("scoreLabelText")
        self.value_label = QLabel("--\uc810")
        self.value_label.setObjectName("scoreValueBox")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.reason_btn = QPushButton("\uc774\uc720")
        self.reason_btn.setObjectName("scoreReasonButton")
        self.reason_btn.clicked.connect(self.reason_requested.emit)
        row.addWidget(label, 0)
        row.addWidget(self.value_label, 1)
        row.addWidget(self.reason_btn, 0)

    def set_score(self, score):
        if score is None:
            value = "--"
        else:
            value = str(max(0, min(100, int(score))))
        self.value_label.setText(f"{value}\uc810")


class EvaluationReasonOverlay(QWidget):
    close_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Evaluation Reason")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(420, 230)
        self.setStyleSheet(
            """
            QFrame#reasonCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#reasonTitle {
                color: #2f241f;
                font-size: 14px;
                font-weight: 900;
            }
            QTextEdit#reasonText {
                background: #fffaf4;
                border: 1px solid #dccbbb;
                border-radius: 10px;
                color: #2f241f;
                padding: 10px;
                font-size: 13px;
                font-weight: 700;
            }
            QTextEdit#reasonText QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 8px 2px 8px 0;
            }
            QTextEdit#reasonText QScrollBar::handle:vertical {
                background: #c7a98c;
                border-radius: 4px;
                min-height: 22px;
            }
            QTextEdit#reasonText QScrollBar::add-line:vertical,
            QTextEdit#reasonText QScrollBar::sub-line:vertical {
                height: 0;
                width: 0;
            }
            QTextEdit#reasonText QScrollBar::add-page:vertical,
            QTextEdit#reasonText QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QPushButton#closeOverlayButton {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border-radius: 13px;
                border: 0;
                padding: 0;
                background: #ead7cf;
                color: #2f241f;
                font-weight: 900;
            }
            QPushButton#closeOverlayButton:hover { background: #dcc1a7; }
            """
        )
        card = QFrame(self)
        card.setObjectName("reasonCard")
        card.setGeometry(0, 0, 420, 230)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.title_label = QLabel("\ud3c9\uac00 \uc774\uc720")
        self.title_label.setObjectName("reasonTitle")
        close_btn = QPushButton("X")
        close_btn.setObjectName("closeOverlayButton")
        close_btn.clicked.connect(self._request_close)
        top.addWidget(self.title_label, 1)
        top.addWidget(close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)
        self.reason_label = QTextEdit()
        self.reason_label.setObjectName("reasonText")
        self.reason_label.setReadOnly(True)
        self.reason_label.setAcceptRichText(False)
        self.reason_label.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self.reason_label, 1)

    def show_for_window(self, window_handle, reason, title=None):
        self.title_label.setText(str(title or "\ud3c9\uac00 \uc774\uc720"))
        self.reason_label.setPlainText(str(reason or ""))
        rect = self._target_rect(window_handle)
        if rect is None:
            screen = QApplication.primaryScreen()
            if screen is None:
                self.show()
                self.raise_()
                return
            geo = screen.availableGeometry()
            left, top, right, bottom = geo.left(), geo.top(), geo.right(), geo.bottom()
        else:
            left, top, right, bottom = rect
        x = left + max(0, (right - left - self.width()) // 2)
        y = top + max(0, (bottom - top - self.height()) // 2)
        self.move(x, y)
        self.show()
        self.raise_()

    def _request_close(self):
        self.hide()
        self.close_requested.emit()

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            return win32gui.GetWindowRect(root)
        except Exception:
            return None


class NotificationOverlay(QWidget):
    close_requested = pyqtSignal()

    PAGE_SIZE = 5

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Notifications")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(360, 290)
        self._notifications = []
        self._page = 0
        self.setStyleSheet(
            """
            QFrame#notificationCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#notificationTitle {
                color: #2f241f;
                font-size: 14px;
                font-weight: 900;
            }
            QLabel#notificationItem {
                background: #fffaf4;
                border: 1px solid #e3d4c4;
                border-radius: 8px;
                color: #2f241f;
                padding: 6px 8px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#notificationItem[error="true"] {
                border: 1px solid #d34a4a;
            }
            QLabel#notificationEmpty {
                color: #7b6658;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#notificationPage {
                color: #7b6658;
                font-size: 11px;
                font-weight: 800;
            }
            QPushButton#notificationNavButton, QPushButton#closeOverlayButton {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border-radius: 13px;
                border: 0;
                padding: 0;
                background: #ead7cf;
                color: #2f241f;
                font-weight: 900;
            }
            QPushButton#notificationNavButton:hover, QPushButton#closeOverlayButton:hover {
                background: #dcc1a7;
            }
            """
        )

        card = QFrame(self)
        card.setObjectName("notificationCard")
        card.setGeometry(0, 0, 360, 290)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("알림")
        title.setObjectName("notificationTitle")
        close_btn = QPushButton("X")
        close_btn.setObjectName("closeOverlayButton")
        close_btn.clicked.connect(self._request_close)
        top.addWidget(title, 1)
        top.addWidget(close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)

        self.item_labels = []
        for _ in range(self.PAGE_SIZE):
            label = QLabel("")
            label.setObjectName("notificationItem")
            label.setWordWrap(True)
            label.setFixedHeight(34)
            self.item_labels.append(label)
            layout.addWidget(label)

        self.empty_label = QLabel("알림이 없습니다.")
        self.empty_label.setObjectName("notificationEmpty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_label, 1)

        bottom = QHBoxLayout()
        self.prev_btn = QPushButton("<")
        self.prev_btn.setObjectName("notificationNavButton")
        self.prev_btn.clicked.connect(self._prev_page)
        self.page_label = QLabel("1 / 1")
        self.page_label.setObjectName("notificationPage")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next_btn = QPushButton(">")
        self.next_btn.setObjectName("notificationNavButton")
        self.next_btn.clicked.connect(self._next_page)
        bottom.addStretch()
        bottom.addWidget(self.prev_btn)
        bottom.addWidget(self.page_label)
        bottom.addWidget(self.next_btn)
        bottom.addStretch()
        layout.addLayout(bottom)
        self._render()

    def set_notifications(self, notifications):
        self._notifications = list(notifications or [])
        max_page = self._max_page()
        self._page = max(0, min(self._page, max_page))
        self._render()

    def show_for_window(self, window_handle):
        self._render()
        rect = self._target_rect(window_handle)
        if rect is None:
            screen = QApplication.primaryScreen()
            if screen is None:
                self.show()
                self.raise_()
                return
            geo = screen.availableGeometry()
            left, top, right, bottom = geo.left(), geo.top(), geo.right(), geo.bottom()
        else:
            left, top, right, bottom = rect
        self.move(left + max(0, (right - left - self.width()) // 2), top + max(0, (bottom - top - self.height()) // 2))
        self.show()
        self.raise_()

    def _render(self):
        total = len(self._notifications)
        has_items = total > 0
        self.empty_label.setVisible(not has_items)
        max_page = self._max_page()
        self._page = max(0, min(self._page, max_page))
        start = self._page * self.PAGE_SIZE
        page_items = self._notifications[start:start + self.PAGE_SIZE]
        for index, label in enumerate(self.item_labels):
            if index < len(page_items):
                item = page_items[index]
                label.setText(f"{item.get('time', '')}  {item.get('message', '')}")
                label.setProperty("error", bool(item.get("error")))
                label.style().unpolish(label)
                label.style().polish(label)
                label.show()
            else:
                label.hide()
        pages = max(1, max_page + 1)
        self.page_label.setText(f"{self._page + 1} / {pages}")
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < max_page)

    def _max_page(self):
        if not self._notifications:
            return 0
        return max(0, (len(self._notifications) - 1) // self.PAGE_SIZE)

    def _prev_page(self):
        self._page = max(0, self._page - 1)
        self._render()

    def _next_page(self):
        self._page = min(self._max_page(), self._page + 1)
        self._render()

    def _request_close(self):
        self.hide()
        self.close_requested.emit()

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            return win32gui.GetWindowRect(root)
        except Exception:
            return None


class SummaryResultOverlay(QWidget):
    copy_requested = pyqtSignal(str)
    close_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Summary")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._default_size = (420, 260)
        self._compact_size = (340, 220)
        self.setFixedSize(*self._default_size)
        self._summary_text = ""
        self.setStyleSheet(
            """
            QFrame#summaryCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#summaryTitle {
                color: #2f241f;
                font-size: 14px;
                font-weight: 900;
            }
            QTextEdit#summaryText {
                background: #fffaf4;
                border: 1px solid #dccbbb;
                border-radius: 12px;
                color: #2f241f;
                padding: 8px;
                font-size: 13px;
                font-weight: 650;
            }
            QTextEdit#summaryText QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 7px 3px 7px 0;
            }
            QTextEdit#summaryText QScrollBar::handle:vertical {
                background: #d8c0a9;
                border-radius: 4px;
                min-height: 28px;
            }
            QTextEdit#summaryText QScrollBar::handle:vertical:hover {
                background: #b86a3c;
            }
            QTextEdit#summaryText QScrollBar::add-line:vertical,
            QTextEdit#summaryText QScrollBar::sub-line:vertical {
                height: 0px;
                border: none;
                background: transparent;
            }
            QTextEdit#summaryText QScrollBar::add-page:vertical,
            QTextEdit#summaryText QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QTextEdit#summaryText QScrollBar:horizontal {
                height: 0px;
            }
            QPushButton#summaryCopyButton {
                border: 0;
                border-radius: 12px;
                padding: 6px 12px;
                background: #b86a3c;
                color: #fff8f2;
                font-size: 12px;
                font-weight: 900;
            }
            QPushButton#summaryCopyButton:hover { background: #9f5730; }
            QPushButton#closeOverlayButton {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border-radius: 13px;
                border: 0;
                padding: 0;
                background: #ead7cf;
                color: #2f241f;
                font-weight: 900;
            }
            QPushButton#closeOverlayButton:hover { background: #dcc1a7; }
            """
        )
        self.card = QFrame(self)
        self.card.setObjectName("summaryCard")
        self.card.setGeometry(0, 0, *self._default_size)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.title_label = QLabel("\uc694\uc57d \uacb0\uacfc")
        self.title_label.setObjectName("summaryTitle")
        self.copy_btn = QPushButton("\ubcf5\uc0ac")
        self.copy_btn.setObjectName("summaryCopyButton")
        close_btn = QPushButton("X")
        close_btn.setObjectName("closeOverlayButton")
        close_btn.clicked.connect(self._request_close)
        self.copy_btn.clicked.connect(self._copy_summary)
        top.addWidget(self.title_label, 1)
        top.addWidget(self.copy_btn, 0, Qt.AlignRight)
        top.addWidget(close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)
        self.text_box = QTextEdit()
        self.text_box.setObjectName("summaryText")
        self.text_box.setReadOnly(True)
        self.text_box.setLineWrapMode(QTextEdit.WidgetWidth)
        layout.addWidget(self.text_box, 1)

    def set_summary(self, text):
        self._summary_text = str(text or "")
        self.text_box.setPlainText(self._summary_text)

    def set_result(self, title, text):
        self.title_label.setText(str(title or "\uc694\uc57d \uacb0\uacfc"))
        self.set_summary(text)

    def set_compact_mode(self, enabled: bool):
        size = self._compact_size if enabled else self._default_size
        if (self.width(), self.height()) != size:
            self.setFixedSize(*size)
            self.resize(*size)
        self.card.setGeometry(0, 0, *size)

    def _copy_summary(self):
        self.copy_requested.emit(self._summary_text)

    def _request_close(self):
        self.close_requested.emit()




class TitleConfirmOverlay(QWidget):
    accepted = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Title")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(330, 174)
        self._title_text = ""
        self.setStyleSheet(
            """
            QFrame#titleCard { background: #f7efe5; border: 1px solid #dccbbb; border-radius: 18px; }
            QLabel#titleConfirmTitle { color: #2f241f; font-size: 14px; font-weight: 900; }
            QLabel#titleConfirmText { background: #fffaf4; border: 1px solid #dccbbb; border-radius: 12px; color: #2f241f; padding: 10px; font-size: 14px; font-weight: 850; }
            QLabel#titleConfirmHint { color: #6d5548; font-size: 12px; font-weight: 800; }
            QPushButton#titleConfirmButton { border: 0; border-radius: 13px; padding: 7px 18px; background: #b86a3c; color: #fff8f2; font-size: 13px; font-weight: 900; }
            QPushButton#titleConfirmButton:hover { background: #9f5730; }
            QPushButton#titleCancelButton { border: 0; border-radius: 13px; padding: 7px 18px; background: #ead7cf; color: #3f2f26; font-size: 13px; font-weight: 900; }
            QPushButton#titleCancelButton:hover { background: #dcc1a7; }
            QPushButton#closeOverlayButton { min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; border-radius: 13px; border: 0; padding: 0; background: #ead7cf; color: #2f241f; font-weight: 900; }
            QPushButton#closeOverlayButton:hover { background: #dcc1a7; }
            """
        )
        card = QFrame(self)
        card.setObjectName("titleCard")
        card.setGeometry(0, 0, 330, 174)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(8)
        top = QHBoxLayout()
        title = QLabel("\uc81c\ubaa9 \ucd94\ucc9c")
        title.setObjectName("titleConfirmTitle")
        close_btn = QPushButton("X")
        close_btn.setObjectName("closeOverlayButton")
        close_btn.clicked.connect(self.hide)
        top.addWidget(title, 1)
        top.addWidget(close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)
        self.title_label = QLabel("")
        self.title_label.setObjectName("titleConfirmText")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label, 1)
        hint = QLabel("\uc774 \uc81c\ubaa9\uc73c\ub85c \ud560\uae4c\uc694?")
        hint.setObjectName("titleConfirmHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.setSpacing(10)
        yes_btn = QPushButton("\uc608")
        yes_btn.setObjectName("titleConfirmButton")
        no_btn = QPushButton("\uc544\ub2c8\uc694")
        no_btn.setObjectName("titleCancelButton")
        yes_btn.clicked.connect(self._accept_title)
        no_btn.clicked.connect(self.hide)
        row.addStretch(1)
        row.addWidget(yes_btn)
        row.addWidget(no_btn)
        row.addStretch(1)
        layout.addLayout(row)

    def show_for_window(self, window_handle, title_text):
        self._title_text = str(title_text or "").strip()
        self.title_label.setText(self._title_text)
        self._move_to_target_center(window_handle)
        self.show()
        self.raise_()

    def show_for_overlay(self, overlay_rect, window_handle, title_text):
        self._title_text = str(title_text or "").strip()
        self.title_label.setText(self._title_text)
        self.reposition_for_overlay(overlay_rect, window_handle)
        self.show()
        self.raise_()

    def reposition_for_overlay(self, overlay_rect, window_handle):
        if overlay_rect is None:
            self._move_to_target_center(window_handle)
            return
        rect = self._target_rect(window_handle)
        margin = 12
        x = int(overlay_rect.left())
        y = int(overlay_rect.bottom()) + 14
        if rect is not None:
            left, top, right, bottom = rect
            x = min(max(left + margin, x), max(left + margin, right - self.width() - margin))
            y = min(max(top + margin, y), max(top + margin, bottom - self.height() - margin))
        self.move(x, y)

    def _move_to_target_center(self, window_handle):
        rect = self._target_rect(window_handle)
        if rect is None:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            left, top, right, bottom = geo.left(), geo.top(), geo.right(), geo.bottom()
        else:
            left, top, right, bottom = rect
        x = left + max(0, (right - left - self.width()) // 2)
        y = top + max(0, (bottom - top - self.height()) // 2)
        self.move(x, y)

    def _accept_title(self):
        title = self._title_text.strip()
        self.hide()
        if title:
            self.accepted.emit(title)

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            return win32gui.GetWindowRect(root)
        except Exception:
            return None


class MainOverlay(QWidget):
    settings_save_requested = pyqtSignal(str, bool)
    open_panel_requested = pyqtSignal()
    history_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    evaluate_requested = pyqtSignal()
    evaluation_reason_requested = pyqtSignal()
    spelling_feedback_requested = pyqtSignal()
    title_requested = pyqtSignal()
    title_insert_requested = pyqtSignal(str)
    correction_requested = pyqtSignal()
    spelling_requested = pyqtSignal()
    summary_requested = pyqtSignal()
    summary_copy_requested = pyqtSignal(str)
    tone_requested = pyqtSignal()
    tone_submitted = pyqtSignal(str)
    dark_mode_requested = pyqtSignal()
    focus_restore_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Main Overlay")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._compact_size = (432, 128)
        self._card_size = (432, 94)
        self._default_compact_size = self._compact_size
        self._default_card_size = self._card_size
        self._notepad_compact_size = (408, 118)
        self._notepad_card_size = (408, 84)
        self._collapsed_size = (94, 54)
        self._collapsed = False
        self.setFixedSize(*self._compact_size)
        self._last_window_handle = None
        self._last_reader_name = ""
        self._overlay_state = None
        self._active_mode = "clipboard"
        self._spelling_replace_mode = False
        self._dark_mode = False
        self._pending_hide_reason = "direct"
        self._drag_active = False
        self._drag_offset = QPoint()
        self._manual_position_active = False
        self._manual_position_hwnd = 0
        self._manual_position_relative = None
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)
        self._mode_indicators = {}
        self._mode_labels = {}
        self.score_overlay = EvaluationScoreOverlay()
        self.score_overlay.reason_requested.connect(self._handle_score_reason_requested)
        self.reason_overlay = EvaluationReasonOverlay()
        self.reason_overlay.close_requested.connect(self.focus_restore_requested.emit)
        self.notification_overlay = NotificationOverlay()
        self.notification_overlay.close_requested.connect(self.focus_restore_requested.emit)
        self.summary_overlay = SummaryResultOverlay()
        self.title_overlay = TitleConfirmOverlay()
        self.tone_prompt = TonePrompt()
        self.summary_overlay.copy_requested.connect(self.summary_copy_requested.emit)
        self.summary_overlay.close_requested.connect(self.hide_summary_result)
        self.title_overlay.accepted.connect(self.title_insert_requested.emit)
        self.tone_prompt.submitted.connect(self.tone_submitted.emit)
        self._evaluation_reason = ""
        self._spelling_feedback_available = False
        self._score_visible = False
        self._summary_visible = False
        self._notifications = []
        self._notification_unread = False
        self._reset_notification_log()
        self._busy_message = ""
        self._busy_step = 0
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(120)
        self._busy_timer.timeout.connect(self._tick_busy_overlay)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        self.root_layout = root
        root.setContentsMargins(0, 34, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("mainOverlayCard")
        root.addWidget(self.card)

        self.setStyleSheet(
            """
            QFrame#mainOverlayCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 16px;
            }
            QLabel#sectionLabel {
                color: #7b6658;
                font-size: 11px;
                font-weight: 800;
            }
            QLabel#statusLabel {
                color: #7b6658;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#modeText {
                color: #3f2f26;
                font-size: 10px;
                font-weight: 800;
            }
            QLabel#modeDot {
                color: #b86a3c;
                font-size: 12px;
                font-weight: 900;
                min-width: 13px;
                max-width: 13px;
            }
            QWidget#modePill {
                background: #fffaf4;
                border: 1px solid #dccbbb;
                border-radius: 12px;
            }
            QPushButton {
                border: 0;
                border-radius: 13px;
                padding: 6px 10px;
                background: #b86a3c;
                color: #fff8f2;
                font-weight: 900;
            }
            QPushButton:hover { background: #9f5730; }
            QPushButton:disabled {
                background: #c9c9c9;
                color: #f7f7f7;
            }
            QPushButton:disabled:hover { background: #c9c9c9; }
            QPushButton#iconOverlayButton {
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                border-radius: 15px;
                padding: 0;
                background: #e8d4bf;
                color: #3f2f26;
            }
            QPushButton#iconOverlayButton:hover { background: #dcc1a7; }
            QPushButton#iconOverlayButton[loginRequired="true"] {
                background: #d8d0c8;
                color: #fff8f2;
            }
            QPushButton#iconOverlayButton[loginRequired="true"]:hover {
                background: #cfc6be;
            }
            QPushButton#hideOverlayButton {
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                border-radius: 15px;
                padding: 0;
                background: #ead7cf;
                color: #3f2f26;
                font-weight: 900;
            }
            QPushButton#hideOverlayButton:hover { background: #dcc1a7; }
            QPushButton#floatingReasonButton {
                border: 0;
                border-radius: 12px;
                padding: 5px 11px;
                background: #ead7cf;
                color: #3f2f26;
                font-size: 11px;
                font-weight: 900;
            }
            QPushButton#floatingReasonButton:hover { background: #dcc1a7; }
            QPushButton#collapsedHelperButton {
                border-radius: 20px;
                padding: 0;
                background: #b86a3c;
                color: #fff8f2;
                font-size: 11px;
                font-weight: 900;
            }
            QPushButton#collapsedHelperButton:hover { background: #9f5730; }
            QPushButton#closeOverlayButton {
                min-width: 22px;
                max-width: 22px;
                min-height: 22px;
                max-height: 22px;
                border-radius: 11px;
                padding: 0;
                background: #ead7cf;
                color: #2f241f;
                font-size: 11px;
                font-weight: 900;
            }
            QFrame#sectionLine {
                background: rgba(47, 36, 31, 42);
                min-width: 1px;
                max-width: 1px;
            }
            QFrame#settingsCover {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#settingsTitle {
                color: #2f241f;
                font-size: 13px;
                font-weight: 900;
            }
            QRadioButton {
                color: #3f2f26;
                font-size: 11px;
                font-weight: 800;
            }
            QRadioButton:disabled, QCheckBox:disabled {
                color: #9a8a7e;
            }
            QCheckBox#settingsSubCheck {
                color: #3f2f26;
                font-size: 9px;
                font-weight: 750;
                spacing: 4px;
            }
            QRadioButton::indicator {
                width: 13px;
                height: 13px;
                border: 1px solid #cdb8a5;
                border-radius: 7px;
                background: #fffaf4;
            }
            QRadioButton::indicator:checked { background: #b86a3c; }
            QCheckBox#settingsSubCheck::indicator {
                width: 11px;
                height: 11px;
                border: 1px solid #cdb8a5;
                border-radius: 4px;
                background: #fffaf4;
            }
            QCheckBox#settingsSubCheck::indicator:disabled {
                border: 1px solid #ddd4ca;
                background: #eee8e1;
            }
            QCheckBox#settingsSubCheck::indicator:checked {
                background: #b86a3c;
                border: 1px solid #b86a3c;
            }
            """
        )

        layout = QHBoxLayout(self.card)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(4)

        self.mode_widget = self._create_mode_display()
        self.mode_widget.setParent(self)

        self.evaluate_btn = self._button("\ud3c9\uac00", self.evaluate_requested.emit, width=54)
        self.title_btn = self._button("\uc81c\ubaa9", self.title_requested.emit, width=54)
        layout.addWidget(self._section("\ud14d\uc2a4\ud2b8", [
            self.evaluate_btn,
            self.title_btn,
        ], width=114))
        layout.addWidget(self._section_line())

        self.spelling_btn = self._button("\ub9de\ucda4\ubc95", self.spelling_requested.emit, width=70)
        self.tone_btn = self._button("\ubb38\uccb4", self.tone_requested.emit, width=54)
        self.correction_btn = self.spelling_btn
        layout.addWidget(self._section("\uad50\uc815", [
            self.spelling_btn,
            self.tone_btn,
        ], width=126, button_spacing=2))
        layout.addWidget(self._section_line())

        self.summary_btn = self._button("\uc694\uc57d", self.summary_requested.emit, width=56)
        layout.addWidget(self._section("\uc694\uc57d", [
            self.summary_btn,
        ], width=66, left_pad=8))

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setFixedWidth(0)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.hide()

        self.undo_btn = self._icon_button("\ub418\ub3cc\ub9ac\uae30", ("back.png", "back.svg"))
        self.undo_btn.setParent(self)
        self.undo_btn.clicked.connect(self.undo_requested.emit)
        self.undo_btn.setEnabled(False)
        self.redo_btn = self._icon_button("\uc7ac\uc2e4\ud589", ("forward.png", "forward.svg"))
        self.redo_btn.setParent(self)
        self.redo_btn.clicked.connect(self.redo_requested.emit)
        self.redo_btn.setEnabled(False)

        self.hide_btn = QPushButton("X", self)
        self.hide_btn.setObjectName("hideOverlayButton")
        self.hide_btn.setToolTip("\ub3c4\uc6b0\ubbf8 \uc228\uae30\uae30")
        self.hide_btn.clicked.connect(self.collapse)

        self.settings_btn = self._icon_button("\uc124\uc815", ("settings.png", "settings.svg"))
        self.settings_btn.setParent(self)
        self.settings_btn.clicked.connect(self.open_settings_cover)
        self.history_btn = self._icon_button("\uae30\ub85d", ("list.png", "list.svg"))
        self.history_btn.setParent(self)
        self.history_btn.clicked.connect(self.history_requested.emit)
        self.open_btn = self._icon_button("\uc5f4\uae30", ("magnification_large.png", "magnification_large.svg"))
        self.open_btn.setParent(self)
        self.open_btn.clicked.connect(self.open_panel_requested.emit)
        self.notification_btn = self._icon_button("\uc54c\ub9bc", ("notifications.png", "notifications.svg"))
        self.notification_btn.setParent(self)
        self.notification_btn.clicked.connect(self.show_notifications)
        self.dark_btn = self._icon_button("\ub2e4\ud06c \ubaa8\ub4dc", ("brightness.png", "brightness.svg"))
        self.dark_btn.setParent(self)
        self.dark_btn.clicked.connect(self.dark_mode_requested.emit)
        self.dark_btn.hide()
        self.spelling_reason_btn = QPushButton("\uc218\uc815 \uc774\uc720", self)
        self.spelling_reason_btn.setObjectName("floatingReasonButton")
        self.spelling_reason_btn.setToolTip("\ub9de\ucda4\ubc95 \uc218\uc815 \uc774\uc720")
        self.spelling_reason_btn.clicked.connect(self.spelling_feedback_requested.emit)
        self.spelling_reason_btn.hide()

        self.collapsed_btn = QPushButton("도우미\n키기", self.card)
        self.collapsed_btn.setObjectName("collapsedHelperButton")
        self.collapsed_btn.setToolTip("도우미 오버레이 펼치기")
        self.collapsed_btn.clicked.connect(self.expand)
        self.collapsed_btn.hide()

        self._build_settings_cover()
        self._build_busy_cover()

    def _build_busy_cover(self):
        self.busy_cover = QFrame(self.card)
        self.busy_cover.setObjectName("busyCover")
        self.busy_cover.setStyleSheet(
            """
            QFrame#busyCover {
                background: rgba(247, 239, 229, 235);
                border: 1px solid #dccbbb;
                border-radius: 16px;
            }
            QLabel#busySpinner {
                color: #b86a3c;
                font-size: 26px;
                font-weight: 900;
            }
            QLabel#busyText {
                color: #2f241f;
                font-size: 14px;
                font-weight: 900;
            }
            """
        )
        layout = QVBoxLayout(self.busy_cover)
        layout.setContentsMargins(0, 16, 0, 8)
        layout.setSpacing(2)
        self.busy_spinner_label = BusySpinner(self.busy_cover)
        self.busy_text_label = QLabel("")
        self.busy_text_label.setObjectName("busyText")
        self.busy_text_label.setAlignment(Qt.AlignCenter)
        layout.addStretch(2)
        layout.addWidget(self.busy_spinner_label, 0, Qt.AlignCenter)
        layout.addWidget(self.busy_text_label, 0, Qt.AlignCenter)
        layout.addStretch(1)
        self.busy_cover.hide()

    def show_busy(self, message):
        if not hasattr(self, "busy_cover"):
            return
        self._busy_message = str(message or "")
        self._busy_step = 0
        self.busy_cover.setGeometry(self.card.rect())
        self._tick_busy_overlay()
        self.busy_cover.show()
        self.busy_cover.raise_()
        self.busy_cover.repaint()
        self.repaint()
        QApplication.processEvents()
        self._busy_timer.start()

    def hide_busy(self):
        if hasattr(self, "busy_cover"):
            self.busy_cover.hide()
        self._busy_timer.stop()

    def _tick_busy_overlay(self):
        dots = "." * (self._busy_step % 4)
        if hasattr(self, "busy_spinner_label"):
            self.busy_spinner_label.set_step(self._busy_step)
        if hasattr(self, "busy_text_label"):
            self.busy_text_label.setText(f"{self._busy_message}{dots}")
        self._busy_step += 1

    def _create_mode_display(self):
        widget = QWidget()
        widget.setObjectName("modePill")
        widget.setFixedSize(82, 30)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        dot = QLabel("\u25cf")
        dot.setObjectName("modeDot")
        text = QLabel("\ud074\ub9bd\ubcf4\ub4dc")
        text.setObjectName("modeText")
        layout.addWidget(dot, 0, Qt.AlignVCenter)
        layout.addWidget(text, 1, Qt.AlignVCenter)
        self._mode_indicators["active"] = dot
        self._mode_labels["active"] = text
        return widget

    def _build_settings_cover(self):
        self.settings_cover = QFrame(self.card)
        self.settings_cover.setObjectName("settingsCover")
        self.settings_cover.hide()

        layout = QVBoxLayout(self.settings_cover)
        layout.setContentsMargins(16, 8, 16, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel("\uc624\ubc84\ub808\uc774 \uc124\uc815")
        title.setObjectName("settingsTitle")
        self.settings_close_btn = QPushButton("X")
        self.settings_close_btn.setObjectName("closeOverlayButton")
        self.settings_close_btn.setToolTip("\ub2eb\uae30")
        self.settings_close_btn.clicked.connect(self.close_settings_cover)
        top.addWidget(title, 1)
        top.addWidget(self.settings_close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        self.mode_group = QButtonGroup(self.settings_cover)
        self.mode_group.setExclusive(True)
        self.drag_radio = QRadioButton("\ub4dc\ub798\uadf8 \ubaa8\ub4dc")
        self.realtime_radio = QRadioButton("\uc2e4\uc2dc\uac04 \ubaa8\ub4dc")
        self.drag_replace_check = QCheckBox("\ub9de\ucda4\ubc95 \uc218\uc815 \ubc29\uc2dd \uc0ac\uc6a9")
        self.drag_replace_check.setObjectName("settingsSubCheck")
        self.realtime_replace_check = QCheckBox("\ub9de\ucda4\ubc95 \uc218\uc815 \ubc29\uc2dd \uc0ac\uc6a9")
        self.realtime_replace_check.setObjectName("settingsSubCheck")
        self.drag_replace_check.toggled.connect(lambda checked: self._sync_replace_checks("drag", checked))
        self.realtime_replace_check.toggled.connect(lambda checked: self._sync_replace_checks("realtime", checked))
        for radio, mode in ((self.drag_radio, "drag"), (self.realtime_radio, "realtime")):
            self.mode_group.addButton(radio)
            radio.setProperty("mode", mode)
            radio.toggled.connect(self._update_settings_replace_availability)
            replace_check = self.drag_replace_check if mode == "drag" else self.realtime_replace_check
            mode_layout = QVBoxLayout()
            mode_layout.setContentsMargins(0, 0, 0, 0)
            mode_layout.setSpacing(1)
            mode_layout.addWidget(radio)
            sub_row = QHBoxLayout()
            sub_row.setContentsMargins(13, 0, 0, 0)
            sub_row.setSpacing(0)
            sub_row.addWidget(replace_check)
            sub_row.addStretch(1)
            mode_layout.addLayout(sub_row)
            controls_row.addLayout(mode_layout)
        controls_row.addStretch(1)
        save_btn = self._button("\uc800\uc7a5", self._emit_mode_save, width=64)
        save_btn.setFixedHeight(30)
        controls_row.addWidget(save_btn)
        layout.addLayout(controls_row)

    def _button(self, text, callback, width=52):
        button = QPushButton(text)
        button.clicked.connect(callback)
        button.setFixedWidth(width)
        return button

    def _icon_button(self, tooltip, icon_names):
        button = QPushButton("")
        button.setObjectName("iconOverlayButton")
        button.setToolTip(tooltip)
        icon_path = self._find_icon(icon_names)
        if icon_path:
            button.setIcon(QIcon(str(icon_path)))
            button.setIconSize(QSize(18, 18))
        else:
            button.setText("?")
        return button

    def _section(self, title, buttons, width, left_pad=0, button_spacing=4):
        widget = QWidget()
        widget.setFixedWidth(width)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(left_pad, 0, 0, 0)
        layout.setSpacing(5)
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        label.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(button_spacing)
        row.setAlignment(Qt.AlignCenter)
        for button in buttons:
            row.addWidget(button)
        layout.addWidget(label)
        layout.addLayout(row)
        return widget

    def _section_line(self):
        line = QFrame()
        line.setObjectName("sectionLine")
        line.setFixedWidth(1)
        return line

    def _find_icon(self, names):
        icon_dir = Path(__file__).resolve().parent.parent / "icon"
        for name in names:
            path = icon_dir / name
            if path.exists():
                return path
        return None

    def _emit_mode_save(self):
        checked = self.mode_group.checkedButton()
        mode = checked.property("mode") if checked is not None else self._active_mode
        replace_enabled = self.drag_replace_check.isChecked() if mode == "drag" else self.realtime_replace_check.isChecked()
        self.close_settings_cover()
        self.settings_save_requested.emit(str(mode or self._active_mode), bool(replace_enabled))

    def _sync_replace_checks(self, source_mode, checked):
        self._spelling_replace_mode = bool(checked)
        other = self.realtime_replace_check if source_mode == "drag" else self.drag_replace_check
        other.blockSignals(True)
        other.setChecked(bool(checked))
        other.blockSignals(False)

    def set_spelling_replace_mode(self, enabled):
        self._spelling_replace_mode = bool(enabled)
        for checkbox in (self.drag_replace_check, self.realtime_replace_check):
            checkbox.blockSignals(True)
            checkbox.setChecked(self._spelling_replace_mode)
            checkbox.blockSignals(False)
        self._update_settings_replace_availability()

    def set_correction_enabled(self, enabled):
        if hasattr(self, "spelling_btn"):
            self.spelling_btn.setEnabled(bool(enabled))

    def set_api_actions_enabled(self, enabled):
        enabled = bool(enabled)
        for button_name in (
            "evaluate_btn",
            "title_btn",
            "spelling_btn",
            "tone_btn",
            "summary_btn",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(enabled)

    def set_action_buttons_enabled(
        self,
        *,
        evaluate=None,
        title=None,
        spelling=None,
        tone=None,
        summary=None,
    ):
        state_map = {
            "evaluate_btn": evaluate,
            "title_btn": title,
            "spelling_btn": spelling,
            "tone_btn": tone,
            "summary_btn": summary,
        }
        for button_name, enabled in state_map.items():
            if enabled is None:
                continue
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(bool(enabled))

    def set_spelling_feedback_available(self, available: bool):
        self._spelling_feedback_available = bool(available)
        if hasattr(self, "spelling_reason_btn"):
            self.spelling_reason_btn.setVisible(self._spelling_feedback_available and not self._collapsed)

    def set_undo_available(self, available):
        if hasattr(self, "undo_btn"):
            self.undo_btn.setEnabled(bool(available))

    def set_redo_available(self, available):
        if hasattr(self, "redo_btn"):
            self.redo_btn.setEnabled(bool(available))

    def set_dark_mode(self, enabled):
        self._dark_mode = bool(enabled)
        if not hasattr(self, "dark_btn"):
            return
        names = ("dark_mode.png", "dark_mode.svg") if self._dark_mode else ("brightness.png", "brightness.svg")
        icon_path = self._find_icon(names)
        if icon_path:
            self.dark_btn.setText("")
            self.dark_btn.setIcon(QIcon(str(icon_path)))
            self.dark_btn.setIconSize(QSize(18, 18))
        else:
            self.dark_btn.setText("\ub2e4")

    def set_history_login_state(self, logged_in):
        if not hasattr(self, "history_btn"):
            return
        login_required = not bool(logged_in)
        self.history_btn.setProperty("loginRequired", login_required)
        self.history_btn.setToolTip("기록" if not login_required else "로그인 후 기록 사용")
        self.history_btn.style().unpolish(self.history_btn)
        self.history_btn.style().polish(self.history_btn)
        self.history_btn.update()

    def add_notification(self, message, error=False):
        text = str(message or "").strip()
        if not text:
            return
        item = {
            "time": time.strftime("%H:%M:%S"),
            "message": text,
            "error": bool(error),
        }
        self._notifications.insert(0, item)
        self._notifications = self._notifications[:100]
        self._notification_unread = True
        self._write_notification_log(item)
        self._update_notification_icon()
        if getattr(self, "notification_overlay", None) is not None and self.notification_overlay.isVisible():
            self.notification_overlay.set_notifications(self._notifications)

    def show_notifications(self):
        if not hasattr(self, "notification_overlay"):
            return
        self._notification_unread = False
        self._update_notification_icon()
        self.notification_overlay.set_notifications(self._notifications)
        self.notification_overlay.show_for_window(self._last_window_handle)

    def _update_notification_icon(self):
        if not hasattr(self, "notification_btn"):
            return
        names = ("notifications_un.png", "notifications_un.svg") if self._notification_unread else ("notifications.png", "notifications.svg")
        icon_path = self._find_icon(names)
        if icon_path:
            self.notification_btn.setText("")
            self.notification_btn.setIcon(QIcon(str(icon_path)))
            self.notification_btn.setIconSize(QSize(18, 18))
        elif self._notification_unread:
            self.notification_btn.setText("!")
        else:
            self.notification_btn.setText("?")

    def _write_notification_log(self, item):
        try:
            payload = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "time": item.get("time", ""),
                "message": item.get("message", ""),
                "error": bool(item.get("error")),
            }
            with _NOTIFICATION_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _reset_notification_log(self):
        try:
            _NOTIFICATION_LOG_PATH.write_text("", encoding="utf-8")
        except Exception:
            pass

    def set_active_mode(self, mode):
        self._active_mode = mode if mode in {"clipboard", "drag", "realtime"} else "clipboard"
        active_label = {
            "clipboard": "\ud074\ub9bd\ubcf4\ub4dc",
            "drag": "\ub4dc\ub798\uadf8",
            "realtime": "\uc2e4\uc2dc\uac04",
        }.get(self._active_mode, "\ud074\ub9bd\ubcf4\ub4dc")
        label = self._mode_labels.get("active")
        if label is not None:
            label.setText(active_label)
        radio = {
            "drag": self.drag_radio,
            "realtime": self.realtime_radio,
        }.get(self._active_mode)
        if radio is not None:
            radio.setChecked(True)
        else:
            for button in (self.drag_radio, self.realtime_radio):
                button.setAutoExclusive(False)
                button.setChecked(False)
                button.setAutoExclusive(True)
        self._update_settings_replace_availability()

    def _update_settings_replace_availability(self):
        drag_selected = self.drag_radio.isChecked()
        realtime_selected = self.realtime_radio.isChecked()
        self.drag_replace_check.setVisible(drag_selected)
        self.realtime_replace_check.setVisible(realtime_selected)
        self.drag_replace_check.setEnabled(drag_selected)
        self.realtime_replace_check.setEnabled(realtime_selected)

    def open_settings_cover(self):
        self.settings_cover.setGeometry(self.card.rect())
        self.settings_cover.show()
        self.settings_cover.raise_()

    def close_settings_cover(self):
        self.settings_cover.hide()
        self.focus_restore_requested.emit()

    def toggle_settings(self):
        if self.settings_cover.isVisible():
            self.close_settings_cover()
        else:
            self.open_settings_cover()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "settings_cover"):
            self.settings_cover.setGeometry(self.card.rect())
        if hasattr(self, "busy_cover"):
            self.busy_cover.setGeometry(self.card.rect())


    def collapse(self):
        self._collapsed = True
        self.close_settings_cover()
        self._ensure_collapsed_size()
        for widget in (
            self.mode_widget,
            self.status_label,
            self.undo_btn,
            self.redo_btn,
            self.history_btn,
            self.open_btn,
            self.settings_btn,
            self.notification_btn,
            self.spelling_reason_btn,
            self.hide_btn,
        ):
            widget.hide()
        for child in self.card.findChildren(QFrame):
            if child is not self.card and child is not self.settings_cover:
                child.hide()
        for child in self.card.findChildren(QPushButton):
            if child is not self.collapsed_btn:
                child.hide()
        for child in self.card.findChildren(QLabel):
            child.hide()
        self.collapsed_btn.show()
        self.hide_evaluation_score()
        if hasattr(self, "summary_overlay"):
            self.summary_overlay.hide()
        self._show_for_window(self._last_window_handle)

    def expand(self):
        self._collapsed = False
        self.hide_busy()
        self._ensure_expanded_size()
        self.collapsed_btn.hide()
        self.mode_widget.show()
        self.mode_widget.raise_()
        self.hide_btn.show()
        self.undo_btn.show()
        self.redo_btn.show()
        self.history_btn.show()
        self.open_btn.show()
        self.settings_btn.show()
        self.notification_btn.show()
        self.dark_btn.hide()
        for child in self.card.findChildren(QWidget):
            if child not in {
                self.settings_cover,
                self.busy_cover,
                self.collapsed_btn,
            }:
                child.show()
        self.set_spelling_feedback_available(self._spelling_feedback_available)
        self.close_settings_cover()
        self._show_for_window(self._last_window_handle)

    def _ensure_expanded_size(self):
        if self._is_notepad_reader():
            self._compact_size = self._notepad_compact_size
            self._card_size = self._notepad_card_size
            self.mode_widget.setFixedSize(72, 30)
            self._set_mode_labels_compact(True)
        else:
            self._compact_size = self._default_compact_size
            self._card_size = self._default_card_size
            self.mode_widget.setFixedSize(82, 30)
            self._set_mode_labels_compact(False)
        if hasattr(self, "root_layout"):
            self.root_layout.setContentsMargins(0, 34, 0, 0)
        if self.width() != self._compact_size[0] or self.height() != self._compact_size[1]:
            self.setFixedSize(*self._compact_size)
            self.resize(*self._compact_size)
        self.card.setFixedSize(*self._card_size)
        self.card.resize(*self._card_size)
        if hasattr(self, "busy_cover"):
            self.busy_cover.setGeometry(self.card.rect())
        top_button_y = 0
        self.mode_widget.move(0, top_button_y)
        self.mode_widget.raise_()
        buttons = [
            self.undo_btn,
            self.redo_btn,
            self.history_btn,
            self.open_btn,
            self.settings_btn,
            self.notification_btn,
            self.hide_btn,
        ]
        x = self.width() - 30
        for button in reversed(buttons):
            button.setGeometry(x, top_button_y, 30, 30)
            x -= 36
            button.raise_()
        self.spelling_reason_btn.setGeometry(0, max(0, self.height() - 30), 78, 28)
        self.spelling_reason_btn.raise_()
        self.spelling_reason_btn.setVisible(bool(self._spelling_feedback_available) and not self._collapsed)

    def _ensure_collapsed_size(self):
        if hasattr(self, "root_layout"):
            self.root_layout.setContentsMargins(0, 0, 0, 0)
        if self.width() != self._collapsed_size[0] or self.height() != self._collapsed_size[1]:
            self.setFixedSize(*self._collapsed_size)
            self.resize(*self._collapsed_size)
        self.card.setFixedSize(*self._collapsed_size)
        self.card.resize(*self._collapsed_size)
        self.hide_btn.hide()
        self.undo_btn.hide()
        self.redo_btn.hide()
        self.settings_btn.hide()
        self.history_btn.hide()
        self.open_btn.hide()
        self.notification_btn.hide()
        self.dark_btn.hide()
        self.collapsed_btn.setGeometry(8, 7, 78, 40)

    def show_status(self, message, auto_hide_ms=1200):
        self.status_label.setText(str(message or ""))
        self._status_timer.stop()
        self._status_timer.start(max(200, int(auto_hide_ms)))

    def _clear_status(self):
        self.status_label.clear()

    def show_for_target(self, reader_name="", window_handle=None):
        if reader_name:
            self._last_reader_name = reader_name
        if window_handle:
            if int(window_handle) != int(self._last_window_handle or 0):
                self._manual_position_active = False
                self._manual_position_hwnd = 0
                self._manual_position_relative = None
            self._last_window_handle = window_handle
        state = (reader_name or self._last_reader_name, int(window_handle or self._last_window_handle or 0), self._active_mode, "collapsed" if self._collapsed else "expanded")
        self._overlay_state = state
        self._show_for_window(window_handle or self._last_window_handle)

    def show_tone_prompt(self):
        self.tone_prompt.show_for_overlay(self.frameGeometry(), self._last_window_handle)

    def _show_for_window(self, window_handle=None):
        if self._collapsed:
            self._ensure_collapsed_size()
        else:
            self._ensure_expanded_size()
        rect = self._target_rect(window_handle)
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if rect is not None:
            left, top, right, bottom = rect
            margin = 20
            if self._should_hide_for_bounds(left, top, right, bottom, margin):
                self.hide_with_reason("target_too_small")
                return
            current_hwnd = int(window_handle or self._last_window_handle or 0)
            if self._manual_position_active and self._manual_position_hwnd == current_hwnd:
                relative = self._manual_position_relative or (self.x() - left, self.y() - top)
                x, y = self._clamp_position_within_target(
                    left + int(relative[0]),
                    top + int(relative[1]),
                    left,
                    top,
                    right,
                    bottom,
                    margin,
                )
            else:
                x, y = self._responsive_position(left, top, right, bottom, margin)
            self.move(x, y)
        elif screen is not None:
            available = screen.availableGeometry()
            left, top, right, bottom = available.left(), available.top(), available.right(), available.bottom()
            x, y = self._responsive_position(left, top, right, bottom, 20)
            self.move(x, y)
        self.show()
        self.raise_()
        self._position_score_overlay()
        self._position_summary_overlay()
        self._position_title_overlay()
        self._position_tone_prompt()

    def _position_score_overlay(self):
        if not getattr(self, "_score_visible", False) or self._collapsed or not self.isVisible():
            return
        geo = self.frameGeometry()
        x = geo.right() - self.score_overlay.width()
        y = geo.bottom() + 12
        self.score_overlay.move(x, y)
        self.score_overlay.show()
        self.score_overlay.raise_()

    def _position_summary_overlay(self):
        if not getattr(self, "_summary_visible", False) or self._collapsed or not self.isVisible():
            return
        self.summary_overlay.set_compact_mode(self._is_notepad_reader())
        geo = self.frameGeometry()
        x = geo.right() - self.summary_overlay.width() if self._is_notepad_reader() else geo.left()
        y = geo.bottom() + 14
        if getattr(self, "_score_visible", False) and hasattr(self, "score_overlay"):
            y = max(y, geo.bottom() + 12 + self.score_overlay.height() + 10)
        self.summary_overlay.move(x, y)
        self.summary_overlay.show()
        self.summary_overlay.raise_()

    def _position_tone_prompt(self):
        if not hasattr(self, "tone_prompt") or not self.tone_prompt.isVisible():
            return
        self.tone_prompt.reposition_for_overlay(self.frameGeometry(), self._last_window_handle)
        self.tone_prompt.raise_()

    def _position_title_overlay(self):
        if not hasattr(self, "title_overlay") or not self.title_overlay.isVisible():
            return
        self.title_overlay.reposition_for_overlay(self.frameGeometry(), self._last_window_handle)
        self.title_overlay.raise_()

    def show_title_confirmation(self, title_text):
        self.title_overlay.show_for_overlay(self.frameGeometry(), self._last_window_handle, title_text)

    def should_delegate_summary_to_panel(self):
        return self._should_delegate_reason_to_panel()

    def show_summary_result(self, summary_text):
        self.hide_summary_result()
        self.summary_overlay.set_result("\uc694\uc57d \uacb0\uacfc", summary_text)
        self._summary_visible = True
        self._position_summary_overlay()

    def show_text_result(self, title, text):
        self.hide_summary_result()
        self.summary_overlay.set_result(title, text)
        self._summary_visible = True
        self._position_summary_overlay()

    def replace_summary_result(self, summary_text):
        self.show_summary_result(summary_text)

    def hide_summary_result(self):
        self._summary_visible = False
        self.summary_overlay.hide()

    def show_evaluation_score(self, score, reason=""):
        self.score_overlay.set_score(score)
        self._evaluation_reason = str(reason or "")
        self._score_visible = True
        self._position_score_overlay()

    def _handle_score_reason_requested(self):
        self.evaluation_reason_requested.emit()

    def show_spelling_feedback_reason(self, feedback, window_handle=None):
        self.reason_overlay.show_for_window(
            window_handle or self._last_window_handle,
            feedback or "\uad50\uc815 \ud3c9\uac00\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.",
            "\uc218\uc815 \uc774\uc720",
        )

    def _should_delegate_reason_to_panel(self):
        rect = self._target_rect(self._last_window_handle)
        if rect is None:
            return True
        left, top, right, bottom = rect
        reader_name = self._last_reader_name or ""
        threshold = 760 if self._is_notepad_reader() else 1800
        return max(0, right - left) < threshold

    def _is_notepad_reader(self):
        return (self._last_reader_name or "") in {"notepad", "notepad_selection"}

    def _set_mode_labels_compact(self, compact: bool):
        labels = {
            "clipboard": "\ud074\ub9bd\ubcf4\ub4dc",
            "drag": "\ub4dc\ub798\uadf8",
            "realtime": "\uc2e4\uc2dc\uac04",
        }
        widget = self._mode_labels.get("active")
        if widget is not None:
            widget.setText(labels.get(self._active_mode, "\ud074\ub9bd\ubcf4\ub4dc"))

    def hide_evaluation_score(self):
        self._score_visible = False
        self.score_overlay.hide()
        self.reason_overlay.hide()

    def _responsive_position(self, left, top, right, bottom, margin):
        width = max(0, right - left)
        height = max(0, bottom - top)
        reader_name = self._last_reader_name or ""
        overlay_width = self.width()
        overlay_height = self.height()

        if self._is_notepad_reader():
            wide_threshold = 760
            wide_x = right - overlay_width - 46
            top_offset = 74
        else:
            wide_threshold = 1800
            wide_x = left + 10
            top_offset = 252

        centered_x = left + max(margin, (width - overlay_width) // 2)
        desired_x = centered_x if width < wide_threshold else wide_x
        x = min(max(desired_x, left + margin), right - overlay_width - margin)

        desired_y = top + top_offset
        if height < top_offset + overlay_height + margin * 2:
            desired_y = top + max(margin, (height - overlay_height) // 2)
        y = min(max(desired_y, top + margin), bottom - overlay_height - margin)
        return x, y

    def _clamp_position_within_target(self, x, y, left, top, right, bottom, margin):
        min_x = left + margin
        max_x = right - self.width() - margin
        min_y = top + margin
        max_y = bottom - self.height() - margin
        return (
            min(max(int(x), min_x), max_x),
            min(max(int(y), min_y), max_y),
        )

    def _should_hide_for_bounds(self, left, top, right, bottom, margin):
        width = max(0, right - left)
        height = max(0, bottom - top)
        if width < self.width() + margin * 2:
            return True
        reader_name = self._last_reader_name or ""
        min_height = 240 if self._is_notepad_reader() else 430
        return height < min_height

    def hide_with_reason(self, reason):
        self._pending_hide_reason = reason or "direct"
        self.hide()

    def hide(self):
        reason = getattr(self, "_pending_hide_reason", "direct")
        self._pending_hide_reason = "direct"
        self._drag_active = False
        self._log_overlay("hide_called", reason=reason, state=self._overlay_state, last_hwnd=self._last_window_handle)
        if hasattr(self, "settings_cover"):
            self.settings_cover.hide()
        if hasattr(self, "busy_cover"):
            self.hide_busy()
        if hasattr(self, "score_overlay"):
            self.score_overlay.hide()
            self.reason_overlay.hide()
        if hasattr(self, "title_overlay"):
            self.title_overlay.hide()
        if hasattr(self, "summary_overlay"):
            self.summary_overlay.hide()
        if hasattr(self, "tone_prompt"):
            self.tone_prompt.hide()
        if hasattr(self, "notification_overlay"):
            self.notification_overlay.hide()
        self._overlay_state = None
        super().hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.buttons() & Qt.LeftButton):
            rect = self._target_rect(self._last_window_handle)
            if rect is not None:
                left, top, right, bottom = rect
                x, y = self._clamp_position_within_target(
                    event.globalPos().x() - self._drag_offset.x(),
                    event.globalPos().y() - self._drag_offset.y(),
                    left,
                    top,
                    right,
                    bottom,
                    20,
                )
                self.move(x, y)
                self._manual_position_active = True
                self._manual_position_hwnd = int(self._last_window_handle or 0)
                self._manual_position_relative = (x - left, y - top)
                self._position_score_overlay()
                self._position_summary_overlay()
                self._position_title_overlay()
                self._position_tone_prompt()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def has_overlay_focus(self):
        if win32gui is None:
            return False
        try:
            foreground = win32gui.GetForegroundWindow()
            overlay_hwnd = int(self.winId())
            if self._same_root_window(foreground, overlay_hwnd):
                return True
            title = win32gui.GetWindowText(foreground) or ""
            class_name = win32gui.GetClassName(foreground) or ""
            overlay_titles = {
                "Writing Assistant Main Overlay",
                "Writing Assistant Score",
                "Writing Assistant Evaluation Reason",
                "Writing Assistant Summary",
                "Writing Assistant Title",
                "Writing Assistant Tone",
                "Writing Assistant Notifications",
            }
            return title in overlay_titles and (class_name.startswith("Qt") or "QWindow" in class_name)
        except Exception:
            return False

    def _target_rect(self, window_handle):
        if win32gui is None or not window_handle:
            return None
        try:
            if not win32gui.IsWindow(window_handle):
                return None
            root = win32gui.GetAncestor(window_handle, 2) or window_handle
            left, top, right, bottom = win32gui.GetWindowRect(root)
            if right - left <= self.width() + 24 or bottom - top <= self.height() + 24:
                return None
            return left, top, right, bottom
        except Exception:
            return None

    def _same_root_window(self, first, second):
        try:
            first = int(first or 0)
            second = int(second or 0)
            if not first or not second:
                return False
            if first == second:
                return True
            if win32gui is None:
                return False
            root_first = win32gui.GetAncestor(first, 2) or first
            root_second = win32gui.GetAncestor(second, 2) or second
            return int(root_first) == int(root_second)
        except Exception:
            return False

    def _log_overlay(self, note, **values):
        try:
            fg_hwnd = 0
            fg_class = ""
            fg_title = ""
            if win32gui is not None:
                fg_hwnd = win32gui.GetForegroundWindow()
                if fg_hwnd:
                    fg_class = win32gui.GetClassName(fg_hwnd) or ""
                    fg_title = win32gui.GetWindowText(fg_hwnd) or ""
            pieces = [f"{key}={value!r}" for key, value in values.items()]
            pieces.extend([f"fg_hwnd={fg_hwnd!r}", f"fg_class={fg_class!r}", f"fg_title={fg_title!r}"])
            with _MAIN_OVERLAY_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {note} {' '.join(pieces)}\n")
        except Exception:
            pass



