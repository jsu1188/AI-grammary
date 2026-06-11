from __future__ import annotations

import json
import time
from pathlib import Path

import pythoncom
from pywintypes import IID
import win32com.client as win32


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = PROJECT_ROOT / ".logs" / "hwp_textfile_diagnostic.log"
HWP_ACTIVE_PROGIDS = (
    "HWPFrame.HwpObject.2",
    "HWPFrame.HwpObject.1",
    "HWPFrame.HwpObject",
)
HWP_IHWP_OBJECT_IID = "{5E6A8276-CF1C-42B8-BCED-319548B02AF6}"
FORMATS = (
    "TEXT",
    "UNICODE",
    "HTML",
    "HWPML",
    "HWPML2X",
    "HWPML2X_S",
    "HWPML2X_P",
    "HWPML2X_STYLE",
)


def main():
    pythoncom.CoInitialize()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    hwp, attach_info = active_hwp()
    if hwp is None:
        result = {"timestamp": now(), "status": "no_hwp_object", "attach": attach_info}
    else:
        result = {"timestamp": now(), "status": "ok", "attach": attach_info, "formats": try_formats(hwp)}
    LOG_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def active_hwp():
    attempts = []
    for progid in HWP_ACTIVE_PROGIDS:
        try:
            hwp = coerce_hwp_object(win32.GetActiveObject(progid))
            if hwp is not None:
                attempts.append({"source": progid, "status": "ok"})
                return hwp, attempts
            attempts.append({"source": progid, "status": "not_usable"})
        except Exception as exc:
            attempts.append({"source": progid, "status": "fail", "error": f"{type(exc).__name__}: {exc}"})

    try:
        rot = pythoncom.GetRunningObjectTable()
        enum_moniker = rot.EnumRunning()
        bind_context = pythoncom.CreateBindCtx(0)
    except Exception as exc:
        attempts.append({"source": "ROT", "status": "fail", "error": f"{type(exc).__name__}: {exc}"})
        return None, attempts

    while True:
        monikers = enum_moniker.Next(1)
        if not monikers:
            return None, attempts
        moniker = monikers[0]
        try:
            name = moniker.GetDisplayName(bind_context, None)
        except Exception:
            name = ""
        lowered = str(name).lower()
        if "hwp" not in lowered and "hancom" not in lowered and "hword" not in lowered:
            continue
        attempt = {"source": "ROT", "name": str(name)}
        try:
            obj = rot.GetObject(moniker)
            hwp = coerce_hwp_object(obj)
            if hwp is not None:
                attempt["status"] = "ok"
                attempts.append(attempt)
                return hwp, attempts
            attempt["status"] = "not_usable"
        except Exception as exc:
            attempt.update({"status": "fail", "error": f"{type(exc).__name__}: {exc}"})
        attempts.append(attempt)


def coerce_hwp_object(obj):
    if obj is None:
        return None
    for candidate in hwp_dispatch_candidates(obj):
        if all(hasattr(candidate, name) for name in ("GetTextFile", "HAction", "HParameterSet")):
            return candidate
    return None


def hwp_dispatch_candidates(obj):
    candidates = [obj]
    try:
        candidates.append(win32.Dispatch(obj))
    except Exception:
        pass
    for source in (obj, getattr(obj, "_oleobj_", None)):
        query = getattr(source, "QueryInterface", None)
        if not callable(query):
            continue
        for iid in hwp_query_interface_iids():
            try:
                candidates.append(win32.Dispatch(query(iid)))
            except Exception:
                pass
    wrapped = []
    for candidate in candidates:
        wrapped.append(candidate)
        try:
            wrapped.append(win32.CastTo(candidate, "IHwpObject"))
        except Exception:
            pass
    return wrapped


def hwp_query_interface_iids():
    iids = []
    try:
        iids.append(IID(HWP_IHWP_OBJECT_IID))
    except Exception:
        pass
    try:
        iids.append(pythoncom.IID_IDispatch)
    except Exception:
        pass
    return iids


def try_formats(hwp) -> list[dict]:
    rows = []
    for fmt in FORMATS:
        for option in ("", "saveblock", "selection"):
            row = {"format": fmt, "option": option}
            try:
                data = hwp.GetTextFile(fmt, option)
                text = str(data) if data is not None else ""
                row.update(
                    {
                        "status": "ok",
                        "length": len(text),
                        "preview": text[:400],
                        "has_charshape_hint": any(
                            token in text.lower()
                            for token in ("charshape", "charpr", "textcolor", "underline", "fontref")
                        ),
                    }
                )
            except Exception as exc:
                row.update({"status": "fail", "error": f"{type(exc).__name__}: {exc}"})
            rows.append(row)
    return rows


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
