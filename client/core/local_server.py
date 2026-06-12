import os
import subprocess
import sys
import time
from pathlib import Path

import requests


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

    def __init__(self, base_url="http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")
        self.process = None

    def ensure_running(self, timeout=8.0):
        if self._is_running():
            return
        if self._has_incompatible_server():
            raise RuntimeError(
                "8765 포트에 이전 버전 로컬 서버가 실행 중입니다. "
                "기존 Python/uvicorn 서버를 종료한 뒤 다시 실행해주세요."
            )

        server_dir = Path(__file__).resolve().parents[2] / "server"
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # Avoid broken global proxy settings (e.g. 127.0.0.1:9) from blocking OpenAI calls.
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env.pop(key, None)
        port = self.base_url.rsplit(":", 1)[-1]
        if port.isdigit():
            env["CHECKWORD_SERVER_PORT"] = port
        self.process = subprocess.Popen(
            [sys.executable, str(server_dir / "launch_server.py")],
            cwd=str(server_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_running():
                return
            time.sleep(0.15)
        raise RuntimeError("로그인 서버를 시작하지 못했습니다.")

    def stop(self):
        if self.process is None:
            return
        try:
            self.process.terminate()
        except Exception:
            pass
        self.process = None

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
        try:
            response = requests.get(f"{self.base_url}/server-info", timeout=0.5)
            if response.status_code >= 500:
                return False
            data = response.json()
            routes = set(data.get("openai_routes") or [])
            return (
                data.get("api_version") != self.REQUIRED_API_VERSION
                or not self.REQUIRED_ROUTES.issubset(routes)
            )
        except Exception:
            return False
