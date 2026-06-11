from client.core.ai_client import AIClient


class TextAnalyzer:
    TEMP_SPELLING_FEEDBACK = "OpenAI 맞춤법 교정 결과입니다."
    TEMP_RESULT_MARKERS = {
        "spelling": "",
        "summary": "",
        "tone": "",
    }

    def __init__(self):
        self.ai = AIClient()

    def analyze_spelling(self, text):
        result = self.check_spelling(text)
        return self.format_spell_check(result)

    def get_spelling_result_payload(self, text):
        raw_result = self.check_spelling(text)
        formatted_result = self.format_spell_check(raw_result)
        corrected_text = self.extract_spelling_corrected_text(raw_result, formatted_result)
        feedback = ""
        if isinstance(raw_result, dict):
            feedback = str(raw_result.get("issues", "") or "").strip()
        return {
            "raw": raw_result,
            "formatted": formatted_result,
            "corrected": corrected_text,
            "spelling_feedback": feedback or self.TEMP_SPELLING_FEEDBACK,
        }

    def analyze_summary(self, text):
        result = self.summarize(text)
        return self.format_summary(result)

    def analyze_evaluation(self, text):
        return self.ai.evaluate(text)

    def analyze_title_recommendation(self, text):
        return self.ai.recommend_title(text)

    def analyze_tone_change(self, text, tone):
        return self.ai.change_tone(text, tone)

    def map_style_runs(self, source_text, corrected_text, style_runs):
        return self.ai.map_style_runs(source_text, corrected_text, style_runs)

    def map_style_runs_by_slot(self, source_text, corrected_text, style_runs):
        return self.ai.map_style_runs_by_slot(source_text, corrected_text, style_runs)

    def check_spelling(self, text):
        return self.ai.correct_spelling(text)

    def summarize(self, text):
        return self.ai.summarize(text)

    def format_spell_check(self, result):
        issues = ""
        corrected = ""

        if isinstance(result, dict):
            issues = str(result.get("issues", "") or "").strip()
            corrected = str(result.get("corrected", "") or "").strip()
        else:
            corrected = str(result or "").strip()

        sections = ["맞춤법 검사 결과:"]
        sections.extend(["", issues or self.TEMP_SPELLING_FEEDBACK])
        sections.extend(["", "맞춤법 수정 결과:", "", self._append_temp_marker(corrected, "spelling")])
        return "\n".join(sections).rstrip()

    def format_summary(self, result):
        summary_text = self._append_temp_marker(result, "summary")
        return f"요약 결과:\n\n{summary_text}"

    def _append_temp_marker(self, text, feature_name):
        value = str(text or "").strip()
        marker = self.TEMP_RESULT_MARKERS.get(feature_name, "")
        if not marker:
            return value
        if value.endswith(marker.strip()):
            return value
        return f"{value}{marker}".strip()

    def extract_spelling_corrected_text(self, raw_result, formatted_result=""):
        if isinstance(raw_result, dict):
            corrected = str(raw_result.get("corrected", "") or "").strip()
            if corrected:
                return corrected
        text = str(formatted_result or raw_result or "").strip()
        if not text:
            return ""

        marker = "맞춤법 수정 결과:"
        if marker in text:
            return text.split(marker, 1)[1].strip()

        lines = [line.rstrip() for line in text.splitlines()]
        non_empty_indices = [index for index, line in enumerate(lines) if line.strip()]
        if not non_empty_indices:
            return ""

        block_start = non_empty_indices[-1]
        while block_start > 0 and lines[block_start - 1].strip():
            block_start -= 1
        return "\n".join(lines[block_start:]).strip()
