import json
import sys
import threading
import time
import copy
from datetime import datetime
from pathlib import Path

import pyperclip
from PyQt5.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QPushButton, QStyle, QSystemTrayIcon

try:
    import win32gui
except Exception:  # pragma: no cover - optional Windows dependency
    win32gui = None

try:
    import win32con
except Exception:  # pragma: no cover - optional Windows dependency
    win32con = None

from client.app_settings import DEFAULT_SETTINGS, load_app_settings, save_app_settings
from client.config import APP_VERSION
from client.core.auth_api_client import AuthAPIClient, UnauthorizedError
from client.core.analyzer import TextAnalyzer
from client.core.line_structure import preserve_replacement_structure
from client.core.local_server import LocalServer
from client.input.clipboard_monitor import monitor_clipboard
from client.ui.main_overlay import MainOverlay
from client.ui.result_panel import ResultPanel


_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_UI_INPUT_EVENT_LOG_PATH = _LOG_DIR / "ui_input_events.log"
_REPLACEMENT_STRUCTURE_LOG_PATH = _LOG_DIR / "replacement_structure.log"


class SignalBridge(QObject):
    text_signal = pyqtSignal(object)
    auth_sync_signal = pyqtSignal(object)
    spell_check_signal = pyqtSignal(object)
    update_signal = pyqtSignal(object)


