from datetime import datetime

from pydantic import BaseModel


class SignupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


class AccountVerifyRequest(BaseModel):
    password: str


class AccountUpdateRequest(BaseModel):
    display_name: str | None = None
    username: str | None = None
    password: str | None = None


class AccountResponse(BaseModel):
    username: str
    display_name: str | None = None
    access_token: str | None = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CorrectRequest(BaseModel):
    text: str


class CorrectResponse(BaseModel):
    corrected_text: str
    spelling_feedback: str | None = None


class SummaryRequest(BaseModel):
    text: str


class SummaryResponse(BaseModel):
    summary_text: str


class EvaluationRequest(BaseModel):
    text: str


class EvaluationResponse(BaseModel):
    score_text: str


class TitleRequest(BaseModel):
    text: str


class TitleResponse(BaseModel):
    title_text: str


class ToneRequest(BaseModel):
    text: str
    tone: str


class ToneResponse(BaseModel):
    changed_text: str


class StyleRun(BaseModel):
    start: int
    end: int
    style: dict
    text: str | None = None


class StyleTextRunRequest(BaseModel):
    text: str


class StyleTextRunResponse(BaseModel):
    text: str


class StyleMapRequest(BaseModel):
    source_text: str
    corrected_text: str
    style_runs: list[StyleTextRunRequest]


class StyleMapResponse(BaseModel):
    mapped_runs: list[StyleTextRunResponse]


class StyleSlotMapRequest(BaseModel):
    source_text: str
    corrected_text: str
    style_runs: list[StyleTextRunRequest]


class StyleSlotMapResponse(BaseModel):
    mapped_runs: list[StyleTextRunResponse]


class UsageLogCreateRequest(BaseModel):
    feature_type: int
    input_text: str
    request_id: int | None = None
    output_text: str = ""
    title: str | None = None
    score: int | None = None
    tone: str | None = None
    spelling_feedback: str | None = None


class UsageLogResponse(BaseModel):
    id: int
    feature_type: int
    input_text: str
    request_id: int | None = None
    output_text: str
    title: str | None = None
    score: int | None = None
    tone: str | None = None
    spelling_feedback: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class HistoryRequestResponse(BaseModel):
    request_id: int
    input_text: str
    created_at: datetime
    spelling: dict | None = None
    summary: dict | None = None
    tone: dict | None = None
    evaluation: dict | None = None
    title: dict | None = None


class UserSettingsRequest(BaseModel):
    default_dark_mode: bool = False
    history_enabled: bool = False
    input_mode: str = "clipboard"
    replace_mode: bool = False


class UserSettingsResponse(UserSettingsRequest):
    has_settings: bool = True
    updated_at: datetime | None = None

    class Config:
        from_attributes = True
