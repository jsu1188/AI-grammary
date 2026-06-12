import json
from datetime import datetime
from pathlib import Path

import requests

from client.core.local_server import LocalServer


class AIClient:
    def __init__(self, base_url="http://127.0.0.1:18766"):
        self.base_url = base_url.rstrip("/")
        self.local_server = LocalServer(base_url=self.base_url)
        self._supports_style_map = None
        self._supports_style_slot_map = None
        self._debug_dir = Path(__file__).resolve().parents[2] / ".logs" / "api_debug"
        self._debug_dir.mkdir(parents=True, exist_ok=True)

    def correct_spelling(self, text):
        data = self._post("/correct", {"text": text}, timeout=120)
        return {
            "issues": str(data.get("spelling_feedback", "") or "").strip(),
            "corrected": str(data.get("corrected_text", "") or "").strip(),
        }

    def summarize(self, text, style="brief"):
        data = self._post("/summary", {"text": text, "style": style}, timeout=120)
        return str(data.get("summary_text", "") or "").strip()

    def evaluate(self, text):
        data = self._post("/evaluate", {"text": text}, timeout=120)
        return str(data.get("score_text", "") or "").strip()

    def evaluate_reason(self, text, score_text=""):
        data = self._post("/evaluate-reason", {"text": text, "score_text": score_text}, timeout=120)
        return str(data.get("evaluation_reason", "") or "").strip()

    def recommend_title(self, text):
        data = self._post("/title", {"text": text}, timeout=120)
        return str(data.get("title_text", "") or "").strip()

    def change_tone(self, text, tone):
        data = self._post("/tone", {"text": text, "tone": tone}, timeout=120)
        return str(data.get("changed_text", "") or "").strip()

    def map_style_runs(self, source_text, corrected_text, style_runs):
        if not self._style_map_supported():
            return []
        payload_runs = []
        for run in style_runs or []:
            text = run.get("text")
            if isinstance(text, str):
                payload_runs.append({"text": text})
        data = self._post(
            "/style-map",
            {
                "source_text": source_text,
                "corrected_text": corrected_text,
                "style_runs": payload_runs,
            },
            timeout=120,
        )
        return data.get("mapped_runs", []) or []

    def map_style_runs_by_slot(self, source_text, corrected_text, style_runs):
        if not self._style_slot_map_supported():
            return []
        payload_runs = []
        for run in style_runs or []:
            text = run.get("text")
            if isinstance(text, str):
                payload_runs.append({"text": text})
        data = self._post(
            "/style-map-slots",
            {
                "source_text": source_text,
                "corrected_text": corrected_text,
                "style_runs": payload_runs,
            },
            timeout=120,
        )
        return data.get("mapped_runs", []) or []

    def _style_map_supported(self):
        if self._supports_style_map is not None:
            return self._supports_style_map
        try:
            routes = self._server_routes()
            self._supports_style_map = "/style-map" in routes
            return self._supports_style_map
        except Exception:
            self._supports_style_map = False
            return False

    def _style_slot_map_supported(self):
        if self._supports_style_slot_map is not None:
            return self._supports_style_slot_map
        try:
            routes = self._server_routes()
            self._supports_style_slot_map = "/style-map-slots" in routes
            return self._supports_style_slot_map
        except Exception:
            self._supports_style_slot_map = False
            return False

    def _server_routes(self):
        self.local_server.ensure_running()
        response = requests.get(f"{self.base_url}/server-info", timeout=0.8)
        if response.status_code >= 500:
            return []
        return response.json().get("openai_routes") or []

    def request(self, prompt):
        source_text = prompt.split("\n", 1)[1] if "\n" in prompt else prompt
        return self.correct_spelling(source_text)

    def _post(self, path, payload, timeout=60):
        self.local_server.ensure_running()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{timestamp}_{path.strip('/').replace('/', '_')}.json"
        debug_path = self._debug_dir / filename
        debug_data = {
            "timestamp": timestamp,
            "url": f"{self.base_url}{path}",
            "request": payload,
            "status_code": None,
            "response": None,
            "error": None,
        }
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=timeout,
            )
            debug_data["status_code"] = response.status_code
            try:
                debug_data["response"] = response.json()
            except Exception:
                debug_data["response"] = response.text
            response.raise_for_status()
            result = response.json()
            self._write_debug_snapshot(debug_path, debug_data)
            return result
        except requests.HTTPError as exc:
            detail = ""
            try:
                detail = response.json().get("detail", "")
            except Exception:
                detail = response.text
            debug_data["error"] = detail or str(exc)
            self._write_debug_snapshot(debug_path, debug_data)
            raise RuntimeError(detail or str(exc)) from exc
        except requests.RequestException as exc:
            debug_data["error"] = str(exc)
            self._write_debug_snapshot(debug_path, debug_data)
            raise RuntimeError(f"OpenAI 요청 통신 실패: {exc}") from exc

    def _write_debug_snapshot(self, path: Path, data: dict):
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