class CorrectionOverlayButton(QPushButton):
    def __init__(self, apply_callback):
        super().__init__("원본 수정")
        self.target = None
        self._last_raise_key = None
        self._dragging = False
        self._drag_candidate = False
        self._drag_offset = None
        self._drag_start_global = None
        self._did_drag = False
        self._manual_positions = {}
        self._resume_after = 0.0
        self._last_interaction_at = 0.0
        self._drag_threshold = 3
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(104, 38)
        self.clicked.connect(apply_callback)
        self.setStyleSheet(
            """
            QPushButton {
                background: #2563eb;
                border: 1px solid rgba(255, 255, 255, 0.62);
                border-radius: 8px;
                color: white;
                font-size: 13px;
                font-weight: 700;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: #1d4ed8;
            }
            QPushButton:disabled {
                background: #94a3b8;
                color: #eef2ff;
            }
            """
        )

    def follow_target(self, target):
        self.target = target
        self.update_position()

    def suspend(self, seconds=0.5, hide=True):
        try:
            delay = max(0.0, float(seconds))
        except Exception:
            delay = 0.5
        self._resume_after = max(self._resume_after, time.monotonic() + delay)
        if hide:
            self.hide()

    def is_suspended(self) -> bool:
        return time.monotonic() < self._resume_after

    def raise_key(self):
        if self.target is None:
            return None
        return (
            getattr(self.target, "mode", ""),
            getattr(self.target, "window_handle", None),
            getattr(self.target, "window_title", ""),
        )

    def update_position(self):
        if self._dragging:
            return

        rect = self._target_rect()
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        key = self.raise_key()

        if key in self._manual_positions:
            x, y = self._manual_positions[key]
            x = max(available.left() + 8, min(int(x), available.right() - self.width() - 8))
            y = max(available.top() + 8, min(int(y), available.bottom() - self.height() - 8))
            self.move(x, y)
            return

        if rect is None:
            x = available.right() - self.width() - 24
            y = available.bottom() - self.height() - 80
        else:
            left, top, right, bottom = rect
            margin_x = 12
            margin_y = 12
            # Keep overlay inside the target window bounds (top-right corner).
            x = right - self.width() - margin_x
            y = top + margin_y

            # Clamp to both the target window and the visible screen area.
            min_x = max(left + margin_x, available.left() + 8)
            max_x = min(right - self.width() - margin_x, available.right() - self.width() - 8)
            min_y = max(top + margin_y, available.top() + 8)
            max_y = min(bottom - self.height() - margin_y, available.bottom() - self.height() - 8)

            if max_x < min_x:
                x = min_x
            else:
                x = max(min_x, min(x, max_x))
            if max_y < min_y:
                y = min_y
            else:
                y = max(min_y, min(y, max_y))
        self.move(int(x), int(y))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_candidate = True
            self._dragging = False
            self._drag_offset = None
            self._drag_start_global = event.globalPos()
            self._did_drag = False
            self._last_interaction_at = time.monotonic()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_candidate:
            if self._drag_start_global is not None:
                moved = event.globalPos() - self._drag_start_global
                if not self._dragging and (
                    abs(moved.x()) >= self._drag_threshold or abs(moved.y()) >= self._drag_threshold
                ):
                    self._dragging = True
                    self._did_drag = True
                    self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        if self._dragging and self._drag_offset is not None:
            new_pos = event.globalPos() - self._drag_offset
            self._last_interaction_at = time.monotonic()
            screen = QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                x = max(available.left() + 8, min(new_pos.x(), available.right() - self.width() - 8))
                y = max(available.top() + 8, min(new_pos.y(), available.bottom() - self.height() - 8))
                self.move(int(x), int(y))
            else:
                self.move(new_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_candidate:
            self._last_interaction_at = time.monotonic()
            if self._dragging:
                key = self.raise_key()
                if key is not None:
                    self._manual_positions[key] = (self.x(), self.y())
                self._dragging = False
                self._drag_candidate = False
                self._drag_offset = None
                self._drag_start_global = None
                self._did_drag = False
                event.accept()
                return
            self._drag_candidate = False
            self._drag_offset = None
            self._drag_start_global = None
            self._did_drag = False
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            QTimer.singleShot(0, self.raise_near_target)
            QTimer.singleShot(30, self.raise_near_target)

    def is_recently_interacting(self, seconds=0.8) -> bool:
        return (time.monotonic() - self._last_interaction_at) < float(seconds)

    def is_target_foreground(self) -> bool:
        if self.target is None:
            return False
        if win32gui is None:
            return True
        try:
            overlay_hwnd = int(self.winId())
            foreground = win32gui.GetForegroundWindow()
            if foreground == overlay_hwnd:
                return True
        except Exception:
            pass
        target_hwnd = getattr(self.target, "window_handle", None)
        if not target_hwnd:
            if getattr(self.target, "mode", "") == "browser_extension":
                try:
                    from client.input.ai_grammary_text_reader import BROWSER_PROCESS_NAMES, get_foreground_hwnd, get_process_name

                    return get_process_name(get_foreground_hwnd()) in BROWSER_PROCESS_NAMES
                except Exception:
                    return False
            return True
        try:
            return foreground == target_hwnd
        except Exception:
            return False

    def has_active_modal_popup(self) -> bool:
        if self.target is None or win32gui is None:
            return False
        target_hwnd = getattr(self.target, "window_handle", None)
        try:
            foreground = win32gui.GetForegroundWindow()
            if foreground:
                class_name = (win32gui.GetClassName(foreground) or "").strip()
            else:
                class_name = ""

            # Native Windows confirm/save dialogs are commonly #32770.
            if class_name == "#32770":
                if not target_hwnd:
                    return True
                owner = win32gui.GetWindow(foreground, 4)  # GW_OWNER
                if owner == target_hwnd:
                    return True

            if not target_hwnd:
                return False

            # Most modal dialogs disable their owner window.
            try:
                if not win32gui.IsWindowEnabled(target_hwnd):
                    return True
            except Exception:
                pass

            # If an enabled popup exists for this target window, hide overlay
            # so document-level confirm dialogs can be clicked without overlap.
            enabled_popup = win32gui.GetWindow(target_hwnd, 6)  # GW_ENABLEDPOPUP
            if enabled_popup and enabled_popup != target_hwnd:
                return True

            if foreground and foreground != target_hwnd:
                owner = win32gui.GetWindow(foreground, 4)  # GW_OWNER
                if owner == target_hwnd:
                    return True

            # Catch custom non-#32770 dialogs owned by the target window.
            owned_visible_popups = []

            def _enum_windows(hwnd, _lparam):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return True
                    if hwnd == target_hwnd:
                        return True
                    owner_hwnd = win32gui.GetWindow(hwnd, 4)  # GW_OWNER
                    if owner_hwnd == target_hwnd:
                        owned_visible_popups.append(hwnd)
                except Exception:
                    return True
                return True

            try:
                win32gui.EnumWindows(_enum_windows, 0)
            except Exception:
                pass
            if owned_visible_popups:
                return True
            return False
        except Exception:
            return False

    def raise_near_target(self):
        self.raise_()
        self.ensure_normal_z_order()

    def ensure_normal_z_order(self):
        if win32gui is None or win32con is None:
            return
        try:
            hwnd = int(self.winId())
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def _target_rect(self):
        if win32gui is None or self.target is None:
            return None
        hwnd = getattr(self.target, "window_handle", None)
        if not hwnd:
            return None
        try:
            if not win32gui.IsWindow(hwnd):
                return None
            return win32gui.GetWindowRect(hwnd)
        except Exception:
            return None


class App:
    def __init__(self):
        self.qt_app = QApplication(sys.argv)
        self.load_app_font()
        self.local_server = LocalServer()
        self.api_client = AuthAPIClient()
        self._startup_server_error = ""
        self._server_started = False
        self.pending_signup_username = ""
        self.initialize_auth()

        self.settings = self.normalize_settings(load_app_settings())
        self.startup_clipboard_text = self.safe_paste()

        self.panel = ResultPanel(
            initial_dark_mode=self.settings.get("default_dark_mode", False)
        )
        self.word_main_overlay = MainOverlay()
        overlay_mode = self.settings.get("input_mode", "clipboard")
        self.word_main_overlay.set_active_mode(
            "drag" if overlay_mode == "selection" else overlay_mode
        )
        self.word_main_overlay.set_spelling_replace_mode(
            self.settings.get("replace_mode", False)
        )
        self.word_main_overlay.set_dark_mode(
            self.settings.get("default_dark_mode", False)
        )
        self.word_overlay_history_hwnd = None
        self.word_overlay_original_text = ""
        self.word_overlay_corrected_text = ""
        self.word_overlay_original_target = None
        self.word_overlay_corrected_target = None
        self.word_overlay_snapshot_locked = False
        self.word_main_overlay.evaluate_requested.connect(
            lambda: self.handle_word_overlay_action("evaluate")
        )
        self.word_main_overlay.evaluation_reason_requested.connect(
            self.handle_word_overlay_evaluation_reason
        )
        self.word_main_overlay.title_requested.connect(
            lambda: self.handle_word_overlay_action("title")
        )
        self.word_main_overlay.spelling_requested.connect(
            self.apply_correction_to_source
        )
        self.word_main_overlay.tone_requested.connect(
            lambda: self.handle_word_overlay_action("tone")
        )
        self.word_main_overlay.tone_submitted.connect(
            self.apply_word_overlay_tone_change
        )
        self.word_main_overlay.summary_requested.connect(
            lambda: self.handle_word_overlay_action("summary")
        )
        self.word_main_overlay.settings_save_requested.connect(
            self.handle_word_overlay_settings_save
        )
        self.word_main_overlay.open_panel_requested.connect(self.show_panel)
        self.word_main_overlay.history_requested.connect(
            self.show_word_overlay_history
        )
        self.word_main_overlay.undo_requested.connect(
            self.undo_last_word_overlay_correction
        )
        self.word_main_overlay.redo_requested.connect(
            self.redo_last_word_overlay_correction
        )
        self.word_main_overlay.title_insert_requested.connect(
            self.insert_word_overlay_title
        )
        self.word_main_overlay.summary_copy_requested.connect(self.safe_copy)
        self.word_overlay_timer = QTimer(self.qt_app)
        self.word_overlay_timer.setInterval(60)
        self.word_overlay_timer.timeout.connect(self.update_word_overlay_presence)
        self.word_overlay_timer.start()
        self.panel.set_default_dark_mode_checked(
            self.settings.get("default_dark_mode", False)
        )
        self.panel.set_history_enabled_checked(
            self.settings.get("history_enabled", False)
        )
        self.panel.set_replace_mode_checked(
            self.settings.get("replace_mode", False)
        )

        self.analyzer = TextAnalyzer()
        self.output_applier = None
        self.last_input = ""
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.current_history_request_id = None
        self.current_history_source_text = ""
        self.update_info = None
        self.update_notice_shown = False
        self.last_output_target = None
        self.suppress_replacement_echo_until = 0.0
        self.suppress_replacement_echo_text = ""
        self.correction_overlay_suppressed = False
        self.last_browser_extension_event_at = 0.0
        self.spell_check_in_progress = False
        self.spell_check_pending = False
        self.pending_apply_correction = False
        self.spell_check_request_id = 0
        self.spell_check_debounce_ms = 2000
        self.spell_check_timer = QTimer()
        self.spell_check_timer.setSingleShot(True)
        self.spell_check_timer.timeout.connect(self.run_spell_check)
        self.active_input_mode = self.settings.get("input_mode", "clipboard")
        self.clipboard_thread = None
        self.realtime_thread = None
        self.last_logged_keys = set()
        self.last_local_log_keys = set()

        self.signals = SignalBridge()
        self.signals.text_signal.connect(self.handle_input_event)
        self.signals.auth_sync_signal.connect(self.handle_background_auth_sync_result)
        self.signals.spell_check_signal.connect(self.handle_spell_check_result)
        self.signals.update_signal.connect(self.handle_update_check_result)
        # Overlay mode is deprecated; use in-panel apply action only.
        self.correction_overlay = None
        self.overlay_timer = None

        self.panel.set_input_mode(self.active_input_mode)
        self._sync_apply_correction_button_visibility()
        self.reset_session_state()

        self.panel.copy_btn.clicked.connect(self.copy_result)
        self.panel.refresh_btn.clicked.connect(self.run_spell_check)
        self.panel.apply_correction_btn.clicked.connect(self.apply_correction_to_source)
        self.panel.quit_btn.clicked.connect(self.quit_app)
        self.panel.evaluate_btn.clicked.connect(self.run_evaluation)
        self.panel.recommend_title_btn.clicked.connect(self.run_title_recommendation)
        self.panel.run_summary_btn.clicked.connect(self.run_summary)
        self.panel.summary_history_btn.clicked.connect(lambda: self.show_history(3))
        self.panel.run_tone_btn.clicked.connect(self.run_tone_change)
        self.panel.save_settings_btn.clicked.connect(self.save_settings)
        self.panel.close_settings_btn.clicked.connect(self.panel.close_settings_page)
        self.panel.login_btn.clicked.connect(self.handle_login_button)
        self.panel.header_history_btn.clicked.connect(lambda: self.show_history(0))
        self.panel.header_update_btn.clicked.connect(self.show_update_notice)
        self.panel.login_submit_btn.clicked.connect(self.handle_login_submit)
        self.panel.signup_submit_btn.clicked.connect(self.handle_signup_submit)
        self.panel.account_manage_btn.clicked.connect(self.handle_account_manage_button)
        self.panel.account_verify_submit_btn.clicked.connect(self.handle_account_verify_submit)
        self.panel.account_save_btn.clicked.connect(lambda: self.handle_account_update())
        self.panel.account_name_edit_btn.clicked.connect(lambda: self.handle_account_update("display_name"))
        self.panel.account_username_edit_btn.clicked.connect(lambda: self.handle_account_update("username"))
        self.panel.account_password_edit_btn.clicked.connect(lambda: self.handle_account_update("password"))
        self.panel.account_delete_btn.clicked.connect(self.confirm_account_delete)
        self.panel.tabs.currentChanged.connect(self.handle_tab_changed)
        self.panel.history_request_action_callback = self.handle_history_request_action
        self.panel.history_delete_callback = self.confirm_history_delete
        self.panel.history_page_refresh_callback = lambda: self.show_history(0)
        self.panel.history_delete_all_btn.clicked.connect(self.confirm_delete_all_history_requests)
        self.panel.clipboard_mode_checkbox.toggled.connect(self.handle_live_input_mode_change)
        self.panel.realtime_mode_checkbox.toggled.connect(self.handle_live_input_mode_change)
        self.panel.selection_mode_checkbox.toggled.connect(self.handle_live_input_mode_change)
        self.panel.replace_mode_checkbox.toggled.connect(self.handle_live_input_mode_change)

        self.init_tray()
        self.update_login_state()
        QTimer.singleShot(0, self.start_restored_login_sync)
        QTimer.singleShot(200, self.start_update_check)

    def initialize_auth(self):
        self.api_client.try_restore_session()

    def load_app_font(self):
        self.qt_app.setFont(QFont("Malgun Gothic", 10))

    def init_tray(self):
        tray_icon = self.qt_app.style().standardIcon(QStyle.SP_FileDialogInfoView)
        self.tray = QSystemTrayIcon(tray_icon, self.qt_app)
        self.tray.setToolTip("Writing Assistant 실행 중")
        self.tray.activated.connect(self.handle_tray_activation)

        menu = QMenu()
        show_action = QAction("열기")
        self.login_action = QAction("로그인")
        quit_action = QAction("종료")

        show_action.triggered.connect(self.show_panel)
        self.login_action.triggered.connect(self.handle_login_button)
        quit_action.triggered.connect(self.quit_app)

        menu.addAction(show_action)
        menu.addAction(self.login_action)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def start(self):
        self.reset_session_state()
        self.show_panel()

        self.clipboard_thread = threading.Thread(
            target=self.run_monitor,
            args=(self.startup_clipboard_text,),
            daemon=True,
        )
        self.clipboard_thread.start()
        self.ensure_realtime_monitor_started()

        sys.exit(self.qt_app.exec_())

    def start_update_check(self):
        threading.Thread(target=self.run_update_check, daemon=True).start()

    def run_update_check(self):
        try:
            data = self.api_client.get_client_version_info()
        except Exception:
            return

        latest_version = str(data.get("latest_version", "") or "").strip()
        if not latest_version or not self._is_version_newer(latest_version, APP_VERSION):
            self.signals.update_signal.emit({"available": False})
            return

        self.signals.update_signal.emit(
            {
                "available": True,
                "current_version": APP_VERSION,
                "latest_version": latest_version,
                "minimum_version": str(data.get("minimum_version", "") or "").strip(),
                "download_url": str(data.get("download_url", "") or "").strip(),
                "message": str(data.get("message", "") or "").strip(),
            }
        )

    def handle_update_check_result(self, result):
        if not isinstance(result, dict):
            return
        if not bool(result.get("available")):
            self.update_info = None
            self.panel.set_update_available(False)
            return

        self.update_info = result
        self.panel.set_update_available(
            True,
            latest_version=result.get("latest_version", ""),
            message=result.get("message", ""),
            download_url=result.get("download_url", ""),
        )
        if not self.update_notice_shown:
            self.update_notice_shown = True
            self.show_update_notice()

    def show_update_notice(self):
        if not isinstance(self.update_info, dict):
            return
        latest_version = str(self.update_info.get("latest_version", "") or "").strip()
        current_version = str(self.update_info.get("current_version", APP_VERSION) or APP_VERSION).strip()
        notice_text = self.panel.get_update_notice_text()
        lines = [
            f"현재 버전: {current_version}",
        ]
        if latest_version:
            lines.append(f"최신 버전: {latest_version}")
        if notice_text:
            lines.append("")
            lines.append(notice_text)
        self.panel.show_notice("업데이트 알림", "\n".join(lines).strip())

    def _is_version_newer(self, latest_version, current_version):
        return self._version_key(latest_version) > self._version_key(current_version)

    def _version_key(self, value):
        parts = []
        for chunk in str(value or "").replace("-", ".").split("."):
            digits = "".join(char for char in chunk if char.isdigit())
            if digits:
                parts.append(int(digits))
            else:
                parts.append(0)
        while len(parts) < 4:
            parts.append(0)
        return tuple(parts[:4])

    def run_monitor(self, initial_text):
        from client.input.ai_grammary_text_reader import UniversalActiveTextReader

        clipboard_reader = UniversalActiveTextReader()

        def callback(text):
            event = {
                "source": "clipboard",
                "window_title": "",
                "text": text,
            }
            snapshot = clipboard_reader.poll_snapshot(selection_only=True)
            if snapshot is None or str(snapshot.text or "").strip() != str(text or "").strip():
                snapshot = clipboard_reader.poll_snapshot(selection_only=False)
            if snapshot is not None and str(snapshot.text or "").strip() == str(text or "").strip():
                event.update(
                    {
                        "window_title": snapshot.window_title,
                        "reader": snapshot.reader_name,
                        "window_handle": snapshot.window_handle,
                        "style_info": snapshot.style_info,
                    }
                )
            self.signals.text_signal.emit(event)

        monitor_clipboard(callback, initial_text=initial_text)

    def update_word_overlay_presence(self):
        if win32gui is None or not hasattr(self, "word_main_overlay"):
            return
        if self.word_main_overlay.has_overlay_focus():
            return
        try:
            hwnd = int(win32gui.GetForegroundWindow() or 0)
            if not hwnd or not win32gui.IsWindow(hwnd):
                self.word_main_overlay.hide_with_reason("word_window_missing")
                return
            if self._has_document_modal_popup(hwnd):
                if self.word_main_overlay.isVisible():
                    self.word_main_overlay.hide_with_reason("document_modal_popup")
                return
            from client.input.ai_grammary_text_reader import HWP_PROCESS_NAMES, WORD_PROCESS_NAMES, get_process_name

            process_name = get_process_name(hwnd)
            if process_name in WORD_PROCESS_NAMES:
                reader_name = "word"
                self.word_main_overlay.set_action_buttons_enabled(
                    evaluate=True,
                    title=True,
                    spelling=True,
                    tone=True,
                    summary=True,
                )
            elif process_name in HWP_PROCESS_NAMES:
                reader_name = "hwp"
                self.word_main_overlay.set_action_buttons_enabled(
                    evaluate=True,
                    title=True,
                    spelling=True,
                    tone=True,
                    summary=True,
                )
            else:
                if self.word_main_overlay.isVisible():
                    self.word_main_overlay.hide_with_reason("word_not_foreground")
                return
            if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
                self.word_main_overlay.hide_with_reason("word_window_hidden")
                return
            if reader_name != "word" or self.word_overlay_history_hwnd not in {None, hwnd}:
                self.word_main_overlay.set_undo_available(False)
                self.word_main_overlay.set_redo_available(False)
            self.word_main_overlay.show_for_target(reader_name, hwnd)
        except Exception:
            if self.word_main_overlay.isVisible():
                self.word_main_overlay.hide_with_reason("word_overlay_update_failed")

    def _has_document_modal_popup(self, hwnd):
        if win32gui is None or not hwnd:
            return False
        try:
            class_name = (win32gui.GetClassName(hwnd) or "").strip()
            if class_name == "#32770":
                return True
            owner = int(win32gui.GetWindow(hwnd, 4) or 0)  # GW_OWNER
            if owner and owner != int(hwnd):
                return True
            root = int(win32gui.GetAncestor(hwnd, 2) or hwnd)
            if root and root != int(hwnd):
                root_enabled_popup = int(win32gui.GetWindow(root, 6) or 0)  # GW_ENABLEDPOPUP
                if root_enabled_popup and root_enabled_popup != root:
                    return True
            return False
        except Exception:
            return False

    def _refresh_word_overlay_input(self):
        hwnd = int(getattr(self.word_main_overlay, "_last_window_handle", 0) or 0)
        if not hwnd:
            return False
        try:
            import pythoncom
            import win32com.client as win32

            from client.input.ai_grammary_text_reader import ActiveWordReader, get_window_title
            from client.input.output_applier import OutputTarget

            pythoncom.CoInitialize()
            word = win32.GetActiveObject("Word.Application")
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                return False
            reader = ActiveWordReader()
            text = reader._read_paragraph_text(document)
            if not str(text or "").strip():
                return False
            style_info = reader._read_style_info_from_document(document)
            self.last_input = text
            self.last_correction_source_text = text
            self.current_history_source_text = text
            self.last_output_target = OutputTarget(
                mode="word",
                window_handle=hwnd,
                window_title=get_window_title(hwnd),
                style_info=style_info,
            )
            self.panel.set_original_text(text)
            self._capture_original_snapshot(text, self.last_output_target)
            return True
        except Exception as exc:
            self.word_main_overlay.show_status(f"Word 읽기 실패: {exc}", auto_hide_ms=1600)
            return False

    def _refresh_hwp_overlay_input(self):
        hwnd = int(getattr(self.word_main_overlay, "_last_window_handle", 0) or 0)
        if not hwnd:
            return False
        try:
            from client.input.ai_grammary_text_reader import ActiveHwpReader, get_window_title
            from client.input.output_applier import OutputTarget

            reader = ActiveHwpReader()
            if not reader._is_hwp_window(hwnd):
                return False
            reader._log_hwp_window(hwnd)
            hwp = reader._get_hwp_object_from_native_om(hwnd)
            if hwp is None:
                hwp = reader._get_active_hwp_object()
            if hwp is None:
                hwp = reader._get_hwp_object_from_rot()
            com_text = reader._read_text_via_hwp_com(hwp) if hwp is not None else ""
            uia_text = reader._read_text_via_uia(hwnd)
            text, read_method = reader._choose_hwp_live_text(com_text, uia_text)
            text = reader._stabilize_hwp_text(text)
            if not str(text or "").strip():
                return False
            reader._last_read_method = read_method
            reader._last_hwp_text = str(text or "")
            reader._last_hwp_style_info = (
                reader._read_hwp_style_info(hwp)
                if read_method == "com" and hwp is not None
                else {}
            )
            style_info = reader.read_style_info() or {}
            self.last_input = text
            self.last_correction_source_text = text
            self.current_history_source_text = text
            self.last_output_target = OutputTarget(
                mode="hwp",
                window_handle=hwnd,
                window_title=get_window_title(hwnd),
                style_info=style_info,
            )
            self.panel.set_original_text(text)
            self._capture_original_snapshot(text, self.last_output_target)
            return True
        except Exception as exc:
            self.word_main_overlay.show_status(f"한글 읽기 실패: {exc}", auto_hide_ms=1600)
            return False

    def _refresh_overlay_input_for_reader(self, reader_name):
        reader_name = str(reader_name or "").strip().lower()
        if reader_name == "hwp":
            return self._refresh_hwp_overlay_input()
        return self._refresh_word_overlay_input()

    def _overlay_reader_display_name(self, reader_name):
        return "한글" if str(reader_name or "").strip().lower() == "hwp" else "Word"

    def add_word_overlay_notification(self, message, error=False):
        self.word_main_overlay.add_notification(message, error=error)

    def show_word_overlay_history(self):
        self.show_panel()
        self.show_history(0)

    def undo_last_word_overlay_correction(self):
        self._replace_word_overlay_snapshot("undo")

    def redo_last_word_overlay_correction(self):
        self._replace_word_overlay_snapshot("redo")

    def _capture_original_snapshot(self, source_text, target):
        displayed_text = ""
        try:
            displayed_text = self.panel.text_box.toPlainText()
        except Exception:
            pass
        text = str(displayed_text or source_text or "")
        if not text.strip() or target is None:
            self.word_overlay_history_hwnd = None
            self.word_overlay_original_text = ""
            self.word_overlay_corrected_text = ""
            self.word_overlay_original_target = None
            self.word_overlay_corrected_target = None
            self.word_overlay_snapshot_locked = False
            self.word_main_overlay.set_undo_available(False)
            self.word_main_overlay.set_redo_available(False)
            return
        self.word_overlay_original_text = text
        self.word_overlay_corrected_text = ""
        self.word_overlay_original_target = copy.deepcopy(target)
        self.word_overlay_corrected_target = None
        self.word_overlay_history_hwnd = int(getattr(target, "window_handle", 0) or 0)
        self.word_overlay_snapshot_locked = False
        self.word_main_overlay.set_undo_available(True)
        self.word_main_overlay.set_redo_available(False)

    def _snapshot_target_for_current_text(self, target, current_text):
        snapshot_target = copy.deepcopy(target)
        if snapshot_target is None:
            return None
        style_info = dict(getattr(snapshot_target, "style_info", None) or {})
        if not style_info.get("selection_mode"):
            return snapshot_target

        text = str(current_text or "")
        if snapshot_target.mode == "word":
            start = style_info.get("word_selection_start")
            if start is not None:
                style_info["word_selection_end"] = int(start) + len(text)
                style_info["selection_text"] = text
        elif snapshot_target.mode == "hwp":
            start_pos = style_info.get("hwp_selection_start_pos")
            if isinstance(start_pos, (list, tuple)) and len(start_pos) >= 2:
                start_para, start_col = int(start_pos[0]), int(start_pos[1])
                lines = text.split("\n")
                end_pos = (
                    (start_para, start_col + len(lines[0]))
                    if len(lines) == 1
                    else (start_para + len(lines) - 1, len(lines[-1]))
                )
                style_info["hwp_selection_end_pos"] = end_pos
                style_info["hwp_selection_length"] = len(text)
                style_info["selection_text"] = text
                style_info["_source_text"] = text
        elif snapshot_target.mode == "notepad":
            start = style_info.get("selection_start")
            if start is not None:
                style_info["selection_end"] = int(start) + len(text)
                style_info["selection_text"] = text

        snapshot_target.style_info = style_info
        return snapshot_target

    def _commit_replacement_snapshot(self, corrected_text, applied_target=None):
        target = applied_target or self.last_output_target
        if target is None or not str(corrected_text or "").strip():
            return
        original_text = str(
            self.word_overlay_original_text
            or self.last_correction_source_text
            or self.panel.text_box.toPlainText()
            or self.last_input
            or ""
        )
        if not original_text.strip():
            return
        original_style_target = self.word_overlay_original_target or copy.deepcopy(target)
        self.word_overlay_original_text = original_text
        self.word_overlay_corrected_text = str(corrected_text)
        self.word_overlay_original_target = self._snapshot_target_for_current_text(
            original_style_target,
            corrected_text,
        )
        self.word_overlay_corrected_target = self._snapshot_target_for_current_text(
            target,
            original_text,
        )
        self.word_overlay_history_hwnd = int(getattr(target, "window_handle", 0) or 0)
        self.word_overlay_snapshot_locked = True
        self.word_main_overlay.set_undo_available(True)
        self.word_main_overlay.set_redo_available(True)

    def _begin_word_overlay_spelling_snapshot(self, source_text):
        target = self.last_output_target
        if target is None:
            return
        target_hwnd = int(getattr(target, "window_handle", 0) or 0)
        if (
            self.word_overlay_snapshot_locked
            and self.word_overlay_history_hwnd == target_hwnd
            and self.word_overlay_original_text
            and self.word_overlay_corrected_text
        ):
            return
        if (
            self.word_overlay_original_target is not None
            and self.word_overlay_original_text == str(source_text or "")
            and self.word_overlay_history_hwnd == target_hwnd
        ):
            return
        panel_source_text = ""
        try:
            panel_source_text = self.panel.text_box.toPlainText()
        except Exception:
            pass
        self.word_overlay_original_text = str(panel_source_text or source_text or "")
        self.word_overlay_corrected_text = ""
        self.word_overlay_original_target = copy.deepcopy(target)
        self.word_overlay_corrected_target = None
        self.word_overlay_history_hwnd = target_hwnd
        self.word_overlay_snapshot_locked = False
        self.word_main_overlay.set_undo_available(False)
        self.word_main_overlay.set_redo_available(False)

    def _complete_word_overlay_spelling_snapshot(self, source_text, corrected_text):
        target = self.last_output_target
        if target is None:
            return
        target_hwnd = int(getattr(target, "window_handle", 0) or 0)
        if (
            self.word_overlay_snapshot_locked
            and self.word_overlay_history_hwnd == target_hwnd
            and self.word_overlay_original_text
            and self.word_overlay_corrected_text
        ):
            return
        if not self.word_overlay_original_text:
            self.word_overlay_original_text = str(source_text or "")
        if self.word_overlay_original_target is None:
            self.word_overlay_original_target = copy.deepcopy(target)
        self.word_overlay_corrected_text = str(corrected_text or "")
        self.word_overlay_history_hwnd = target_hwnd
        self.word_main_overlay.set_redo_available(
            bool(self.word_overlay_corrected_text.strip())
        )

    def _replace_word_overlay_snapshot(self, action):
        if action not in {"undo", "redo"}:
            return
        text = (
            self.word_overlay_original_text
            if action == "undo"
            else self.word_overlay_corrected_text
        )
        target = (
            self.word_overlay_original_target
            if action == "undo"
            else self.word_overlay_corrected_target or self.word_overlay_original_target
        )
        if not str(text or "").strip() or target is None:
            message = "되돌릴 원문이 없습니다." if action == "undo" else "다시 적용할 교정문이 없습니다."
            self.word_main_overlay.show_status(message, auto_hide_ms=1400)
            return
        try:
            if action == "redo" and self.word_overlay_corrected_target is None:
                self.last_output_target = target
                if not self._apply_correction_text(text):
                    raise RuntimeError("교정문 원본 치환에 실패했습니다.")
                target = self.word_overlay_corrected_target or target
            else:
                self.get_output_applier().apply(target, text)
            self.last_output_target = target
            self.last_corrected_text = text if action == "redo" else self.word_overlay_corrected_text
            self.suppress_replacement_echo_text = text
            self.suppress_replacement_echo_until = time.monotonic() + 4.0
            is_undo = action == "undo"
            self.word_main_overlay.set_undo_available(not is_undo)
            self.word_main_overlay.set_redo_available(is_undo)
            self.word_overlay_history_hwnd = int(getattr(target, "window_handle", 0) or 0)
            message = "되돌리기 완료" if is_undo else "다시 실행 완료"
            self.word_main_overlay.show_status(message, auto_hide_ms=1200)
            self.add_word_overlay_notification(message)
        except Exception as exc:
            message = "되돌리기 실패" if action == "undo" else "다시 실행 실패"
            self.word_main_overlay.show_status(message, auto_hide_ms=1400)
            self.add_word_overlay_notification(
                f"{message}: {type(exc).__name__}",
                error=True,
            )

    def handle_word_overlay_settings_save(self, mode, replace_mode=False):
        input_mode = "selection" if mode == "drag" else "realtime"
        settings = self.settings.copy()
        settings["input_mode"] = input_mode
        settings["replace_mode"] = bool(replace_mode) and input_mode == "realtime"
        self.apply_settings_state(settings)
        if self.is_logged_in():
            try:
                self.save_remote_settings()
            except Exception as exc:
                self.add_word_overlay_notification(
                    f"설정 동기화 실패: {type(exc).__name__}",
                    error=True,
                )
        self.word_main_overlay.set_active_mode(
            "drag" if input_mode == "selection" else input_mode
        )
        self.word_main_overlay.set_spelling_replace_mode(settings["replace_mode"])
        self.word_main_overlay.show_status("저장됨")
        self.add_word_overlay_notification("오버레이 설정 저장 완료")

    def apply_word_overlay_tone_change(self, tone):
        tone = str(tone or "").strip()
        if not tone:
            return
        self.word_main_overlay.show_busy("문체 변경 진행중")
        self.add_word_overlay_notification("문체 변경 시작")
        QApplication.processEvents()
        try:
            result = self.analyzer.analyze_tone_change(self.last_input, tone)
            self.panel.set_tone_result(result)
            self.save_history_log(
                feature_type=4,
                input_text=self._history_source_text(),
                request_id=self.current_history_request_id,
                output_text=result,
                tone=tone,
            )
            replacement = self._prepare_replacement_text(result)
            self.get_output_applier().apply(self.last_output_target, replacement)
            self.last_corrected_text = replacement
            self._commit_replacement_snapshot(replacement, self.last_output_target)
            self.word_main_overlay.show_text_result("문체 변환 결과", result)
            self.word_main_overlay.show_status("문체 변경 완료")
            self.add_word_overlay_notification("문체 변경 완료")
        except Exception as exc:
            self.word_main_overlay.show_status(f"문체 변경 실패: {exc}", auto_hide_ms=1800)
            self.add_word_overlay_notification(
                f"문체 변경 실패: {type(exc).__name__}",
                error=True,
            )
        finally:
            self.word_main_overlay.hide_busy()

    def handle_word_overlay_action(self, action):
        reader_name = str(getattr(self.word_main_overlay, "_last_reader_name", "") or "word")
        if not self._refresh_overlay_input_for_reader(reader_name):
            self.word_main_overlay.show_status("텍스트 없음", auto_hide_ms=1200)
            self.add_word_overlay_notification(f"{self._overlay_reader_display_name(reader_name)} 텍스트를 찾을 수 없음", error=True)
            return
        if action == "tone":
            self.word_main_overlay.show_tone_prompt()
            return

        self.word_main_overlay.show_busy(
            {
                "evaluate": "평가 진행중",
                "title": "제목 추천중",
                "summary": "요약 진행중",
            }.get(action, "처리중")
        )
        action_label = {
            "evaluate": "평가",
            "title": "제목 추천",
            "summary": "요약",
        }.get(action, "작업")
        self.add_word_overlay_notification(f"{action_label} 시작")
        QApplication.processEvents()
        try:
            if action == "evaluate":
                result = self.run_evaluation()
                if result:
                    score = self._parse_score(result) or 0
                    self.word_main_overlay.show_evaluation_score(
                        score,
                        "",
                    )
                    self.add_word_overlay_notification("평가 완료")
                else:
                    self.add_word_overlay_notification("평가 실패", error=True)
            elif action == "title":
                result = self.run_title_recommendation()
                if result:
                    self.word_main_overlay.show_title_confirmation(result)
                    self.add_word_overlay_notification("제목 추천 완료")
                else:
                    self.add_word_overlay_notification("제목 추천 실패", error=True)
            elif action == "summary":
                result = self.run_summary()
                if result:
                    self.word_main_overlay.show_summary_result(
                        self._strip_result_heading(result)
                    )
                    self.add_word_overlay_notification("요약 완료")
                else:
                    self.add_word_overlay_notification("요약 실패", error=True)
        finally:
            self.word_main_overlay.hide_busy()

    def insert_word_overlay_title(self, title):
        clean_title = str(title or "").strip()
        if not clean_title:
            return
        target = self.last_output_target
        if target and target.mode == "hwp":
            try:
                if not self._refresh_hwp_overlay_input():
                    raise RuntimeError("활성 한글 문서를 찾을 수 없습니다.")
                self.get_output_applier().insert_hwp_title(
                    self.last_output_target.window_handle,
                    clean_title,
                )
                self.word_main_overlay.show_status("제목 삽입 완료")
            except Exception as exc:
                self.word_main_overlay.show_status(f"제목 삽입 실패: {exc}", auto_hide_ms=1800)
            return
        try:
            import pythoncom
            import win32com.client as win32

            pythoncom.CoInitialize()
            word = win32.GetActiveObject("Word.Application")
            document = getattr(word, "ActiveDocument", None)
            if document is None:
                raise RuntimeError("활성 Word 문서를 찾을 수 없습니다.")
            insert_text = f"{clean_title}\r\r"
            document.Range(Start=0, End=0).InsertBefore(insert_text)
            title_range = document.Range(Start=0, End=len(clean_title))
            title_range.Font.Bold = True
            title_range.Font.Size = 14
            document.Paragraphs.Item(1).Range.ParagraphFormat.Alignment = 1
            self.word_main_overlay.show_status("제목 삽입 완료")
        except Exception as exc:
            self.word_main_overlay.show_status(f"제목 삽입 실패: {exc}", auto_hide_ms=1800)

    def run_realtime_monitor(self):
        from client.input.realtime_text_monitor import monitor_realtime_text

        def callback(event):
            self.signals.text_signal.emit(event)

        monitor_realtime_text(callback, get_active_mode=lambda: self.active_input_mode)

    def ensure_realtime_monitor_started(self):
        if self.active_input_mode not in {"realtime", "selection"}:
            return
        if self.realtime_thread and self.realtime_thread.is_alive():
            return
        self.realtime_thread = threading.Thread(
            target=self.run_realtime_monitor,
            daemon=True,
        )
        self.realtime_thread.start()

    def handle_input_event(self, event):
        if not isinstance(event, dict):
            return

        source = event.get("source", "")
        if source != self.active_input_mode:
            return

        text = event.get("text", "")
        reader_name = str(event.get("reader", "")).strip()
        self._log_input_event(event, text, reader_name)
        if source == "realtime" and reader_name.endswith("_closed"):
            self.reset_session_state()
            self.panel.set_active_window_title("")
            return
        if source == "realtime" and not text:
            if not self.last_input:
                self.panel.set_active_window_title(event.get("window_title", ""))
                self.last_output_target = None
                self.panel.show_text_unavailable_placeholder()
            return

        if self._should_ignore_blank_line_downgrade(reader_name, text):
            self._log_input_event(event, text, reader_name, note="ignored_blank_line_downgrade")
            return
        if self._is_internal_selection_sentinel(text):
            self._log_input_event(event, text, reader_name, note="ignored_internal_selection_sentinel")
            return

        if not text or text == self.last_input:
            return

        if self._is_replacement_echo(text):
            return

        self.panel.set_active_window_title(event.get("window_title", ""))
        if reader_name == "browser_extension":
            self.last_browser_extension_event_at = time.monotonic()
        self.last_input = text
        self.last_corrected_text = ""
        self.current_history_request_id = None
        self.current_history_source_text = text
        self.correction_overlay_suppressed = False
        self.last_output_target = self._build_output_target(event)
        self.panel.set_original_text(text)
        self._capture_original_snapshot(text, self.last_output_target)
        self.schedule_spell_check()
        self.update_correction_overlay()

    def reset_session_state(self):
        self.last_input = ""
        self.last_corrected_text = ""
        self.last_correction_source_text = ""
        self.current_history_request_id = None
        self.current_history_source_text = ""
        self.last_output_target = None
        self.suppress_replacement_echo_until = 0.0
        self.suppress_replacement_echo_text = ""
        self.correction_overlay_suppressed = False
        self.spell_check_timer.stop()
        self.spell_check_in_progress = False
        self.spell_check_pending = False
        self.pending_apply_correction = False
        self.spell_check_request_id += 1
        self._set_live_reading_paused(False)
        if hasattr(self, "word_main_overlay"):
            self.word_overlay_history_hwnd = None
            self.word_overlay_original_text = ""
            self.word_overlay_corrected_text = ""
            self.word_overlay_original_target = None
            self.word_overlay_corrected_target = None
            self.word_overlay_snapshot_locked = False
            self.word_main_overlay.set_undo_available(False)
            self.word_main_overlay.set_redo_available(False)
        self.panel.reset_text_tab()
        self.panel.clear_spell_result()
        self.panel.clear_summary_result()
        self.panel.clear_tone_result()
        self.panel.set_active_window_title("")
        self.update_correction_overlay()

    def copy_result(self):
        text = self.panel.get_current_text()
        if text:
            self.safe_copy(text)

    def show_panel(self):
        self.panel.showNormal()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def handle_tray_activation(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_panel()

    def schedule_spell_check(self):
        if not self.last_input:
            return
        if self.spell_check_in_progress:
            return
        self.spell_check_timer.start(self.spell_check_debounce_ms)

    def run_spell_check(self):
        if not self.last_input:
            return
        self.spell_check_timer.stop()
        if self.spell_check_in_progress:
            return
        source_text = self.last_input
        self.spell_check_request_id += 1
        request_id = self.spell_check_request_id
        self.spell_check_in_progress = True
        self.spell_check_pending = False
        self.last_correction_source_text = source_text
        self.current_history_source_text = source_text
        self.last_corrected_text = ""
        self._begin_word_overlay_spelling_snapshot(source_text)
        self.panel.set_spell_result("OpenAI 맞춤법 요청 중입니다...")
        self.update_correction_overlay()

        worker = threading.Thread(
            target=self._run_spell_check_worker,
            args=(request_id, source_text),
            daemon=True,
        )
        worker.start()

    def _run_spell_check_worker(self, request_id, source_text):
        try:
            spelling_payload = self.analyzer.get_spelling_result_payload(source_text)
            self.signals.spell_check_signal.emit(
                {
                    "request_id": request_id,
                    "source_text": source_text,
                    "payload": spelling_payload,
                }
            )
        except Exception as exc:
            self.signals.spell_check_signal.emit(
                {
                    "request_id": request_id,
                    "source_text": source_text,
                    "error": str(exc),
                }
            )

    def handle_spell_check_result(self, result_data):
        if not isinstance(result_data, dict):
            return
        if result_data.get("request_id") != self.spell_check_request_id:
            return
        self.spell_check_in_progress = False

        source_text = result_data.get("source_text", "")
        if source_text != self.last_input:
            self.spell_check_pending = False
            return

        if result_data.get("error"):
            self.last_corrected_text = ""
            self.pending_apply_correction = False
            self.panel.set_spell_result(f"OpenAI 맞춤법 요청 실패:\n\n{result_data.get('error')}")
            self.update_correction_overlay()
            return

        spelling_payload = result_data.get("payload") or {}
        result = spelling_payload.get("formatted", "")
        spelling_feedback = spelling_payload.get("spelling_feedback") or self.analyzer.TEMP_SPELLING_FEEDBACK
        self.last_corrected_text = spelling_payload.get("corrected", "")
        self._complete_word_overlay_spelling_snapshot(
            source_text,
            self.last_corrected_text,
        )
        self.panel.set_spell_result(result)
        self.update_correction_overlay()
        self.save_history_log(
            feature_type=2,
            input_text=self.last_correction_source_text,
            request_id=self.current_history_request_id,
            output_text=self.last_corrected_text,
            spelling_feedback=spelling_feedback,
        )
        if self.pending_apply_correction and self._can_apply_source_correction():
            self.pending_apply_correction = False
            self._apply_correction_text(self.last_corrected_text)

    def _set_live_reading_paused(self, paused: bool):
        try:
            from client.input.realtime_text_monitor import set_polling_paused

            set_polling_paused(paused)
        except Exception:
            pass

    def _is_internal_selection_sentinel(self, text: str) -> bool:
        return "AI_GRAMMARY_NO_SELECTION_" in str(text or "")

    def run_spell_check_sync(self):
        if not self.last_input:
            return
        self.last_correction_source_text = self.last_input
        self.current_history_source_text = self.last_correction_source_text
        self._begin_word_overlay_spelling_snapshot(self.last_correction_source_text)
        try:
            spelling_payload = self.analyzer.get_spelling_result_payload(self.last_correction_source_text)
        except Exception as exc:
            self.last_corrected_text = ""
            self.panel.set_spell_result(f"OpenAI 맞춤법 요청 실패:\n\n{exc}")
            self.update_correction_overlay()
            return
        result = spelling_payload["formatted"]
        spelling_feedback = spelling_payload.get("spelling_feedback") or self.analyzer.TEMP_SPELLING_FEEDBACK
        self.last_corrected_text = spelling_payload["corrected"]
        self._complete_word_overlay_spelling_snapshot(
            self.last_correction_source_text,
            self.last_corrected_text,
        )
        self.panel.set_spell_result(result)
        self.update_correction_overlay()
        self.save_history_log(
            feature_type=2,
            input_text=self.last_correction_source_text,
            request_id=self.current_history_request_id,
            output_text=self.last_corrected_text,
            spelling_feedback=spelling_feedback,
        )

    def apply_correction_to_source(self):
        if not self._can_apply_source_correction():
            return False
        if self.spell_check_in_progress or not str(self.last_corrected_text or "").strip():
            self.pending_apply_correction = True
            self.panel.set_spell_result("교정된 텍스트를 기다리는 중입니다...\n\n결과가 도착하면 자동으로 원본 수정이 진행됩니다.")
            return None
        self.pending_apply_correction = False
        return self._apply_correction_text(self.last_corrected_text)

    def _apply_correction_text(self, text):
        if not text:
            self.panel.set_spell_result("수정할 맞춤법 검사 결과가 없습니다.")
            return False

        output_applier = self.get_output_applier()
        can_replace, reason = output_applier.inspect_replace_availability(self.last_output_target)
        if not can_replace:
            self.panel.set_spell_result(
                self.panel.spell_box.toPlainText().rstrip()
                + "\n\n[원본 수정 실패]\n"
                + (reason or "원본 창을 찾을 수 없습니다.")
            )
            return False

        try:
            self._pause_realtime_replace_polling()
            previous_spell_text = self.panel.spell_box.toPlainText().rstrip()
            if self.last_output_target and self.word_overlay_original_target is None:
                self.word_overlay_original_text = str(
                    self.last_correction_source_text
                    or self.panel.text_box.toPlainText()
                    or self.last_input
                    or ""
                )
                self.word_overlay_original_target = copy.deepcopy(self.last_output_target)
            text = self._prepare_replacement_text(text)
            guard_message = self._validate_selection_replacement_guard(text)
            if guard_message:
                self.panel.set_spell_result(
                    previous_spell_text
                    + "\n\n[원본 수정 중단]\n"
                    + guard_message
                )
                return False
            style_map_info = self._maybe_apply_openai_style_mapping(text)
            mapped_text = str(style_map_info.get("mapped_corrected_text") or "").strip()
            if mapped_text:
                text = mapped_text
            output_applier.apply(self.last_output_target, text)
            self.last_corrected_text = text
            self.suppress_replacement_echo_text = text
            self.suppress_replacement_echo_until = time.monotonic() + 4.0
            status_line = ""
            status = style_map_info.get("status")
            if status == "unavailable":
                status_line = "\n[서식 매핑 API 미사용: 서버에 /style-map 라우트 없음]"
            elif status == "failed":
                status_line = "\n[서식 매핑 API 실패: 로컬 매핑으로 대체]"
            elif status == "ok":
                status_line = (
                    "\n[서식 매핑 적용]"
                    f" source_chars={style_map_info.get('source_chars', 0)}"
                    f" corrected_chars={style_map_info.get('corrected_chars', 0)}"
                    f" input_runs={style_map_info.get('input_runs', 0)}"
                    f" mapped_runs={style_map_info.get('mapped_runs', 0)}"
                )
            elif status == "skipped":
                reason = style_map_info.get("reason") or ""
                if reason:
                    status_line = f"\n[서식 매핑 건너뜀: {reason}]"
            self.panel.set_spell_result(
                previous_spell_text
                + "\n\n[원본 수정 완료]\n인식 중이던 원본 창에 교정문을 반영했습니다."
                + status_line
            )
            self.correction_overlay_suppressed = True
            self.update_correction_overlay(force_hide=True)
            self._commit_replacement_snapshot(text, self.last_output_target)
            return True
        except Exception as exc:
            self.panel.set_spell_result(
                self.panel.spell_box.toPlainText().rstrip()
                + f"\n\n[원본 수정 실패]\n{exc}"
            )
            self.update_correction_overlay()
            return False

    def _validate_selection_replacement_guard(self, corrected_text: str) -> str:
        target = self.last_output_target
        if not target or target.mode != "hwp":
            return ""
        style_info = target.style_info or {}
        if not style_info.get("selection_mode"):
            return ""
        source_text = str(style_info.get("selection_text") or self.last_correction_source_text or self.last_input or "")
        source_lines = source_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        target_lines = str(corrected_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if len(source_lines) >= 2 and len(target_lines) < len(source_lines):
            return (
                "교정 결과 줄 수가 원문보다 적어 원본 치환을 중단했습니다. "
                f"(원문 {len(source_lines)}줄, 교정 {len(target_lines)}줄)"
            )
        return ""

    def _maybe_apply_openai_style_mapping(self, corrected_text: str):
        target = self.last_output_target
        if target is None:
            return {"status": "skipped", "reason": "target_missing"}
        if target.mode not in {"word", "hwp"}:
            return {"status": "skipped", "reason": "unsupported_mode"}
        style_info = target.style_info or {}
        if target.mode == "hwp":
            if style_info.get("selection_mode"):
                prepared_style_info = self.get_output_applier().prepare_hwp_selection_style_runs(
                    target,
                    str(style_info.get("selection_text") or self.last_correction_source_text or self.last_input or ""),
                )
            else:
                prepared_style_info = self.get_output_applier().prepare_hwp_full_document_style_runs(
                    target,
                    str(style_info.get("_source_text") or self.last_correction_source_text or self.last_input or ""),
                )
            if prepared_style_info:
                target.style_info = dict(prepared_style_info)
                style_info = target.style_info
        source_text = str(
            style_info.get("selection_text")
            or style_info.get("_source_text")
            or self.last_correction_source_text
            or self.last_input
            or ""
        )
        if not source_text.strip() or not str(corrected_text or "").strip():
            return {"status": "skipped", "reason": "source_or_corrected_empty"}
        style_runs = style_info.get("segments") or []
        if not style_runs:
            return {"status": "skipped", "reason": "style_runs_empty"}
        style_runs = self._enrich_style_runs_with_source_text(source_text, style_runs)
        info = {
            "status": "failed",
            "source_chars": len(source_text),
            "corrected_chars": len(str(corrected_text or "")),
            "input_runs": len(style_runs),
            "mapped_runs": 0,
        }
        if target.mode == "hwp":
            try:
                mapped_text_runs = self.analyzer.map_style_runs_by_slot(source_text, corrected_text, style_runs)
            except Exception as exc:
                if "404" in str(exc) or "Not Found" in str(exc):
                    info["status"] = "unavailable"
                else:
                    info["status"] = "failed"
                    info["reason"] = "api_slot_map_failed"
                return info
            mapped_runs = self._merge_slot_mapped_text_with_local_styles(
                str(corrected_text or ""),
                style_runs,
                mapped_text_runs,
            )
            if not mapped_runs:
                info["status"] = "failed"
                info["reason"] = "api_slot_map_invalid"
                return info
            target.style_info = dict(style_info)
            target.style_info["segments"] = mapped_runs
            target.style_info["segments_mapped"] = True
            target.style_info["mapping_mode"] = "hwp_api_slot_map"
            info["mapped_corrected_text"] = self._rebuild_text_from_mapped_runs(
                str(corrected_text or ""),
                mapped_runs,
            )
            info["status"] = "ok"
            info["mapped_runs"] = len(mapped_runs)
            try:
                preview = [str(run.get("text") or "") for run in mapped_runs[:10]]
                self.append_result(
                    "[서식 매핑 디버그] "
                    f"mode=hwp_api_slot_map mapped_runs={len(mapped_runs)} preview={preview}"
                )
            except Exception:
                pass
            return info
        try:
            mapped_text_runs = self.analyzer.map_style_runs(source_text, corrected_text, style_runs)
        except Exception as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                info["status"] = "unavailable"
                return info
            info["status"] = "failed"
            return info
        mapped_runs = self._merge_mapped_text_with_local_styles(
            str(corrected_text or ""),
            style_runs,
            mapped_text_runs,
            source_text,
        )
        if not isinstance(mapped_runs, list) or not mapped_runs:
            info["status"] = "failed"
            return info
        try:
            preview = [str(run.get("text") or "") for run in mapped_runs[:10]]
            self.append_result(
                "[서식 매핑 디버그] "
                f"mapped_runs={len(mapped_runs)} preview={preview}"
            )
        except Exception:
            pass
        target.style_info = dict(style_info)
        target.style_info["segments"] = mapped_runs
        target.style_info["segments_mapped"] = True
        info["mapped_corrected_text"] = self._rebuild_text_from_mapped_runs(str(corrected_text or ""), mapped_runs)
        info["status"] = "ok"
        info["mapped_runs"] = len(mapped_runs)
        return info

    def _enrich_style_runs_with_source_text(self, source_text: str, runs: list[dict]) -> list[dict]:
        text = str(source_text or "")
        text_len = len(text)
        enriched = []
        for run in runs or []:
            try:
                start = max(0, min(text_len, int(run.get("start", 0))))
                end = max(0, min(text_len, int(run.get("end", 0))))
            except Exception:
                continue
            if end <= start:
                continue
            style = run.get("style") or {}
            if not isinstance(style, dict):
                continue
            item = {"start": start, "end": end, "style": style, "text": text[start:end]}
            enriched.append(item)
        return enriched

    def _rebuild_text_from_mapped_runs(self, corrected_text: str, mapped_runs: list[dict]) -> str:
        base = str(corrected_text or "")
        if not base or not mapped_runs:
            return base
        ordered_chunks = []
        has_any_text = False
        for run in mapped_runs:
            run_text = run.get("text")
            if isinstance(run_text, str):
                ordered_chunks.append(run_text)
                has_any_text = True
                continue
            try:
                start = max(0, min(len(base), int(run.get("start", 0))))
                end = max(0, min(len(base), int(run.get("end", 0))))
            except Exception:
                continue
            if end <= start:
                continue
            ordered_chunks.append(base[start:end])
        if has_any_text and ordered_chunks:
            return "".join(ordered_chunks)
        return base

    def _merge_mapped_text_with_local_styles(
        self,
        corrected_text: str,
        local_style_runs: list[dict],
        mapped_text_runs: list[dict],
        source_text: str = "",
    ) -> list[dict]:
        if not isinstance(mapped_text_runs, list) or not mapped_text_runs:
            return []
        merged = []
        cursor = 0
        text_len = len(str(corrected_text or ""))
        source = str(source_text or "")
        for index, mapped_run in enumerate(mapped_text_runs):
            run_text = mapped_run.get("text")
            if not isinstance(run_text, str):
                continue
            local_run = local_style_runs[index] if index < len(local_style_runs) else {}
            style = local_run.get("style") or {}
            if not isinstance(style, dict):
                style = {}
            start = cursor
            end = min(text_len, cursor + len(run_text))
            merged.append(
                {
                    "start": start,
                    "end": end,
                    "style": style,
                    "text": run_text,
                }
            )
            cursor = end
            if index + 1 >= len(local_style_runs):
                continue
            current_end = int(local_run.get("end", 0) or 0)
            next_start = int(local_style_runs[index + 1].get("start", current_end) or current_end)
            if next_start <= current_end:
                continue
            separator = source[current_end:next_start]
            if not isinstance(separator, str) or not separator:
                continue
            if "\n" not in separator and "\r" not in separator:
                continue
            normalized_separator = separator.replace("\r\n", "\n").replace("\r", "\n")
            sep_start = cursor
            sep_end = min(text_len, cursor + len(normalized_separator))
            merged.append(
                {
                    "start": sep_start,
                    "end": sep_end,
                    "style": {},
                    "text": normalized_separator,
                }
            )
            cursor = sep_end
        return merged

    def _merge_slot_mapped_text_with_local_styles(
        self,
        corrected_text: str,
        local_style_runs: list[dict],
        mapped_text_runs: list[dict],
    ) -> list[dict]:
        if not isinstance(mapped_text_runs, list) or not mapped_text_runs:
            return []
        if len(mapped_text_runs) != len(local_style_runs):
            return []

        merged = []
        cursor = 0
        for index, mapped_run in enumerate(mapped_text_runs):
            run_text = mapped_run.get("text")
            if not isinstance(run_text, str):
                return []
            local_run = local_style_runs[index] if index < len(local_style_runs) else {}
            style = local_run.get("style") or {}
            if not isinstance(style, dict):
                style = {}
            start = cursor
            end = cursor + len(run_text)
            merged.append(
                {
                    "start": start,
                    "end": end,
                    "style": style,
                    "text": run_text,
                }
            )
            cursor = end

        rebuilt = "".join(str(run.get("text") or "") for run in merged)
        base = str(corrected_text or "")
        if rebuilt != base:
            return []
        return merged

    def update_correction_overlay(self, force_hide=False):
        if self.correction_overlay is None:
            return
        if force_hide:
            self.correction_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.correction_overlay.hide()
            return
        if self.correction_overlay.is_recently_interacting():
            self.correction_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            if not self.correction_overlay.isVisible():
                self.correction_overlay.show()
            self.correction_overlay.raise_near_target()
            return
        if self._has_blocking_popup():
            self.correction_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.correction_overlay.hide()
            return
        if self.correction_overlay.is_suspended():
            self.correction_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            return
        self.correction_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        if self.correction_overlay._dragging:
            if not self.correction_overlay.isVisible():
                self.correction_overlay.show()
            return
        if (
            self.active_input_mode not in {"realtime", "selection"}
            or not self._can_apply_source_correction()
            or not self.last_output_target
            or not self.last_corrected_text
            or self.correction_overlay_suppressed
        ):
            self.correction_overlay.hide()
            return

        can_replace, reason = self.get_output_applier().inspect_replace_availability(self.last_output_target)
        if not can_replace:
            self.correction_overlay.hide()
            return

        self.correction_overlay.follow_target(self.last_output_target)
        if self.correction_overlay.has_active_modal_popup():
            self.correction_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.correction_overlay.suspend(seconds=2.5, hide=True)
            return
        if not self.correction_overlay.is_target_foreground():
            self.correction_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.correction_overlay.suspend(seconds=0.35, hide=True)
            return

        self.correction_overlay.setEnabled(True)
        self.correction_overlay.setToolTip(reason or "감지한 원본 문서에 교정문을 반영합니다.")
        raise_key = self.correction_overlay.raise_key()
        should_raise = raise_key != self.correction_overlay._last_raise_key
        if not self.correction_overlay.isVisible():
            self.correction_overlay.show()
            should_raise = True
        if should_raise or self.correction_overlay.isVisible():
            self.correction_overlay.raise_near_target()
            self.correction_overlay._last_raise_key = raise_key

    def _has_blocking_popup(self) -> bool:
        if self.correction_overlay is None:
            return False
        if win32gui is None:
            return False
        try:
            foreground = win32gui.GetForegroundWindow()
            if not foreground:
                return False
            overlay_hwnd = int(self.correction_overlay.winId())
            panel_hwnd = int(self.panel.winId())
            target_hwnd = getattr(self.last_output_target, "window_handle", None)
            if foreground in {overlay_hwnd, panel_hwnd, target_hwnd}:
                return False
            class_name = (win32gui.GetClassName(foreground) or "").strip()
            if class_name == "#32770":
                return True
            owner = win32gui.GetWindow(foreground, 4)  # GW_OWNER
            if target_hwnd and owner == target_hwnd:
                return True
            return False
        except Exception:
            return False

    def _pause_realtime_replace_polling(self):
        target = self.last_output_target
        if not target or target.mode != "hwp":
            return
        try:
            from client.input.realtime_text_monitor import pause_polling_for

            pause_polling_for(6.0)
        except Exception:
            pass

    def _can_apply_source_correction(self):
        input_mode = self.panel.get_input_mode()
        if input_mode == "selection":
            return True
        return input_mode == "realtime" and self.panel.get_replace_mode_checked()

    def handle_live_input_mode_change(self):
        new_mode = self.panel.get_input_mode()
        previous_mode = self.active_input_mode
        replace_mode = (
            self.panel.get_replace_mode_checked()
            and new_mode == "realtime"
        )
        self.settings["input_mode"] = new_mode
        self.settings["replace_mode"] = replace_mode
        if new_mode != previous_mode:
            self.active_input_mode = new_mode
            self.ensure_realtime_monitor_started()
            self.reset_session_state()
        else:
            self.active_input_mode = new_mode
        can_apply = self._can_apply_source_correction()
        self._sync_apply_correction_button_visibility()
        if not can_apply:
            self.update_correction_overlay(force_hide=True)

    def _sync_apply_correction_button_visibility(self):
        self.panel.apply_correction_btn.setVisible(self._can_apply_source_correction())

    def _build_output_target(self, event):
        reader_name = str(event.get("reader", "")).strip()
        if reader_name not in {"browser", "browser_extension", "notepad", "word", "hwp"}:
            return None
        from client.input.output_applier import OutputTarget

        mode = "browser_extension" if reader_name == "browser_extension" else reader_name
        style_info = dict(event.get("style_info") or {})
        if event.get("source") == "realtime":
            for key in (
                "selection_mode",
                "selection_text",
                "hwp_selection_start_pos",
                "hwp_selection_end_pos",
                "hwp_selection_length",
                "segments_mapped",
                "mapping_mode",
            ):
                style_info.pop(key, None)
        return OutputTarget(
            mode=mode,
            window_handle=event.get("window_handle"),
            window_title=event.get("window_title", ""),
            style_info=style_info,
        )

    def get_output_applier(self):
        if self.output_applier is None:
            from client.input.output_applier import OutputApplier

            self.output_applier = OutputApplier()
        return self.output_applier

    def _extract_corrected_text(self, result):
        text = str(result or "").strip()
        if not text:
            return ""
        lines = [line.rstrip() for line in text.splitlines()]
        heading_indices = [
            index for index, line in enumerate(lines)
            if line.strip().endswith(":")
        ]
        if heading_indices:
            return "\n".join(lines[heading_indices[-1] + 1:]).strip()
        return text

    def _prepare_replacement_text(self, text):
        target = self.last_output_target
        if target and target.mode in {"notepad", "browser", "browser_extension", "word", "hwp"}:
            source_text = self.last_correction_source_text or self.last_input
            restored = preserve_replacement_structure(source_text, text)
            self._log_replacement_structure(source_text, text, restored, target.mode)
            return restored
        return text

    def _log_replacement_structure(self, source_text, replacement_text, restored_text, mode):
        try:
            with _REPLACEMENT_STRUCTURE_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} mode={mode!r} "
                    f"source_len={len(str(source_text or ''))} source_newlines={str(source_text or '').count(chr(10))} "
                    f"replacement_len={len(str(replacement_text or ''))} replacement_newlines={str(replacement_text or '').count(chr(10))} "
                    f"restored_len={len(str(restored_text or ''))} restored_newlines={str(restored_text or '').count(chr(10))} "
                    f"source_sample={str(source_text or '')[:80]!r} replacement_sample={str(replacement_text or '')[:80]!r} "
                    f"restored_sample={str(restored_text or '')[:80]!r}\n"
                )
        except Exception:
            pass

    def _is_replacement_echo(self, text):
        if not self.suppress_replacement_echo_text:
            return False
        if time.monotonic() > self.suppress_replacement_echo_until:
            self.suppress_replacement_echo_text = ""
            self.suppress_replacement_echo_until = 0.0
            return False
        return text.strip() == self.suppress_replacement_echo_text.strip()

    def _should_ignore_blank_line_downgrade(self, reader_name, text):
        if reader_name == "browser_extension":
            return False
        if not self.last_input or "\n\n" not in self.last_input:
            return False
        if time.monotonic() - self.last_browser_extension_event_at > 10.0:
            return False
        if self.last_output_target is None or self.last_output_target.mode != "browser_extension":
            return False
        if self._content_line_count(text) != self._content_line_count(self.last_input):
            return False
        return self._blank_line_count(text) < self._blank_line_count(self.last_input)

    def _content_line_count(self, text):
        return sum(1 for line in self._split_lines(text) if line.strip())

    def _blank_line_count(self, text):
        return sum(1 for line in self._split_lines(text) if not line.strip())

    def _split_lines(self, text):
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def _log_input_event(self, event, text, reader_name, note=""):
        try:
            with _UI_INPUT_EVENT_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"reader={reader_name!r} source={event.get('source')!r} "
                    f"title={str(event.get('window_title') or '')[:80]!r} "
                    f"text_len={len(str(text or ''))} newlines={str(text or '').count(chr(10))} "
                    f"blank_lines={self._blank_line_count(text)} note={note!r} "
                    f"sample={str(text or '')[:120]!r}\n"
                )
        except Exception:
            pass

    def run_summary(self):
        summary_input = self._summary_input_text()
        if not summary_input:
            return
        try:
            result = self.analyzer.analyze_summary(
                summary_input,
                self.panel.get_summary_style(),
            )
        except Exception as exc:
            self.panel.set_summary_result(f"OpenAI 요약 요청 실패:\n\n{exc}")
            return ""
        self.panel.set_summary_result(result)
        self.save_history_log(
            feature_type=3,
            input_text=self._history_source_text(),
            request_id=self.current_history_request_id,
            output_text=self._strip_result_heading(result),
        )
        return result

    def _summary_input_text(self):
        corrected_text = str(self.last_corrected_text or "").strip()
        if corrected_text:
            return corrected_text
        return str(self.last_input or "").strip()

    def _history_source_text(self):
        source_text = str(
            self.current_history_source_text
            or self.last_correction_source_text
            or self.last_input
            or ""
        ).strip()
        return source_text

    def run_evaluation(self):
        evaluation_input = self._summary_input_text()
        if not evaluation_input:
            return
        try:
            result = self.analyzer.analyze_evaluation(evaluation_input)
        except Exception as exc:
            self.panel.set_evaluation_score(f"평가 실패: {exc}")
            return ""
        self.panel.set_evaluation_score(result)
        self.save_history_log(
            feature_type=1,
            input_text=self._history_source_text(),
            request_id=self.current_history_request_id,
            output_text=result,
            score=self._parse_score(result),
        )
        return result

    def handle_word_overlay_evaluation_reason(self):
        reader_name = str(getattr(self.word_main_overlay, "_last_reader_name", "") or "word")
        if not self._refresh_overlay_input_for_reader(reader_name):
            self.word_main_overlay.show_status("텍스트 없음", auto_hide_ms=1200)
            return
        evaluation_input = self._summary_input_text()
        if not evaluation_input:
            self.word_main_overlay.show_status("텍스트 없음", auto_hide_ms=1200)
            return
        self.word_main_overlay.show_busy("평가 이유 생성중")
        self.add_word_overlay_notification("평가 이유 생성 시작")
        QApplication.processEvents()
        try:
            score_text = self.panel.score_label.text()
            reason = self.analyzer.analyze_evaluation_reason(evaluation_input, score_text)
            self.word_main_overlay.show_text_result("평가 이유", reason)
            self.save_history_log(
                feature_type=1,
                input_text=self._history_source_text(),
                request_id=self.current_history_request_id,
                output_text=score_text,
                score=self._parse_score(score_text),
                evaluation_reason=reason,
            )
            self.add_word_overlay_notification("평가 이유 생성 완료")
        except Exception as exc:
            self.word_main_overlay.show_status(f"평가 이유 실패: {exc}", auto_hide_ms=1800)
            self.add_word_overlay_notification(
                f"평가 이유 실패: {type(exc).__name__}",
                error=True,
            )
        finally:
            self.word_main_overlay.hide_busy()

    def run_title_recommendation(self):
        title_input = self._summary_input_text()
        if not title_input:
            return
        try:
            result = self.analyzer.analyze_title_recommendation(title_input)
        except Exception as exc:
            self.panel.set_title_recommendation(f"제목 추천 실패: {exc}")
            return ""
        self.panel.set_title_recommendation(result)
        self.save_history_log(
            feature_type=1,
            input_text=self._history_source_text(),
            request_id=self.current_history_request_id,
            title=result,
            score=self._parse_score(self.panel.score_label.text()),
        )
        return result

    def run_tone_change(self):
        tone_input = self._summary_input_text()
        if not tone_input:
            return
        tone = self.panel.tone_input.text().strip()
        if not tone:
            self.panel.set_tone_result("변경할 문체/말투를 입력해 주세요.")
            return
        try:
            result = self.analyzer.analyze_tone_change(tone_input, tone)
        except Exception as exc:
            self.panel.set_tone_result(f"OpenAI 문체/말투 요청 실패:\n\n{exc}")
            return
        self.panel.set_tone_result(result)
        self.save_history_log(
            feature_type=4,
            input_text=self._history_source_text(),
            request_id=self.current_history_request_id,
            output_text=result,
            tone=tone,
        )

    def save_history_log(
        self,
        feature_type,
        input_text,
        request_id=None,
        output_text="",
        title=None,
        score=None,
        tone=None,
        spelling_feedback=None,
        evaluation_reason=None,
    ):
        if not input_text:
            return
        payload = {
            "feature_type": feature_type,
            "input_text": input_text,
            "request_id": request_id,
            "output_text": output_text or "",
            "title": title,
            "score": score,
            "tone": tone,
            "spelling_feedback": spelling_feedback,
            "evaluation_reason": evaluation_reason,
        }
        key = (
            payload["feature_type"],
            payload["input_text"],
            payload["output_text"],
            payload.get("title") or "",
            payload.get("score"),
            payload.get("tone") or "",
            payload.get("spelling_feedback") or "",
            payload.get("evaluation_reason") or "",
        )
        if not self.is_logged_in() or not self.is_history_enabled():
            return
        self.write_local_history_log(payload)
        if key in self.last_logged_keys:
            return
        if not self.ensure_server_available():
            return
        remote_payload = dict(payload)
        if remote_payload.get("request_id") is not None:
            remote_payload["input_text"] = ""
        try:
            response = self.api_client.create_log(remote_payload)
            saved_request_id = response.get("request_id")
            if saved_request_id:
                self.current_history_request_id = saved_request_id
                self.current_history_source_text = input_text
            self.last_logged_keys.add(key)
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception:
            pass

    def write_local_history_log(self, payload):
        key = (
            payload["feature_type"],
            payload["input_text"],
            payload["output_text"],
            payload.get("title") or "",
            payload.get("score"),
            payload.get("tone") or "",
            payload.get("spelling_feedback") or "",
            payload.get("evaluation_reason") or "",
        )
        if key in self.last_local_log_keys:
            return

        log_dir = Path(__file__).resolve().parents[2] / ".logs" / "history"
        feature_names = {
            1: "text",
            2: "spelling",
            3: "summary",
            4: "tone",
        }
        feature_name = feature_names.get(payload["feature_type"], "unknown")
        log_data = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "feature_name": feature_name,
            "db_sync_enabled": self.is_logged_in() and self.is_history_enabled(),
            **payload,
        }

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{feature_name}_logs.jsonl"
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(log_data, ensure_ascii=False) + "\n")
            self.last_local_log_keys.add(key)
        except Exception:
            pass

    def show_history(self, feature_type):
        if not self.ensure_history_available():
            return
        if not self.ensure_server_available():
            return
        try:
            requests_data = self.api_client.list_history_requests()
            self.panel.show_history_list(0, requests_data)
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("기록 조회 실패", str(exc))

    def handle_tab_changed(self, index):
        history_tab_index = getattr(self.panel, "history_tab_index", -1)
        if index == history_tab_index:
            self.show_history(0)

    def handle_history_request_action(self, action_key, log, tone_text=None):
        if not isinstance(log, dict) or log.get("request_id") is None:
            return
        if not self.ensure_history_available():
            return
        if not self.ensure_server_available():
            return

        request_id = log.get("request_id")
        source_text = str(log.get("input_text") or "").strip()
        if not source_text:
            self.panel.show_notice("기록 실행 실패", "원문이 비어 있습니다.")
            return

        spelling = log.get("spelling") or {}
        analysis_text = str(spelling.get("corrected_text") or source_text).strip() or source_text

        try:
            if action_key == "spelling":
                payload = self.analyzer.get_spelling_result_payload(source_text)
                self.save_history_log(
                    feature_type=2,
                    input_text=source_text,
                    request_id=request_id,
                    output_text=payload.get("corrected", ""),
                    spelling_feedback=payload.get("spelling_feedback") or self.analyzer.TEMP_SPELLING_FEEDBACK,
                )
            elif action_key == "summary":
                result = self.analyzer.analyze_summary(analysis_text)
                self.save_history_log(
                    feature_type=3,
                    input_text=source_text,
                    request_id=request_id,
                    output_text=self._strip_result_heading(result),
                )
            elif action_key == "tone":
                tone = str(tone_text).strip() if tone_text is not None else self.panel.tone_input.text().strip()
                if not tone:
                    self.panel.show_notice("문체/말투 입력 필요", "문체/말투를 입력해 주세요.")
                    return
                result = self.analyzer.analyze_tone_change(analysis_text, tone)
                self.save_history_log(
                    feature_type=4,
                    input_text=source_text,
                    request_id=request_id,
                    output_text=result,
                    tone=tone,
                )
            elif action_key == "evaluation":
                result = self.analyzer.analyze_evaluation(analysis_text)
                self.save_history_log(
                    feature_type=1,
                    input_text=source_text,
                    request_id=request_id,
                    score=self._parse_score(result),
                )
            elif action_key == "title":
                result = self.analyzer.analyze_title_recommendation(analysis_text)
                self.save_history_log(
                    feature_type=1,
                    input_text=source_text,
                    request_id=request_id,
                    title=result,
                )
            else:
                return
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
            return
        except Exception as exc:
            self.panel.show_notice("기록 실행 실패", str(exc))
            return

        self.refresh_history_request_detail(request_id)

    def refresh_history_request_detail(self, request_id):
        try:
            requests_data = self.api_client.list_history_requests()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
            return
        except Exception as exc:
            self.panel.show_notice("기록 조회 실패", str(exc))
            return

        self.panel._last_history_logs = list(requests_data or [])
        for item in self.panel._last_history_logs:
            if item.get("request_id") == request_id:
                self.panel.show_history_detail(0, item)
                return
        self.panel.show_history_list(0, self.panel._last_history_logs)

    def confirm_history_delete(self, log):
        if not isinstance(log, dict) or log.get("request_id") is None:
            return
        request_id = int(log.get("request_id"))
        self.panel.show_prompt(
            "기록 삭제",
            "이 기록을 삭제하시겠습니까?\n연결된 맞춤법, 요약, 문체/말투, 평가, 제목 결과도 함께 삭제됩니다.",
            yes_callback=lambda: self.delete_history_request(request_id),
        )

    def delete_history_request(self, request_id):
        try:
            self.api_client.delete_history_request(request_id)
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
            return
        except Exception as exc:
            self.panel.show_notice("기록 삭제 실패", str(exc))
            return
        self.show_history(0)

    def confirm_delete_all_history_requests(self):
        self.panel.show_prompt(
            "전체 기록 삭제",
            "기록 목록의 모든 항목을 삭제하시겠습니까?\n연결된 맞춤법, 요약, 문체/말투, 평가, 제목 결과도 함께 삭제됩니다.",
            yes_callback=self.delete_all_history_requests,
        )

    def delete_all_history_requests(self):
        try:
            self.api_client.delete_all_history_requests()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
            return
        except Exception as exc:
            self.panel.show_notice("전체 기록 삭제 실패", str(exc))
            return
        self.show_history(0)

    def _current_title(self):
        title = self.panel.title_label_box.text().strip()
        return "" if title in {"제목", ""} else title

    def _strip_result_heading(self, text):
        value = str(text or "").strip()
        if "\n\n" in value:
            return value.split("\n\n", 1)[1].strip()
        return value

    def _parse_score(self, text):
        digits = "".join(ch for ch in str(text or "") if ch.isdigit())
        if not digits:
            return None
        return max(0, min(100, int(digits[:3])))

    def normalize_settings(self, settings):
        normalized = DEFAULT_SETTINGS.copy()
        if isinstance(settings, dict):
            normalized.update({key: settings[key] for key in DEFAULT_SETTINGS if key in settings})
        normalized["default_dark_mode"] = bool(normalized.get("default_dark_mode", False))
        normalized["history_enabled"] = bool(normalized.get("history_enabled", False))
        normalized["input_mode"] = (
            normalized.get("input_mode")
            if normalized.get("input_mode") in {"clipboard", "realtime", "selection"}
            else "realtime"
        )
        normalized["replace_mode"] = (
            bool(normalized.get("replace_mode", False))
            and normalized["input_mode"] == "realtime"
        )
        return normalized

    def collect_settings_from_panel(self):
        settings = self.settings.copy()
        settings["default_dark_mode"] = self.panel.get_default_dark_mode_checked()
        settings["input_mode"] = self.panel.get_input_mode()
        settings["replace_mode"] = (
            self.panel.get_replace_mode_checked()
            and settings["input_mode"] == "realtime"
        )
        if self.is_logged_in():
            settings["history_enabled"] = self.panel.get_history_enabled_checked()
        return self.normalize_settings(settings)

    def apply_settings_state(self, settings, persist=True):
        previous_mode = self.active_input_mode
        self.settings = self.normalize_settings(settings)
        if persist:
            save_app_settings(self.settings)

        self.panel.set_dark_mode(self.settings["default_dark_mode"], animate=False)
        self.panel.set_default_dark_mode_checked(self.settings["default_dark_mode"])
        self.panel.set_history_enabled_checked(self.settings["history_enabled"])
        self.panel.set_input_mode(self.settings["input_mode"])
        self.panel.set_replace_mode_checked(self.settings["replace_mode"])
        self._sync_apply_correction_button_visibility()
        self.update_login_state()

        mode_changed = previous_mode != self.settings["input_mode"]
        self.active_input_mode = self.settings["input_mode"]
        self.ensure_realtime_monitor_started()
        if self.active_input_mode not in {"realtime", "selection"}:
            self.panel.set_active_window_title("")
        if mode_changed:
            self.reset_session_state()
        self.update_correction_overlay()

    def ensure_server_available(self):
        if self._server_started:
            return True
        try:
            self.local_server.ensure_running()
            self._server_started = True
            self._startup_server_error = ""
            return True
        except Exception as exc:
            self._startup_server_error = str(exc)
            self.panel.show_notice("서버 시작 실패", self._startup_server_error)
            return False

    def save_remote_settings(self):
        if not self.is_logged_in():
            return False
        if not self.ensure_server_available():
            return False
        self.api_client.update_settings(self.settings)
        return True

    def load_remote_settings(self):
        if not self.is_logged_in():
            return None
        if not self.ensure_server_available():
            return None
        remote = self.api_client.get_settings()
        if not remote or not remote.get("has_settings"):
            return None
        return self.normalize_settings(remote)

    def sync_restored_login_settings(self):
        if not self.is_logged_in():
            return
        try:
            remote_settings = self.load_remote_settings()
            if remote_settings:
                self.apply_settings_state(remote_settings)
            else:
                self.save_remote_settings()
        except Exception as exc:
            self.panel.show_notice("설정 동기화 실패", str(exc))

    def start_restored_login_sync(self):
        if not self.is_logged_in():
            return
        threading.Thread(target=self.run_restored_login_sync, daemon=True).start()

    def run_restored_login_sync(self):
        try:
            remote_settings = self.load_remote_settings()
            if remote_settings:
                self.signals.auth_sync_signal.emit({"settings": remote_settings})
            else:
                self.save_remote_settings()
                self.signals.auth_sync_signal.emit({"settings": None})
        except Exception as exc:
            self.signals.auth_sync_signal.emit({"error": str(exc)})

    def handle_background_auth_sync_result(self, result):
        if not isinstance(result, dict):
            return
        if result.get("error"):
            self.panel.show_notice("설정 동기화 실패", result["error"])
            return
        remote_settings = result.get("settings")
        if remote_settings:
            self.apply_settings_state(remote_settings)
        self.refresh_account_identity()

    def save_settings(self):
        self.apply_settings_state(self.collect_settings_from_panel())
        if self.is_logged_in():
            try:
                self.save_remote_settings()
            except UnauthorizedError as exc:
                self.handle_session_expired(str(exc))
                return
            except Exception as exc:
                self.panel.show_notice("설정 저장 실패", str(exc))
                return
        self.panel.show_settings_saved_notice()

    def quit_app(self):
        self.reset_session_state()
        if hasattr(self, "word_overlay_timer"):
            self.word_overlay_timer.stop()
        if hasattr(self, "word_main_overlay"):
            self.word_main_overlay.hide_with_reason("quit_app")
        self.tray.hide()
        self.local_server.stop()
        self.qt_app.quit()

    def logout(self):
        self.api_client.clear_token()
        save_app_settings(self.settings)
        self.panel.set_account_identity("", "")
        self.update_login_state()

    def handle_login_button(self):
        if self.is_logged_in():
            self.logout()
            return
        self.panel.show_auth_page()

    def handle_login_submit(self):
        username = self.panel.login_username_input.text().strip()
        password = self.panel.login_password_input.text().strip()
        remember_me = self.panel.login_remember_checkbox.isChecked()
        if not username or not password:
            self.panel.show_notice("입력 오류", "아이디와 비밀번호를 모두 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            local_settings = self.collect_settings_from_panel()
            self.api_client.login(username, password, remember_me)
            self.update_login_state()
            self.panel.close_auth_page()
            self.handle_login_settings_sync(username, local_settings)
            self.refresh_account_identity()
        except Exception as exc:
            self.panel.show_notice("로그인 실패", str(exc))

    def handle_signup_submit(self):
        username = self.panel.signup_username_input.text().strip()
        password = self.panel.signup_password_input.text().strip()
        password_confirm = self.panel.signup_password_confirm_input.text().strip()
        if not username or not password or not password_confirm:
            self.panel.show_notice("입력 오류", "모든 항목을 입력해 주세요.")
            return
        if password != password_confirm:
            self.panel.show_notice("입력 오류", "비밀번호가 일치하지 않습니다.")
            return
        if len(password) < 4:
            self.panel.show_notice("입력 오류", "비밀번호는 4자 이상으로 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.signup(username, password)
            self.pending_signup_username = username
            self.panel.login_username_input.setText(username)
            self.panel.login_password_input.clear()
            self.panel.show_login_form()
            self.panel.show_prompt(
                "회원가입 완료",
                "회원가입이 완료되었습니다. 로그인해 주세요.",
                yes_callback=self.panel.show_auth_page,
            )
            self.panel.prompt_no_btn.hide()
            self.panel.prompt_yes_btn.setText("확인")
        except Exception as exc:
            self.panel.show_notice("회원가입 실패", str(exc))

    def handle_login_settings_sync(self, username, local_settings):
        try:
            remote_settings = self.load_remote_settings()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
            return
        except Exception as exc:
            self.panel.show_notice("설정 동기화 실패", str(exc))
            return

        is_new_signup = username == self.pending_signup_username
        if is_new_signup or remote_settings is None:
            self.apply_settings_state(local_settings)
            try:
                self.save_remote_settings()
                self.pending_signup_username = ""
                self.refresh_account_identity()
            except Exception as exc:
                self.panel.show_notice("설정 저장 실패", str(exc))
            return

        def keep_local_settings():
            self.apply_settings_state(local_settings)
            try:
                self.save_remote_settings()
            except Exception as exc:
                self.panel.show_notice("설정 저장 실패", str(exc))

        def load_account_settings():
            self.apply_settings_state(remote_settings)

        self.panel.show_prompt(
            "설정 상태 유지",
            "비로그인 상태에서 사용하던 설정을 이 계정에도 유지하시겠습니까?\n"
            "유지하면 현재 설정이 계정 설정으로 저장되고, 비유지를 누르면 DB에 저장된 계정 설정을 불러옵니다.",
            yes_callback=keep_local_settings,
            no_callback=load_account_settings,
            yes_text="유지",
            no_text="비유지",
        )

    def refresh_account_identity(self):
        if not self.is_logged_in():
            return
        try:
            account = self.api_client.get_account()
            self.panel.set_account_identity(
                account.get("username", self.api_client.current_username or ""),
                account.get("display_name", ""),
            )
        except Exception:
            self.panel.set_account_identity(self.api_client.current_username or "", "")

    def handle_account_manage_button(self):
        if not self.is_logged_in():
            self.panel.show_prompt(
                "로그인이 필요합니다",
                "계정 관리는 로그인 후 사용할 수 있습니다.\n지금 로그인하시겠습니까?",
                yes_callback=self.panel.show_auth_page,
            )
            return
        self.panel.show_account_verify_page()

    def handle_account_verify_submit(self):
        password = self.panel.account_verify_password_input.text().strip()
        if not password:
            self.panel.show_notice("입력 오류", "비밀번호를 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            self.api_client.verify_account(password)
            account = self.api_client.get_account()
            self.panel.show_account_page(account)
            self.panel.set_account_identity(account.get("username", ""), account.get("display_name", ""))
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("인증 실패", str(exc))

    def handle_account_update(self, field=None):
        payload = self.panel.get_account_payload(field)
        if not payload:
            return
        if "username" in payload and not payload["username"]:
            self.panel.show_notice("입력 오류", "아이디를 입력해 주세요.")
            return
        if "password" in payload and len(payload["password"]) < 4:
            self.panel.show_notice("입력 오류", "비밀번호는 4자 이상으로 입력해 주세요.")
            return
        if not self.ensure_server_available():
            return
        try:
            account = self.api_client.update_account(payload)
            self.panel.set_account_info(account)
            self.panel.set_account_identity(account.get("username", ""), account.get("display_name", ""))
            self.update_login_state()
            self.panel.show_account_saved_notice()
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("계정 수정 실패", str(exc))

    def confirm_account_delete(self):
        self.panel.show_prompt(
            "계정 탈퇴",
            "계정을 탈퇴하면 저장된 계정 정보와 기록이 삭제됩니다.\n정말 탈퇴하시겠습니까?",
            yes_callback=self.delete_account,
            yes_text="탈퇴",
            no_text="취소",
        )

    def delete_account(self):
        if not self.ensure_server_available():
            return
        try:
            self.api_client.delete_account()
            self.logout()
            self.panel.close_account_pages()
            self.panel.show_notice("계정 탈퇴 완료", "계정이 삭제되었습니다.")
        except UnauthorizedError as exc:
            self.handle_session_expired(str(exc))
        except Exception as exc:
            self.panel.show_notice("계정 탈퇴 실패", str(exc))

    def handle_session_expired(self, message):
        self.panel.show_notice("로그인 만료", message or "다시 로그인해 주세요.")
        self.logout()

    def ensure_history_available(self):
        if not self.is_logged_in():
            self.panel.show_prompt(
                "로그인이 필요합니다",
                "기록 기능은 로그인 후 사용할 수 있습니다.\n지금 로그인하시겠습니까?",
                yes_callback=self.panel.show_auth_page,
            )
            return False
        if self.is_history_enabled():
            return True
        self.panel.show_prompt(
            "기록 기능 비활성화",
            "기록을 사용하려면 설정에서 '기록 사용'을 켜 주세요.",
            yes_callback=self.panel.open_settings_tab,
        )
        return False

    def is_logged_in(self):
        return bool(self.api_client.access_token and self.api_client.current_username)

    def is_history_enabled(self):
        return bool(self.settings.get("history_enabled", False))

    def update_login_state(self):
        logged_in = self.is_logged_in()
        username = self.api_client.current_username or ""
        if hasattr(self, "panel"):
            self.panel.update_login_state(logged_in, username, self.is_history_enabled())
        if hasattr(self, "login_action"):
            self.login_action.setText("로그아웃" if logged_in else "로그인")

    def safe_paste(self, retries=3, retry_delay=0.05):
        for _ in range(retries):
            try:
                return pyperclip.paste()
            except (pyperclip.PyperclipException, OSError):
                time.sleep(retry_delay)
        return ""

    def safe_copy(self, text, retries=3, retry_delay=0.05):
        for _ in range(retries):
            try:
                pyperclip.copy(text)
                return True
            except (pyperclip.PyperclipException, OSError):
                time.sleep(retry_delay)
        return False

