import ctypes
from ctypes import wintypes
from pathlib import Path
import time
import pyperclip


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_COPY = 0x0301
EM_GETSEL = 0x00B0
EM_SETSEL = 0x00B1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

_EDIT_CLASS_HINTS = ("Edit", "RichEdit", "RichEditD2DPT")
_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_NOTEPAD_LOG_PATH = _LOG_DIR / "notepad_monitor.log"
_last_clipboard_probe_time = 0.0
_last_clipboard_probe_text = ""


def get_active_notepad_snapshot():
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not _is_notepad_window(hwnd):
        return None

    title = _get_window_text(hwnd)
    text, child_details = _read_window_text(hwnd)
    _log_notepad_state(hwnd, text, child_details)
    return {
        "source": "realtime",
        "window_title": title,
        "text": text,
    }


def get_active_notepad_text():
    snapshot = get_active_notepad_snapshot()
    if not snapshot:
        return ""
    return snapshot.get("text", "")


def _is_notepad_window(hwnd):
    title = _get_window_text(hwnd).lower()
    class_name = _get_class_name(hwnd).lower()
    process_name = _get_process_name(hwnd).lower()

    if "notepad.exe" in process_name:
        return True
    if class_name == "notepad":
        return True
    return "\uba54\ubaa8\uc7a5" in title or "notepad" in title


def _read_window_text(hwnd):
    candidates = []
    seen = set()
    child_details = []
    text_handles = []

    root_class_name = _get_class_name(hwnd)
    if _is_text_class(root_class_name):
        text_handles.append(hwnd)
        text = _read_text_from_handle(hwnd)
        if text.strip():
            candidates.append(text)
        child_details.append(("__root__", root_class_name, text))

    @WNDENUMPROC
    def enum_proc(child_hwnd, _lparam):
        if child_hwnd in seen:
            return True
        seen.add(child_hwnd)

        class_name = _get_class_name(child_hwnd)
        text = ""
        if _is_text_class(class_name):
            text_handles.append(child_hwnd)
            text = _read_text_from_handle(child_hwnd)
        child_details.append((str(child_hwnd), class_name, text))
        if text.strip():
            candidates.append(text)
        return True

    user32.EnumChildWindows(hwnd, enum_proc, 0)
    if not candidates and text_handles:
        fallback_text = _read_text_via_clipboard_probe(text_handles)
        if fallback_text.strip():
            candidates.append(fallback_text)
            child_details.append(("__clipboard_probe__", "ClipboardFallback", fallback_text))
    if not candidates:
        return "", child_details
    return max(candidates, key=len), child_details


def _is_text_class(class_name):
    lowered = class_name.lower()
    return any(hint.lower() in lowered for hint in _EDIT_CLASS_HINTS)


def _read_text_from_handle(hwnd):
    length = int(user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0))
    if length <= 0:
        return ""

    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, ctypes.addressof(buffer))
    return buffer.value


def _get_window_text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _get_class_name(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _get_process_name(hwnd):
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not process_id.value:
        return ""

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if not handle:
        return ""

    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _read_text_via_clipboard_probe(handles):
    global _last_clipboard_probe_time, _last_clipboard_probe_text

    now = time.time()
    if now - _last_clipboard_probe_time < 1.0:
        return _last_clipboard_probe_text

    _last_clipboard_probe_time = now
    original_clipboard = _safe_paste()

    try:
        for handle in handles:
            result = _copy_all_text_from_handle(handle)
            if result.strip():
                _last_clipboard_probe_text = result
                return result
    finally:
        _safe_copy(original_clipboard)

    _last_clipboard_probe_text = ""
    return ""


def _copy_all_text_from_handle(hwnd):
    selection = int(user32.SendMessageW(hwnd, EM_GETSEL, 0, 0))
    start = selection & 0xFFFF
    end = (selection >> 16) & 0xFFFF

    user32.SendMessageW(hwnd, EM_SETSEL, 0, -1)
    user32.SendMessageW(hwnd, WM_COPY, 0, 0)
    time.sleep(0.06)
    copied = _safe_paste()
    user32.SendMessageW(hwnd, EM_SETSEL, start, end)
    return copied


def _safe_paste():
    try:
        return pyperclip.paste()
    except Exception:
        return ""


def _safe_copy(text):
    try:
        pyperclip.copy(text or "")
    except Exception:
        pass


def _log_notepad_state(hwnd, text, child_details):
    lines = [
        "=" * 80,
        time.strftime("%Y-%m-%d %H:%M:%S"),
        f"hwnd={hwnd}",
        f"title={_get_window_text(hwnd)!r}",
        f"class={_get_class_name(hwnd)!r}",
        f"process={_get_process_name(hwnd)!r}",
        f"captured_text={text[:300]!r}",
        "children:",
    ]

    for child_hwnd, class_name, child_text in child_details[:80]:
        preview = child_text[:160].replace("\r", "\\r").replace("\n", "\\n")
        lines.append(f"- hwnd={child_hwnd} class={class_name!r} text={preview!r}")

    with _NOTEPAD_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write("\n".join(lines) + "\n")
