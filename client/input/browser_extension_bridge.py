from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from client.core.line_structure import preserve_blank_lines


BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8766
_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_BROWSER_BRIDGE_LOG_PATH = _LOG_DIR / "browser_extension_bridge.log"


@dataclass
class BrowserExtensionBridge:
    host: str = BRIDGE_HOST
    port: int = BRIDGE_PORT
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _latest_event: dict[str, Any] | None = None
    _latest_signature: tuple[str, str, str, str, str] | None = None
    _pending_commands: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _session_text_memory: dict[str, str] = field(default_factory=dict)
    _capture_count: int = 0
    _command_poll_count: int = 0
    _last_command_poll_log: float = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_OPTIONS(self):
                self._send_json({"ok": True})

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send_json({"ok": True, "service": "writing-assistant-browser-bridge", "stats": bridge.stats()})
                    return
                if parsed.path == "/command":
                    query = parse_qs(parsed.query)
                    session_id = (query.get("session_id") or [""])[0]
                    self._send_json({"command": bridge.pop_command(session_id), "stats": bridge.stats()})
                    return
                self._send_json({"error": "not_found"}, status=404)

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path == "/capture":
                    payload = self._read_json()
                    event = bridge.record_capture(payload)
                    self._send_json({"ok": True, "accepted": bool(event)})
                    return
                if parsed.path == "/applied":
                    payload = self._read_json()
                    bridge.record_applied(payload)
                    self._send_json({"ok": True})
                    return
                self._send_json({"error": "not_found"}, status=404)

            def log_message(self, _format, *_args):
                return

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    data = {}
                return data if isinstance(data, dict) else {}

            def _send_json(self, payload: dict[str, Any], status: int = 200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._log(f"bridge_start host={self.host!r} port={self.port}")

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self._server = None
        self._thread = None

    def record_capture(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        text = str(payload.get("text") or "")
        if not text.strip():
            return None
        session_id = str(payload.get("session_id") or "")
        dom_debug = payload.get("dom_debug") or {}
        selection = payload.get("selection") or {}
        is_selection = bool(selection.get("selected"))
        if not is_selection and dom_debug.get("hasRange") and self._session_text_memory.get(session_id):
            return None

        previous_text = self._session_text_memory.get(session_id, "")
        if not is_selection:
            text = self._restore_session_blank_lines(session_id, previous_text, text)
        event = {
            "source": "selection" if is_selection else "realtime",
            "reader": "browser_extension",
            "window_title": str(payload.get("title") or payload.get("url") or "Browser"),
            "window_handle": None,
            "text": text,
            "style_info": {
                "read_method": "browser_extension",
                "browser_session_id": session_id,
                "url": str(payload.get("url") or ""),
                "selection": selection,
                "selection_mode": is_selection,
                "segments": payload.get("segments") or [],
                "html": str(payload.get("html") or ""),
                "target_kind": str(payload.get("target_kind") or ""),
                "dom_debug": dom_debug,
            },
        }
        segments_signature = json.dumps(event["style_info"].get("segments") or [], ensure_ascii=False, sort_keys=True)
        signature = (
            session_id,
            event["window_title"],
            text,
            event["style_info"].get("html") or "",
            segments_signature,
        )
        with self._lock:
            if signature == self._latest_signature:
                return None
            self._latest_signature = signature
            self._latest_event = event
            if not is_selection:
                self._session_text_memory[session_id] = text
            self._capture_count += 1
        self._log(
            "capture "
            f"session={session_id!r} kind={event['style_info'].get('target_kind')!r} "
            f"text_len={len(text)} newlines={text.count(chr(10))} "
            f"segments={len(event['style_info'].get('segments') or [])} "
            f"title={event['window_title']!r} "
            f"sample={self._segment_sample(event['style_info'].get('segments') or [])!r} "
            f"dom={self._dom_debug_sample(event['style_info'].get('dom_debug') or {})!r}"
        )
        return event

    def _restore_session_blank_lines(self, session_id: str, previous_text: str, current_text: str) -> str:
        if not previous_text:
            return current_text
        previous_content_count = self._content_line_count(previous_text)
        current_content_count = self._content_line_count(current_text)
        if previous_content_count == 0 or previous_content_count != current_content_count:
            return current_text
        previous_blank_count = self._blank_line_count(previous_text)
        current_blank_count = self._blank_line_count(current_text)
        if previous_blank_count <= current_blank_count:
            return current_text
        restored = preserve_blank_lines(previous_text, current_text)
        if restored != current_text:
            self._log(
                "blank_restore "
                f"session={session_id!r} blank_lines={current_blank_count}->{previous_blank_count} "
                f"text_len={len(current_text)}->{len(restored)}"
            )
        return restored

    def _content_line_count(self, text: str) -> int:
        return sum(1 for line in self._split_lines(text) if line.strip())

    def _blank_line_count(self, text: str) -> int:
        return sum(1 for line in self._split_lines(text) if not line.strip())

    def _split_lines(self, text: str) -> list[str]:
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def poll_event(self) -> dict[str, Any] | None:
        with self._lock:
            event = self._latest_event
            self._latest_event = None
        return event

    def queue_apply(self, session_id: str, text: str, style_info: dict[str, Any] | None = None):
        if not session_id:
            raise RuntimeError("No browser extension session is available.")
        command = {
            "type": "replace_selection",
            "text": text,
            "style_info": style_info or {},
            "created_at": time.time(),
        }
        with self._lock:
            self._pending_commands.setdefault(session_id, []).append(command)
        self._log(
            "queue_apply "
            f"session={session_id!r} text_len={len(text)} "
            f"newlines={str(text).count(chr(10))} "
            f"segments={len((style_info or {}).get('segments') or [])} "
            f"kind={(style_info or {}).get('target_kind')!r}"
        )

    def record_applied(self, payload: dict[str, Any]):
        before = payload.get("before") or {}
        after = payload.get("after") or {}
        self._log(
            "applied "
            f"session={str(payload.get('session_id') or '')!r} "
            f"method={str(payload.get('method') or '')!r} "
            f"text_len={len(str(payload.get('text') or ''))} "
            f"newlines={str(payload.get('text') or '').count(chr(10))} "
            f"before={self._dom_debug_sample(before)!r} "
            f"after={self._dom_debug_sample(after)!r}"
        )

    def pop_command(self, session_id: str) -> dict[str, Any] | None:
        if not session_id:
            return None
        with self._lock:
            queue = self._pending_commands.get(session_id) or []
            self._command_poll_count += 1
            now = time.time()
            if now - self._last_command_poll_log > 10:
                self._last_command_poll_log = now
                self._log(
                    f"command_poll session={session_id!r} polls={self._command_poll_count} "
                    f"pending_sessions={len(self._pending_commands)}"
                )
            if not queue:
                return None
            command = queue.pop(0)
            if not queue:
                self._pending_commands.pop(session_id, None)
            self._log(f"pop_command session={session_id!r} type={command.get('type')!r}")
            return command

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "captures": self._capture_count,
                "command_polls": self._command_poll_count,
                "pending_sessions": len(self._pending_commands),
                "has_latest_event": self._latest_event is not None,
            }

    def _log(self, message: str):
        try:
            with _BROWSER_BRIDGE_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except Exception:
            pass

    def _segment_sample(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sample = []
        for segment in segments[:6]:
            style = segment.get("style") or {}
            sample.append(
                {
                    "range": [segment.get("start"), segment.get("end")],
                    "fontWeight": style.get("fontWeight"),
                    "fontStyle": style.get("fontStyle"),
                    "color": style.get("color"),
                    "textDecorationLine": style.get("textDecorationLine"),
                }
            )
        return sample

    def _dom_debug_sample(self, dom_debug: dict[str, Any]) -> dict[str, Any]:
        nodes = dom_debug.get("textNodes") or []
        sample_nodes = []
        for node in nodes[:5]:
            style = node.get("style") or {}
            sample_nodes.append(
                {
                    "text": node.get("text"),
                    "parent": node.get("parent"),
                    "parentStyle": node.get("parentStyle"),
                    "fontWeight": style.get("fontWeight"),
                    "fontStyle": style.get("fontStyle"),
                    "color": style.get("color"),
                    "textDecorationLine": style.get("textDecorationLine"),
                }
            )
        return {
            "childElementCount": dom_debug.get("childElementCount"),
            "hasRange": dom_debug.get("hasRange"),
            "textPreview": str(dom_debug.get("textPreview") or "")[:160],
            "htmlPreview": str(dom_debug.get("htmlPreview") or "")[:160],
            "textNodes": sample_nodes,
        }


_BRIDGE = BrowserExtensionBridge()


def get_browser_extension_bridge() -> BrowserExtensionBridge:
    return _BRIDGE
