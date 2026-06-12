import time
from pathlib import Path

from client.input.ai_grammary_text_reader import (
    BROWSER_PROCESS_NAMES,
    HWP_PROCESS_NAMES,
    UniversalActiveTextReader,
    WORD_PROCESS_NAMES,
    get_foreground_hwnd,
    get_process_name,
)
from client.input.browser_extension_bridge import get_browser_extension_bridge
from client.input.keyboard_monitor import monitor_typed_text

try:
    import win32api
except Exception:  # pragma: no cover - optional Windows dependency
    win32api = None


_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_ERROR_LOG_PATH = _LOG_DIR / "realtime_monitor_errors.log"
_EXTERNAL_POLL_PAUSE_UNTIL = 0.0
_EXTERNAL_POLL_PAUSED = False


def monitor_realtime_text(callback, poll_interval=0.25, debug=False, get_active_mode=None):
    """Poll active apps with the AI-grammary reader stack.

    The public callback contract is kept identical to writing-assistant 0.1.0:
    callback receives {"source": "realtime", "window_title": str, "text": str}.
    """
    browser_bridge = get_browser_extension_bridge()
    try:
        browser_bridge.start()
    except Exception as exc:
        _log_error("browser_bridge_start", exc)

    try:
        reader = UniversalActiveTextReader(debug=debug)
    except Exception as exc:
        _log_error("reader_init", exc)
        monitor_typed_text(lambda text: callback(_typed_text_event(text)))
        return

    input_pause = _ForegroundInputPause()

    selection_poll_interval = min(poll_interval, 0.08)
    last_active_mode = None

    while True:
        try:
            active_mode = get_active_mode() if callable(get_active_mode) else "realtime"
            if active_mode != last_active_mode:
                reset_reader = getattr(reader, "reset_state", None)
                if callable(reset_reader):
                    reset_reader()
                input_pause.reset()
                last_active_mode = active_mode
            loop_sleep = selection_poll_interval if active_mode == "selection" else poll_interval

            if is_poll_temporarily_paused():
                time.sleep(loop_sleep)
                continue

            browser_event = browser_bridge.poll_event()
            if browser_event is not None:
                if _should_use_browser_bridge_event(browser_event, active_mode):
                    callback(browser_event)
                    time.sleep(loop_sleep)
                    continue

            if input_pause.should_skip_poll():
                time.sleep(loop_sleep)
                continue

            snapshot = reader.poll_snapshot() if active_mode == "realtime" else None
            if snapshot is not None:
                callback(
                    {
                        "source": snapshot.source,
                        "window_title": snapshot.window_title,
                        "text": snapshot.text,
                        "reader": snapshot.reader_name,
                        "window_handle": snapshot.window_handle,
                        "style_info": snapshot.style_info,
                    }
                )

            selection_snapshot = reader.poll_snapshot(selection_only=True) if active_mode == "selection" else None
            if selection_snapshot is not None:
                callback(
                    {
                        "source": selection_snapshot.source,
                        "window_title": selection_snapshot.window_title,
                        "text": selection_snapshot.text,
                        "reader": selection_snapshot.reader_name,
                        "window_handle": selection_snapshot.window_handle,
                        "style_info": selection_snapshot.style_info,
                    }
                )
        except Exception as exc:
            _log_error("reader_poll", exc)

        time.sleep(loop_sleep)


class _ForegroundInputPause:
    SKIP_AFTER_KEY_SECONDS = 0.55
    KEY_RANGE = range(0x08, 0xFF)

    def __init__(self):
        self.last_key_activity = 0.0

    def reset(self):
        self.last_key_activity = 0.0

    def should_skip_poll(self) -> bool:
        if not self._is_foreground_sensitive_editor():
            return False
        now = time.monotonic()
        if self._has_keyboard_activity():
            self.last_key_activity = now
            return True
        return now - self.last_key_activity < self.SKIP_AFTER_KEY_SECONDS

    def _is_foreground_sensitive_editor(self) -> bool:
        try:
            hwnd = get_foreground_hwnd()
            process_name = get_process_name(hwnd)
            return process_name in WORD_PROCESS_NAMES or process_name in HWP_PROCESS_NAMES
        except Exception:
            return False

    def _has_keyboard_activity(self) -> bool:
        if win32api is None:
            return False
        for key_code in self.KEY_RANGE:
            try:
                state = win32api.GetAsyncKeyState(key_code)
                if state & 0x8000 or state & 0x0001:
                    return True
            except Exception:
                return False
        return False


def pause_polling_for(seconds: float):
    global _EXTERNAL_POLL_PAUSE_UNTIL
    try:
        seconds_value = max(0.0, float(seconds))
    except Exception:
        seconds_value = 0.0
    _EXTERNAL_POLL_PAUSE_UNTIL = max(
        _EXTERNAL_POLL_PAUSE_UNTIL,
        time.monotonic() + seconds_value,
    )


def set_polling_paused(paused: bool):
    global _EXTERNAL_POLL_PAUSED
    _EXTERNAL_POLL_PAUSED = bool(paused)


def is_poll_temporarily_paused() -> bool:
    return _EXTERNAL_POLL_PAUSED or time.monotonic() < _EXTERNAL_POLL_PAUSE_UNTIL


def _should_use_browser_bridge_event(event: dict, active_mode: str) -> bool:
    if not isinstance(event, dict):
        return False
    source = event.get("source")
    if active_mode == "selection":
        return source == "selection" and _is_foreground_browser()
    if source != "realtime":
        return False
    return _is_foreground_browser()


def _is_foreground_browser() -> bool:
    try:
        return get_process_name(get_foreground_hwnd()) in BROWSER_PROCESS_NAMES
    except Exception:
        return False


def _typed_text_event(text):
    return {
        "source": "realtime",
        "window_title": "",
        "text": text,
        "reader": "keyboard",
    }


def _log_error(stage, exc):
    try:
        with _ERROR_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{stage}] {type(exc).__name__}: {exc}\n")
    except Exception:
        pass

