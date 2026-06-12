import time

import requests

from client.config import REMOTE_SERVER_URL


class LocalServer:
    REQUIRED_API_VERSION = "openai-separated-v8"
    REQUIRED_ROUTES = {
        "/correct",
        "/evaluate",
        "/evaluate-reason",
        "/title",
        "/summary",
        "/tone",
        "/style-map",
        "/style-map-slots",
        "/history/requests",
    }

    def __init__(self, base_url=REMOTE_SERVER_URL):
        self.base_url = base_url.rstrip("/")

    def ensure_running(self, timeout=8.0):
        if self._is_running():
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_running():
                return
            time.sleep(0.15)
        raise RuntimeError("원격 서버에 연결하지 못했습니다.")

    def stop(self):
        return

    def _is_running(self):
        try:
            response = requests.get(f"{self.base_url}/server-info", timeout=0.5)
            if response.status_code >= 500:
                return False
            data = response.json()
            routes = set(data.get("openai_routes") or [])
            return (
                data.get("api_version") == self.REQUIRED_API_VERSION
                and self.REQUIRED_ROUTES.issubset(routes)
            )
        except Exception:
            return False

    def _has_incompatible_server(self):
        return False
