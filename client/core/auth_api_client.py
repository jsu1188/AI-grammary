import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import requests


class UnauthorizedError(ValueError):
    pass


class AuthAPIClient:
    SESSION_FILE = Path(__file__).resolve().parents[2] / ".auth_session.json"

    def __init__(self, base_url="http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")
        self.access_token = None
        self.current_username = None

    def try_restore_session(self):
        session = self._load_saved_session()
        if not session:
            return None

        token = session.get("access_token")
        username = session.get("username")
        remember_me = bool(session.get("remember_me", False))
        if not token or not username:
            return None
        if not remember_me and self._is_token_expired(token):
            self.clear_token()
            return None

        self.access_token = token
        self.current_username = username
        return username

    def signup(self, username, password):
        response = requests.post(
            f"{self.base_url}/signup",
            json={"username": username, "password": password},
            timeout=20,
        )
        return self._handle_response(response)

    def login(self, username, password, remember_me=False):
        response = requests.post(
            f"{self.base_url}/login",
            json={
                "username": username,
                "password": password,
                "remember_me": bool(remember_me),
            },
            timeout=20,
        )
        data = self._handle_response(response)
        self.access_token = data["access_token"]
        self.current_username = username
        self._save_session(username, self.access_token, remember_me)
        return data

    def verify_account(self, password):
        return self._authorized_request(
            "post",
            "/account/verify",
            json={
                "username": self.current_username or "",
                "password": password,
            },
        )

    def get_account(self):
        return self._authorized_request("get", "/account")

    def update_account(self, payload):
        data = self._authorized_request("put", "/account", json=payload)
        if data.get("access_token"):
            self.access_token = data["access_token"]
            self.current_username = data.get("username", self.current_username)
            session = self._load_saved_session() or {}
            self._save_session(
                self.current_username,
                self.access_token,
                bool(session.get("remember_me", False)),
            )
        else:
            self.current_username = data.get("username", self.current_username)
        return data

    def delete_account(self):
        return self._authorized_request("delete", "/account")

    def clear_token(self):
        self.access_token = None
        self.current_username = None
        try:
            if self.SESSION_FILE.exists():
                self.SESSION_FILE.unlink()
        except Exception:
            pass

    def create_log(self, payload):
        return self._authorized_request("post", "/logs", json=payload)

    def list_logs(self, feature_type=None):
        params = {}
        if feature_type is not None:
            params["feature_type"] = feature_type
        return self._authorized_request("get", "/logs", params=params)

    def list_history_requests(self):
        return self._authorized_request("get", "/history/requests")

    def delete_history_request(self, request_id):
        return self._authorized_request("delete", f"/history/requests/{int(request_id)}")

    def delete_all_history_requests(self):
        return self._authorized_request("delete", "/history/requests")

    def get_settings(self):
        return self._authorized_request("get", "/settings")

    def update_settings(self, settings):
        return self._authorized_request("put", "/settings", json=settings)

    def _authorized_request(self, method, path, **kwargs):
        if not self.access_token:
            raise UnauthorizedError("로그인이 필요합니다.")
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=30,
            **kwargs,
        )
        if response.status_code == 401:
            self.clear_token()
            raise UnauthorizedError("로그인이 만료되었습니다. 다시 로그인해 주세요.")
        return self._handle_response(response)

    def _save_session(self, username, token, remember_me):
        self.SESSION_FILE.write_text(
            json.dumps(
                {
                    "username": username,
                    "access_token": token,
                    "remember_me": bool(remember_me),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load_saved_session(self):
        try:
            if not self.SESSION_FILE.exists():
                return None
            return json.loads(self.SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _is_token_expired(self, token):
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
            exp = data.get("exp")
            return exp is None or datetime.now(timezone.utc).timestamp() >= float(exp)
        except Exception:
            return True

    @staticmethod
    def _handle_response(response):
        try:
            data = response.json()
        except Exception:
            data = {"detail": response.text}
        if response.status_code >= 400:
            raise ValueError(data.get("detail", "요청 처리 중 오류가 발생했습니다."))
        return data
