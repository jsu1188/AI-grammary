import hashlib
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI


ENV_PATH = Path(__file__).resolve().with_name(".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)


class AIService:
    def __init__(self):
        api_key = self.current_api_key()
        if not api_key or api_key == "REPLACE_WITH_NEW_OPENAI_API_KEY":
            raise RuntimeError(f"OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다. 확인 경로: {ENV_PATH}")
        self.api_key_fingerprint = self.fingerprint_api_key(api_key)
        self.client = OpenAI(
            api_key=api_key,
            http_client=httpx.Client(trust_env=False, timeout=60.0),
        )
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4.1")
        self._log_config(api_key)

    @classmethod
    def current_api_key(cls) -> str:
        load_dotenv(dotenv_path=ENV_PATH, override=True)
        return (os.environ.get("OPENAI_API_KEY") or "").strip()

    @classmethod
    def current_api_key_fingerprint(cls) -> str:
        return cls.fingerprint_api_key(cls.current_api_key())

    @staticmethod
    def fingerprint_api_key(api_key: str) -> str:
        value = str(api_key or "").strip()
        if not value:
            return "missing"
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"len:{len(value)}:sha256:{digest}"

    def correct_spelling(self, text: str) -> dict:
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "당신은 한국어 문장 교정 전문가입니다. "
                "사용자가 입력한 문장에서 오타 복원, 맞춤법, 띄어쓰기, 문법, 표현 어색함을 함께 바로잡아 주세요. "
                "의미를 멋대로 추가하거나 삭제하지 말고, 원문의 줄바꿈은 최대한 유지하세요. "
                "응답은 설명 없이 교정문만 출력하세요."
            ),
            input=f"원문:\n{text}\n\n교정문:",
        )
        corrected = self._guard_conservative_correction(text, self._clean_output(response.output_text))

        feedback_response = self.client.responses.create(
            model=self.model,
            instructions=(
                "당신은 한국어 교정 피드백 작성 도우미입니다. "
                "교정 결과를 바탕으로 어떤 문제가 있었는지 2~4줄 정도로 간단히 설명하세요. "
                "불릿 없이 평문으로만 작성하세요."
            ),
            input=(
                f"원문:\n{text}\n\n"
                f"교정문:\n{corrected}\n\n"
                "간단한 교정 피드백:"
            ),
        )
        spelling_feedback = self._clean_output(feedback_response.output_text)
        return {
            "corrected_text": corrected,
            "spelling_feedback": spelling_feedback,
        }

    def summarize_text(self, text: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "당신은 한국어 문서 요약 전문가입니다. "
                "핵심 내용만 간결하게 요약하세요. "
                "원문에 없는 사실은 추가하지 마세요."
            ),
            input=text,
        )
        return self._clean_output(response.output_text)

    def evaluate_text(self, text: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "당신은 한국어 글쓰기 평가 도우미입니다. "
                "글의 명확성, 문법, 가독성을 종합해 0점부터 100점 사이 정수 점수 하나만 출력하세요. "
                "설명은 쓰지 말고 예시처럼 '87점' 형식으로만 답하세요."
            ),
            input=text,
        )
        result = self._clean_output(response.output_text)
        digits = "".join(ch for ch in result if ch.isdigit())
        if not digits:
            return "0점"
        score = max(0, min(100, int(digits)))
        return f"{score}점"

    def recommend_title(self, text: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "당신은 한국어 제목 추천 도우미입니다. "
                "입력된 글에 어울리는 짧고 자연스러운 제목 하나만 제안하세요. "
                "따옴표, 번호, 설명 없이 제목만 출력하세요."
            ),
            input=text,
        )
        return self._clean_output(response.output_text)

    def change_tone(self, text: str, tone: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "당신은 한국어 문체와 말투 변환 도우미입니다. "
                "의미는 유지하고, 사용자가 요청한 문체나 말투로 자연스럽게 바꿔 주세요. "
                "줄바꿈은 가능한 한 유지하고, 설명 없이 변환된 본문만 출력하세요."
            ),
            input=f"요청 문체/말투:\n{tone}\n\n원문:\n{text}",
        )
        return self._clean_output(response.output_text)

    def map_style_runs(self, source_text: str, corrected_text: str, style_runs: list[dict]) -> list[dict]:
        payload = {
            "source_text": str(source_text or ""),
            "corrected_text": str(corrected_text or ""),
            "style_runs": style_runs or [],
        }
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "You map source text runs to corrected text. "
                "Return JSON only. "
                "The JSON object must have one key named mapped_runs. "
                "Each mapped run must be an object with only one key: text. "
                "Preserve run order and map the corrected text across the runs as naturally as possible."
            ),
            input="json\n" + json.dumps(payload, ensure_ascii=False),
        )
        text = self._clean_output(response.output_text)
        try:
            data = json.loads(text)
            runs = data.get("mapped_runs", [])
            if isinstance(runs, list):
                return runs
        except Exception:
            pass
        return []

    def map_style_runs_by_slot(self, source_text: str, corrected_text: str, style_runs: list[dict]) -> list[dict]:
        payload = {
            "source_text": str(source_text or ""),
            "corrected_text": str(corrected_text or ""),
            "style_runs": style_runs or [],
            "slot_count": len(style_runs or []),
        }
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "You assign corrected text into existing style slots. "
                "Return JSON only. "
                "The JSON object must have one key named mapped_runs. "
                "mapped_runs must be a list with exactly the same length as style_runs. "
                "Each mapped run must be an object with only one key: text. "
                "Keep the original slot order. "
                "You may redistribute corrected text across slots, but do not invent or omit text. "
                "Concatenating the mapped run texts in order must reconstruct corrected_text exactly."
            ),
            input="json\n" + json.dumps(payload, ensure_ascii=False),
        )
        text = self._clean_output(response.output_text)
        try:
            data = json.loads(text)
            runs = data.get("mapped_runs", [])
            if isinstance(runs, list):
                sanitized = []
                limit = len(style_runs or [])
                for run in runs[:limit]:
                    run_text = run.get("text") if isinstance(run, dict) else ""
                    sanitized.append({"text": str(run_text or "")})
                while len(sanitized) < limit:
                    sanitized.append({"text": ""})
                return sanitized
        except Exception:
            pass
        return []

    def _clean_output(self, text: str) -> str:
        value = str(text or "").strip()
        if value.startswith("```") and value.endswith("```"):
            lines = value.splitlines()
            if len(lines) >= 2:
                value = "\n".join(lines[1:-1]).strip()
        return value

    def _guard_conservative_correction(self, source_text: str, corrected_text: str) -> str:
        source = str(source_text or "").strip()
        corrected = str(corrected_text or "").strip()
        if not source or not corrected:
            return corrected
        source_len = len(source)
        corrected_len = len(corrected)
        if source_len >= 8 and corrected_len < int(source_len * 0.75):
            print(
                "[openai:correct] conservative_guard=source_returned "
                f"source_len={source_len} corrected_len={corrected_len}",
                flush=True,
            )
            return source
        return corrected

    def _log_config(self, api_key: str):
        masked_key = f"{api_key[:7]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
        print(
            f"[openai:config] env_path={ENV_PATH} "
            f"api_key={masked_key} model={self.model}",
            flush=True,
        )
