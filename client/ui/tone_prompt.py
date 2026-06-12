from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    import win32gui
except Exception:  # pragma: no cover - optional Windows dependency
    win32gui = None


class TonePrompt(QWidget):
    submitted = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Writing Assistant Tone")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(462, 154)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("tonePromptCard")
        root.addWidget(card)
        self.setStyleSheet(
            """
            QFrame#tonePromptCard {
                background: #f7efe5;
                border: 1px solid #dccbbb;
                border-radius: 18px;
            }
            QLabel#tonePromptTitle {
                color: #2f241f;
                font-weight: 800;
                font-size: 14px;
            }
            QLabel#tonePromptGuide {
                color: #4a382f;
                font-size: 12px;
            }
            QPushButton {
                border: 0;
                border-radius: 13px;
                padding: 6px 12px;
                background: #e8d4bf;
                color: #3f2f26;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #dcc1a7;
            }
            QLineEdit#tonePromptInput {
                background: #fffaf4;
                border: 1px solid #dccbbb;
                color: #2f241f;
                border-radius: 14px;
                padding: 9px 13px;
                font-size: 13px;
            }
            QLineEdit#tonePromptInput:focus {
                border: 1px solid #b86a3c;
            }
            QPushButton#tonePromptSubmit {
                background: #b86a3c;
                color: #fff8f2;
                min-width: 58px;
                min-height: 32px;
            }
            QPushButton#tonePromptSubmit:hover {
                background: #9f5730;
            }
            QPushButton#tonePromptClose {
                min-width: 26px;
                max-width: 26px;
                min-height: 26px;
                max-height: 26px;
                border-radius: 13px;
                padding: 0;
                background: #ead7cf;
            }
            """
        )

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("문체/말투 변경")
        title.setObjectName("tonePromptTitle")
        close_btn = QPushButton("X")
        close_btn.setObjectName("tonePromptClose")
        close_btn.setToolTip("닫기")
        close_btn.clicked.connect(self.hide)
        top.addWidget(title, 1)
        top.addWidget(close_btn, 0, Qt.AlignRight)
        layout.addLayout(top)

        guide = QLabel("변경할 문체/말투를 직접 입력해 주세요.")
        guide.setObjectName("tonePromptGuide")
        layout.addWidget(guide)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.tone_input = QLineEdit()
        self.tone_input.setObjectName("tonePromptInput")
        self.tone_input.setPlaceholderText("원하는 문체/말투")
        self.submit_btn = QPushButton("변경")
        self.submit_btn.setObjectName("tonePromptSubmit")
        self.submit_btn.clicked.connect(self._submit)
        self.tone_input.returnPressed.connect(self._submit)
        input_row.addWidget(self.tone_input, 1)
        input_row.addWidget(self.submit_btn)
        layout.addLayout(input_row)

    def _submit(self):
        tone = self.tone_input.text().strip()
        if not tone:
            self.tone_input.setFocus()
            return
        self.hide()
        QApplication.processEvents()
        self.submitted.emit(tone)

    def show_for_window(self, window_handle=None):
        self._move_to_target_center(window_handle)
        self.show()
        self.raise_()
        self.activateWindow()
        self.tone_input.setFocus()
        self.tone_input.selectAll()

    def show_for_overlay(self, overlay_rect, window_handle=None):
        self.reposition_for_overlay(overlay_rect, window_handle)
        self.show()
        self.raise_()
        self.activateWindow()
        self.tone_input.setFocus()
        self.tone_input.selectAll()

    def reposition_for_overlay(self, overlay_rect, window_handle=None):
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

    def _move_to_target_center(self, window_handle=None):
        rect = self._target_rect(window_handle)
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if rect is not None:
            left, top, right, bottom = rect
            margin = 12
            x = left + max(0, (right - left - self.width()) // 2)
            y = top + max(0, (bottom - top - self.height()) // 2)
            x = min(max(left + margin, x), max(left + margin, right - self.width() - margin))
            y = min(max(top + margin, y), max(top + margin, bottom - self.height() - margin))
            self.move(x, y)
        elif screen is not None:
            available = screen.availableGeometry()
            self.move(
                available.left() + max(0, (available.width() - self.width()) // 2),
                available.top() + max(0, (available.height() - self.height()) // 2),
            )

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
