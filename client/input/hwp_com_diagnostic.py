from __future__ import annotations

import argparse
import ctypes
import json
import time
from pathlib import Path

import pythoncom
from pywintypes import IID
import win32com.client as win32

try:
    import win32gui
    import win32process
except Exception:
    win32gui = None
    win32process = None

try:
    import psutil
except Exception:
    psutil = None


PROGIDS = (
    "HWPFrame.HwpObject.2",
    "HWPFrame.HwpObject.1",
    "HWPFrame.HwpObject",
    "HwpAutomationApp2.HwpAutomation.2",
    "HwpAutomationApp2.HwpAutomation.1",
    "HwpAutomationApp2.HwpAutomation",
)
HWP_TYPELIB_CLSID = "{7D2B6F3C-1D95-4E0C-BF5A-5EE564186FBC}"
HWP_IHWP_OBJECT_IID = IID("{5E6A8276-CF1C-42B8-BCED-319548B02AF6}")
HWP_REQUIRED_READ_METHODS = ("InitScan", "GetText", "ReleaseScan", "HAction", "HParameterSet")
HWP_REQUIRED_WRITE_METHODS = ("MovePos", "Run", "HAction", "HParameterSet")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = PROJECT_ROOT / ".logs" / "hwp_com_diagnostic.log"


def main():
    parser = argparse.ArgumentParser(description="Diagnose Hancom HWP COM attachment.")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Also try Dispatch/EnsureDispatch. This may start hidden HWP COM instances.",
    )
    args = parser.parse_args()

    pythoncom.CoInitialize()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "foreground": foreground_info(),
        "get_active_object": try_get_active_objects(),
        "rot": list_rot_entries(),
    }
    if args.create:
        result["create_object"] = try_create_objects()

    LOG_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def foreground_info() -> dict:
    if win32gui is None:
        return {}
    try:
        hwnd = int(win32gui.GetForegroundWindow())
    except Exception:
        return {}
    return {
        "hwnd": hwnd,
        "title": safe_call(lambda: win32gui.GetWindowText(hwnd) or ""),
        "class_name": safe_call(lambda: win32gui.GetClassName(hwnd) or ""),
        "process_name": process_name(hwnd),
    }


def process_name(hwnd: int) -> str:
    if win32process is None:
        return ""
    try:
        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        return ""
    if not pid or psutil is None:
        return ""
    try:
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def try_get_active_objects() -> list[dict]:
    results = []
    for progid in PROGIDS:
        try:
            obj = win32.GetActiveObject(progid)
            results.append(describe_object(progid, "ok", obj))
        except Exception as exc:
            results.append({"progid": progid, "status": "fail", "error": describe_error(exc)})
    return results


def try_create_objects() -> list[dict]:
    results = []
    for progid in PROGIDS:
        row = {"progid": progid, "dispatch": None, "ensure_dispatch": None}
        try:
            row["dispatch"] = describe_object(progid, "ok", win32.Dispatch(progid))
        except Exception as exc:
            row["dispatch"] = {"status": "fail", "error": describe_error(exc)}
        try:
            row["ensure_dispatch"] = describe_object(progid, "ok", win32.gencache.EnsureDispatch(progid))
        except Exception as exc:
            row["ensure_dispatch"] = {"status": "fail", "error": describe_error(exc)}
        results.append(row)
    return results


def list_rot_entries() -> list[dict]:
    entries = []
    try:
        rot = pythoncom.GetRunningObjectTable()
        enum_moniker = rot.EnumRunning()
        bind_context = pythoncom.CreateBindCtx(0)
    except Exception as exc:
        return [{"status": "fail", "error": describe_error(exc)}]

    while True:
        monikers = enum_moniker.Next(1)
        if not monikers:
            break
        moniker = monikers[0]
        try:
            display_name = moniker.GetDisplayName(bind_context, None)
        except Exception as exc:
            display_name = f"<display-name-error: {describe_error(exc)}>"
        entry = {"display_name": display_name}
        lowered = str(display_name).lower()
        if any(token in lowered for token in ("hwp", "hancom", "hword")):
            try:
                entry["object"] = describe_object(display_name, "ok", rot.GetObject(moniker))
            except Exception as exc:
                entry["object"] = {"status": "fail", "error": describe_error(exc)}
        entries.append(entry)
    return entries


def describe_object(name: str, status: str, obj) -> dict:
    attempts = describe_hwp_cast_attempts(obj)
    return {
        "name": name,
        "status": status,
        "type": str(type(obj)),
        "has_xhwp_windows": hasattr(obj, "XHwpWindows"),
        "window_count": safe_call(lambda: int(obj.XHwpWindows.Count), None),
        "version": safe_call(lambda: str(getattr(obj, "Version", "")), ""),
        "dispatch_members": describe_dispatch_members(obj),
        "hwp_cast_attempts": attempts,
    }


def describe_dispatch_members(obj) -> dict:
    return {
        "read_methods": {name: hasattr(obj, name) for name in HWP_REQUIRED_READ_METHODS},
        "write_methods": {name: hasattr(obj, name) for name in HWP_REQUIRED_WRITE_METHODS},
    }


def describe_hwp_cast_attempts(obj) -> list[dict]:
    attempts = []
    ensure_hwp_typelib(attempts)
    candidates = [("raw", obj)]
    oleobj = getattr(obj, "_oleobj_", None)
    if oleobj is not None:
        candidates.append(("_oleobj_", oleobj))

    for source, candidate in candidates:
        attempts.append(try_wrap(source, "Dispatch", lambda c=candidate: win32.Dispatch(c)))
        attempts.append(try_wrap(source, "CastTo(IHwpObject)", lambda c=candidate: win32.CastTo(c, "IHwpObject")))
        query = getattr(candidate, "QueryInterface", None)
        if callable(query):
            attempts.append(
                try_wrap(source, "QI(IHwpObject)+Dispatch", lambda q=query: win32.Dispatch(q(HWP_IHWP_OBJECT_IID)))
            )
            attempts.append(
                try_wrap(source, "QI(IDispatch)+Dispatch", lambda q=query: win32.Dispatch(q(pythoncom.IID_IDispatch)))
            )
    return attempts


def ensure_hwp_typelib(attempts: list[dict]):
    try:
        module = win32.gencache.EnsureModule(HWP_TYPELIB_CLSID, 0, 1, 0)
        attempts.append({"source": "typelib", "method": "EnsureModule", "status": "ok", "module": str(module)})
    except Exception as exc:
        attempts.append({"source": "typelib", "method": "EnsureModule", "status": "fail", "error": describe_error(exc)})


def try_wrap(source: str, method: str, callback) -> dict:
    row = {"source": source, "method": method}
    try:
        wrapped = callback()
        row.update(
            {
                "status": "ok",
                "type": str(type(wrapped)),
                "usable_read": all(hasattr(wrapped, name) for name in HWP_REQUIRED_READ_METHODS),
                "usable_write": all(hasattr(wrapped, name) for name in HWP_REQUIRED_WRITE_METHODS),
                "members": describe_dispatch_members(wrapped),
                "version": safe_call(lambda: str(getattr(wrapped, "Version", "")), ""),
            }
        )
    except Exception as exc:
        row.update({"status": "fail", "error": describe_error(exc)})
    return row


def safe_call(callback, default=None):
    try:
        return callback()
    except Exception:
        return default


def describe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    main()
