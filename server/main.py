import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ai_service import AIService
from auth import (
    create_access_token,
    create_remember_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from database import Base, SessionLocal, engine
from models import (
    AnalysisRequest,
    EvaluationResult,
    SpellingResult,
    SummaryResult,
    TitleResult,
    ToneResult,
    User,
    UserSetting,
)
from schemas import (
    AccountResponse,
    AccountUpdateRequest,
    AccountVerifyRequest,
    CorrectRequest,
    CorrectResponse,
    EvaluationRequest,
    EvaluationReasonResponse,
    EvaluationResponse,
    HistoryRequestResponse,
    LoginRequest,
    SignupRequest,
    StyleMapRequest,
    StyleMapResponse,
    StyleSlotMapRequest,
    StyleSlotMapResponse,
    SummaryRequest,
    SummaryResponse,
    TitleRequest,
    TitleResponse,
    TokenResponse,
    ToneRequest,
    ToneResponse,
    UsageLogCreateRequest,
    UsageLogResponse,
    UserSettingsRequest,
    UserSettingsResponse,
)


SERVER_API_VERSION = "openai-separated-v8"

app = FastAPI(title="AI 문서 보조 서버")
Base.metadata.create_all(bind=engine)

ai_service = None
ai_service_key_fingerprint = None


def ensure_user_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("users")}
    if "display_name" in column_names:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(100)"))


def ensure_evaluation_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("evaluation_results")}
    if "evaluation_reason" in column_names:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE evaluation_results ADD COLUMN evaluation_reason TEXT"))


def migrate_legacy_usage_logs():
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "usage_logs" not in table_names:
        return

    db = SessionLocal()
    try:
        has_new_data = any(
            [
                db.query(SpellingResult).first(),
                db.query(SummaryResult).first(),
                db.query(ToneResult).first(),
                db.query(EvaluationResult).first(),
                db.query(TitleResult).first(),
            ]
        )
        if has_new_data:
            return

        rows = db.execute(
            text(
                """
                SELECT id, user_id, input_text, output_text, feature_type, title, score, tone, spelling_feedback, created_at
                FROM usage_logs
                ORDER BY id ASC
                """
            )
        ).mappings().all()

        for row in rows:
            request_row = AnalysisRequest(
                user_id=row["user_id"],
                input_text=row["input_text"] or "",
                created_at=row["created_at"] or datetime.utcnow(),
            )
            db.add(request_row)
            db.flush()

            feature_type = int(row["feature_type"] or 0)
            created_at = row["created_at"] or datetime.utcnow()
            if feature_type == 1 and row["score"] is not None:
                db.add(
                    EvaluationResult(
                        request_id=request_row.id,
                        score=int(row["score"]),
                        score_text=f"{int(row['score'])}점",
                        created_at=created_at,
                    )
                )
            if feature_type == 1 and row["title"]:
                db.add(
                    TitleResult(
                        request_id=request_row.id,
                        title_text=row["title"],
                        created_at=created_at,
                    )
                )
            if feature_type == 2:
                db.add(
                    SpellingResult(
                        request_id=request_row.id,
                        corrected_text=row["output_text"] or "",
                        spelling_feedback=row["spelling_feedback"] or "",
                        created_at=created_at,
                    )
                )
            if feature_type == 3:
                db.add(
                    SummaryResult(
                        request_id=request_row.id,
                        summary_text=row["output_text"] or "",
                        created_at=created_at,
                    )
                )
            if feature_type == 4:
                db.add(
                    ToneResult(
                        request_id=request_row.id,
                        requested_tone=row["tone"] or "",
                        changed_text=row["output_text"] or "",
                        created_at=created_at,
                    )
                )
        db.commit()
    finally:
        db.close()


ensure_user_columns()
ensure_evaluation_columns()
migrate_legacy_usage_logs()


def get_ai_service() -> AIService:
    global ai_service, ai_service_key_fingerprint
    current_fingerprint = AIService.current_api_key_fingerprint()
    if ai_service is None or ai_service_key_fingerprint != current_fingerprint:
        ai_service = AIService()
        ai_service_key_fingerprint = ai_service.api_key_fingerprint
    return ai_service


def log_ai_request(stage: str, feature: str, **values):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    details = " ".join(f"{key}={value}" for key, value in values.items())
    print(f"[{timestamp}] [openai:{feature}] {stage} {details}".rstrip(), flush=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증 토큰이 없습니다.")

    token = authorization.replace("Bearer ", "").strip()
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

    expire_at = payload.get("exp")
    if expire_at is not None:
        expire_dt = datetime.fromtimestamp(float(expire_at), tz=timezone.utc).astimezone()
        print(f"[auth] token expires at {expire_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="토큰 정보가 올바르지 않습니다.")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return user


def get_or_create_request(db: Session, user_id: int, input_text: str) -> AnalysisRequest:
    request_row = (
        db.query(AnalysisRequest)
        .filter(
            AnalysisRequest.user_id == user_id,
            AnalysisRequest.input_text == input_text,
        )
        .order_by(AnalysisRequest.created_at.desc())
        .first()
    )
    if request_row is None:
        request_row = AnalysisRequest(user_id=user_id, input_text=input_text)
        db.add(request_row)
        db.flush()
    return request_row


def serialize_spelling_result(row: SpellingResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=row.id,
        feature_type=2,
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.corrected_text or "",
        title=None,
        score=None,
        tone=None,
        spelling_feedback=row.spelling_feedback,
        created_at=row.created_at,
    )


def serialize_summary_result(row: SummaryResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=row.id,
        feature_type=3,
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.summary_text or "",
        title=None,
        score=None,
        tone=None,
        spelling_feedback=None,
        created_at=row.created_at,
    )


def serialize_tone_result(row: ToneResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=row.id,
        feature_type=4,
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.changed_text or "",
        title=None,
        score=None,
        tone=row.requested_tone,
        spelling_feedback=None,
        created_at=row.created_at,
    )


def serialize_evaluation_result(row: EvaluationResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=row.id,
        feature_type=1,
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.score_text or "",
        title=None,
        score=row.score,
        tone=None,
        spelling_feedback=None,
        evaluation_reason=row.evaluation_reason,
        created_at=row.created_at,
    )


def serialize_title_result(row: TitleResult) -> UsageLogResponse:
    return UsageLogResponse(
        id=row.id,
        feature_type=1,
        input_text=row.request.input_text if row.request else "",
        request_id=row.request_id,
        output_text=row.title_text or "",
        title=row.title_text or "",
        score=None,
        tone=None,
        spelling_feedback=None,
        created_at=row.created_at,
    )


def serialize_history_request(request_row: AnalysisRequest) -> HistoryRequestResponse:
    spelling_row = (
        max(request_row.spelling_results, key=lambda row: row.created_at)
        if request_row.spelling_results
        else None
    )
    summary_row = (
        max(request_row.summary_results, key=lambda row: row.created_at)
        if request_row.summary_results
        else None
    )
    tone_row = (
        max(request_row.tone_results, key=lambda row: row.created_at)
        if request_row.tone_results
        else None
    )
    evaluation_row = (
        max(request_row.evaluation_results, key=lambda row: row.created_at)
        if request_row.evaluation_results
        else None
    )
    title_row = (
        max(request_row.title_results, key=lambda row: row.created_at)
        if request_row.title_results
        else None
    )
    return HistoryRequestResponse(
        request_id=request_row.id,
        input_text=request_row.input_text,
        created_at=request_row.created_at,
        spelling=(
            {
                "corrected_text": spelling_row.corrected_text,
                "spelling_feedback": spelling_row.spelling_feedback,
                "created_at": spelling_row.created_at.isoformat(),
            }
            if spelling_row
            else None
        ),
        summary=(
            {
                "summary_text": summary_row.summary_text,
                "created_at": summary_row.created_at.isoformat(),
            }
            if summary_row
            else None
        ),
        tone=(
            {
                "requested_tone": tone_row.requested_tone,
                "changed_text": tone_row.changed_text,
                "created_at": tone_row.created_at.isoformat(),
            }
            if tone_row
            else None
        ),
        evaluation=(
            {
                "score": evaluation_row.score,
                "score_text": evaluation_row.score_text,
                "evaluation_reason": evaluation_row.evaluation_reason,
                "created_at": evaluation_row.created_at.isoformat(),
            }
            if evaluation_row
            else None
        ),
        title=(
            {
                "title_text": title_row.title_text,
                "created_at": title_row.created_at.isoformat(),
            }
            if title_row
            else None
        ),
    )


@app.get("/")
def root():
    return {"message": "server is running", "api_version": SERVER_API_VERSION}


@app.get("/server-info")
def server_info():
    return {
        "name": "checkword-local-server",
        "api_version": SERVER_API_VERSION,
        "openai_routes": [
            "/correct",
            "/evaluate",
            "/evaluate-reason",
            "/title",
            "/summary",
            "/tone",
            "/style-map",
            "/style-map-slots",
            "/history/requests",
        ],
        "openai_key_fingerprint": AIService.current_api_key_fingerprint(),
    }


@app.post("/signup")
def signup(data: SignupRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 사용자입니다.")

    user = User(username=data.username, password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"message": "회원가입 완료"}


@app.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 잘못되었습니다.")

    token_factory = create_remember_access_token if data.remember_me else create_access_token
    token = token_factory({"sub": user.username})
    return TokenResponse(access_token=token)


@app.post("/account/verify")
def verify_account(data: AccountVerifyRequest, current_user: User = Depends(get_current_user)):
    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="비밀번호가 잘못되었습니다.")
    return {"verified": True}


@app.get("/account", response_model=AccountResponse)
def get_account(current_user: User = Depends(get_current_user)):
    return AccountResponse(username=current_user.username, display_name=current_user.display_name)


@app.put("/account", response_model=AccountResponse)
def update_account(
    data: AccountUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_username = (data.username or current_user.username).strip()
    access_token = None

    if not new_username:
        raise HTTPException(status_code=400, detail="아이디를 입력해 주세요.")

    if new_username != current_user.username:
        existing_user = db.query(User).filter(User.username == new_username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")
        current_user.username = new_username
        access_token = create_access_token({"sub": current_user.username})

    if data.display_name is not None:
        current_user.display_name = data.display_name.strip() or None
    if data.password:
        if len(data.password) < 4:
            raise HTTPException(status_code=400, detail="비밀번호는 4자 이상으로 입력해 주세요.")
        current_user.password_hash = hash_password(data.password)

    db.commit()
    db.refresh(current_user)
    return AccountResponse(
        username=current_user.username,
        display_name=current_user.display_name,
        access_token=access_token,
    )


@app.delete("/account")
def delete_account(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    request_ids = [row[0] for row in db.query(AnalysisRequest.id).filter(AnalysisRequest.user_id == current_user.id).all()]
    if request_ids:
        db.query(SpellingResult).filter(SpellingResult.request_id.in_(request_ids)).delete(synchronize_session=False)
        db.query(SummaryResult).filter(SummaryResult.request_id.in_(request_ids)).delete(synchronize_session=False)
        db.query(ToneResult).filter(ToneResult.request_id.in_(request_ids)).delete(synchronize_session=False)
        db.query(EvaluationResult).filter(EvaluationResult.request_id.in_(request_ids)).delete(synchronize_session=False)
        db.query(TitleResult).filter(TitleResult.request_id.in_(request_ids)).delete(synchronize_session=False)
    db.query(AnalysisRequest).filter(AnalysisRequest.user_id == current_user.id).delete()
    db.query(UserSetting).filter(UserSetting.user_id == current_user.id).delete()
    db.delete(current_user)
    db.commit()
    return {"message": "계정을 삭제했습니다."}


@app.post("/correct", response_model=CorrectResponse)
def correct_text(data: CorrectRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="교정할 텍스트가 비어 있습니다.")

    started_at = time.monotonic()
    log_ai_request("start", "correct", chars=len(data.text), lines=data.text.count("\n") + 1)
    try:
        service = get_ai_service()
        result = service.correct_spelling(data.text)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "correct", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI 맞춤법 요청 실패: {exc}") from exc

    corrected_text = str(result.get("corrected_text", "") or "")
    spelling_feedback = str(result.get("spelling_feedback", "") or "")
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "correct", model=service.model, elapsed_ms=elapsed_ms, output_chars=len(corrected_text))
    return CorrectResponse(corrected_text=corrected_text, spelling_feedback=spelling_feedback)


@app.post("/evaluate", response_model=EvaluationResponse)
def evaluate_text(data: EvaluationRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="평가할 텍스트가 비어 있습니다.")

    started_at = time.monotonic()
    log_ai_request("start", "evaluate", chars=len(data.text), lines=data.text.count("\n") + 1)
    try:
        service = get_ai_service()
        score_text = service.evaluate_text(data.text)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "evaluate", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI 평가 요청 실패: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "evaluate", model=service.model, elapsed_ms=elapsed_ms, output_chars=len(score_text))
    return EvaluationResponse(score_text=score_text)


@app.post("/evaluate-reason", response_model=EvaluationReasonResponse)
def evaluate_reason(data: EvaluationRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="평가 이유를 만들 텍스트가 비어 있습니다.")

    started_at = time.monotonic()
    log_ai_request("start", "evaluate_reason", chars=len(data.text), lines=data.text.count("\n") + 1)
    try:
        service = get_ai_service()
        reason = service.evaluate_reason(data.text, data.score_text or "")
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "evaluate_reason", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI 평가 이유 요청 실패: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "evaluate_reason", model=service.model, elapsed_ms=elapsed_ms, output_chars=len(reason))
    return EvaluationReasonResponse(evaluation_reason=reason)


@app.post("/title", response_model=TitleResponse)
def recommend_title(data: TitleRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="제목을 추천할 텍스트가 비어 있습니다.")

    started_at = time.monotonic()
    log_ai_request("start", "title", chars=len(data.text), lines=data.text.count("\n") + 1)
    try:
        service = get_ai_service()
        title_text = service.recommend_title(data.text)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "title", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI 제목 추천 요청 실패: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "title", model=service.model, elapsed_ms=elapsed_ms, output_chars=len(title_text))
    return TitleResponse(title_text=title_text)


@app.post("/summary", response_model=SummaryResponse)
def summarize_text(data: SummaryRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="요약할 텍스트가 비어 있습니다.")

    started_at = time.monotonic()
    log_ai_request(
        "start",
        "summary",
        chars=len(data.text),
        lines=data.text.count("\n") + 1,
        style=data.style,
    )
    try:
        service = get_ai_service()
        summary_text = service.summarize_text(data.text, data.style)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "summary", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI 요약 요청 실패: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "summary", model=service.model, elapsed_ms=elapsed_ms, output_chars=len(summary_text))
    return SummaryResponse(summary_text=summary_text)


@app.post("/tone", response_model=ToneResponse)
def change_tone(data: ToneRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="문체/말투를 바꿀 텍스트가 비어 있습니다.")
    if not data.tone.strip():
        raise HTTPException(status_code=400, detail="변경할 문체/말투가 비어 있습니다.")

    started_at = time.monotonic()
    log_ai_request("start", "tone", chars=len(data.text), lines=data.text.count("\n") + 1, tone=repr(data.tone[:60]))
    try:
        service = get_ai_service()
        changed_text = service.change_tone(data.text, data.tone)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "tone", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI 문체/말투 요청 실패: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "tone", model=service.model, elapsed_ms=elapsed_ms, output_chars=len(changed_text))
    return ToneResponse(changed_text=changed_text)


@app.post("/style-map", response_model=StyleMapResponse)
def style_map(data: StyleMapRequest):
    if not data.source_text.strip() or not data.corrected_text.strip():
        raise HTTPException(status_code=400, detail="style-map 입력 텍스트가 비어 있습니다.")
    if not data.style_runs:
        return StyleMapResponse(mapped_runs=[])

    started_at = time.monotonic()
    log_ai_request(
        "start",
        "style_map",
        source_chars=len(data.source_text),
        corrected_chars=len(data.corrected_text),
        runs=len(data.style_runs),
    )
    try:
        service = get_ai_service()
        mapped = service.map_style_runs(
            source_text=data.source_text,
            corrected_text=data.corrected_text,
            style_runs=[{"text": run.text} for run in data.style_runs],
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "style_map", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI style-map 요청 실패: {exc}") from exc

    sanitized = []
    for run in mapped or []:
        try:
            run_text = run.get("text")
        except Exception:
            continue
        if isinstance(run_text, str):
            sanitized.append({"text": run_text})

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "style_map", elapsed_ms=elapsed_ms, mapped_runs=len(sanitized))
    return StyleMapResponse(mapped_runs=sanitized)


@app.post("/style-map-slots", response_model=StyleSlotMapResponse)
def style_map_slots(data: StyleSlotMapRequest):
    if not data.source_text.strip() or not data.corrected_text.strip():
        raise HTTPException(status_code=400, detail="style-map-slots 입력 텍스트가 비어 있습니다.")
    if not data.style_runs:
        return StyleSlotMapResponse(mapped_runs=[])

    started_at = time.monotonic()
    log_ai_request(
        "start",
        "style_map_slots",
        source_chars=len(data.source_text),
        corrected_chars=len(data.corrected_text),
        runs=len(data.style_runs),
    )
    try:
        service = get_ai_service()
        mapped = service.map_style_runs_by_slot(
            source_text=data.source_text,
            corrected_text=data.corrected_text,
            style_runs=[{"text": run.text} for run in data.style_runs],
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        log_ai_request("fail", "style_map_slots", elapsed_ms=elapsed_ms, error=repr(exc))
        raise HTTPException(status_code=502, detail=f"OpenAI style-map-slots 요청 실패: {exc}") from exc

    sanitized = []
    limit = len(data.style_runs)
    for run in (mapped or [])[:limit]:
        try:
            run_text = run.get("text")
        except Exception:
            run_text = ""
        sanitized.append({"text": str(run_text or "")})
    while len(sanitized) < limit:
        sanitized.append({"text": ""})

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    log_ai_request("done", "style_map_slots", elapsed_ms=elapsed_ms, mapped_runs=len(sanitized))
    return StyleSlotMapResponse(mapped_runs=sanitized)


@app.post("/logs", response_model=UsageLogResponse)
def create_usage_log(
    data: UsageLogCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.request_id is None and not data.input_text.strip():
        raise HTTPException(status_code=400, detail="input_text is required when request_id is missing.")
    if data.score is not None and not 0 <= int(data.score) <= 100:
        raise HTTPException(status_code=400, detail="score must be between 0 and 100.")

    request_row = None
    if data.request_id is not None:
        request_row = (
            db.query(AnalysisRequest)
            .filter(
                AnalysisRequest.id == data.request_id,
                AnalysisRequest.user_id == current_user.id,
            )
            .first()
        )
        if request_row is None:
            raise HTTPException(status_code=404, detail="request_id not found.")

    if request_row is None:
        request_row = get_or_create_request(db, current_user.id, data.input_text)

    if data.feature_type == 1 and data.title:
        row = (
            db.query(TitleResult)
            .filter(TitleResult.request_id == request_row.id)
            .order_by(TitleResult.created_at.desc())
            .first()
        )
        if row is None:
            row = TitleResult(request_id=request_row.id, title_text=data.title)
            db.add(row)
        else:
            row.title_text = data.title
            row.created_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return serialize_title_result(row)

    if data.feature_type == 1:
        row = (
            db.query(EvaluationResult)
            .filter(EvaluationResult.request_id == request_row.id)
            .order_by(EvaluationResult.created_at.desc())
            .first()
        )
        if row is None:
            row = EvaluationResult(
                request_id=request_row.id,
                score=int(data.score) if data.score is not None else None,
                score_text=data.output_text or (f"{int(data.score)}?" if data.score is not None else ""),
                evaluation_reason=data.evaluation_reason,
            )
            db.add(row)
        else:
            row.score = int(data.score) if data.score is not None else row.score
            row.score_text = data.output_text or (f"{int(data.score)}?" if data.score is not None else row.score_text)
            row.evaluation_reason = data.evaluation_reason if data.evaluation_reason is not None else row.evaluation_reason
            row.created_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return serialize_evaluation_result(row)

    if data.feature_type == 2:
        row = (
            db.query(SpellingResult)
            .filter(SpellingResult.request_id == request_row.id)
            .order_by(SpellingResult.created_at.desc())
            .first()
        )
        if row is None:
            row = SpellingResult(
                request_id=request_row.id,
                corrected_text=data.output_text or "",
                spelling_feedback=data.spelling_feedback,
            )
            db.add(row)
        else:
            row.corrected_text = data.output_text or ""
            row.spelling_feedback = data.spelling_feedback
            row.created_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return serialize_spelling_result(row)

    if data.feature_type == 3:
        row = (
            db.query(SummaryResult)
            .filter(SummaryResult.request_id == request_row.id)
            .order_by(SummaryResult.created_at.desc())
            .first()
        )
        if row is None:
            row = SummaryResult(request_id=request_row.id, summary_text=data.output_text or "")
            db.add(row)
        else:
            row.summary_text = data.output_text or ""
            row.created_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return serialize_summary_result(row)

    if data.feature_type == 4:
        row = (
            db.query(ToneResult)
            .filter(ToneResult.request_id == request_row.id)
            .order_by(ToneResult.created_at.desc())
            .first()
        )
        if row is None:
            row = ToneResult(
                request_id=request_row.id,
                requested_tone=data.tone,
                changed_text=data.output_text or "",
            )
            db.add(row)
        else:
            row.requested_tone = data.tone
            row.changed_text = data.output_text or ""
            row.created_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return serialize_tone_result(row)

    raise HTTPException(status_code=400, detail="Unsupported feature_type.")


@app.get("/logs", response_model=list[UsageLogResponse])
def list_usage_logs(
    feature_type: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if feature_type == 1:
        rows = []
        rows.extend(
            serialize_evaluation_result(row)
            for row in db.query(EvaluationResult)
            .join(AnalysisRequest, EvaluationResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .order_by(EvaluationResult.created_at.desc())
            .all()
        )
        rows.extend(
            serialize_title_result(row)
            for row in db.query(TitleResult)
            .join(AnalysisRequest, TitleResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .order_by(TitleResult.created_at.desc())
            .all()
        )
        return sorted(rows, key=lambda item: item.created_at, reverse=True)

    if feature_type == 2:
        return [
            serialize_spelling_result(row)
            for row in db.query(SpellingResult)
            .join(AnalysisRequest, SpellingResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .order_by(SpellingResult.created_at.desc())
            .all()
        ]

    if feature_type == 3:
        return [
            serialize_summary_result(row)
            for row in db.query(SummaryResult)
            .join(AnalysisRequest, SummaryResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .order_by(SummaryResult.created_at.desc())
            .all()
        ]

    if feature_type == 4:
        return [
            serialize_tone_result(row)
            for row in db.query(ToneResult)
            .join(AnalysisRequest, ToneResult.request_id == AnalysisRequest.id)
            .filter(AnalysisRequest.user_id == current_user.id)
            .order_by(ToneResult.created_at.desc())
            .all()
        ]

    rows = []
    rows.extend(
        serialize_spelling_result(row)
        for row in db.query(SpellingResult)
        .join(AnalysisRequest, SpellingResult.request_id == AnalysisRequest.id)
        .filter(AnalysisRequest.user_id == current_user.id)
        .all()
    )
    rows.extend(
        serialize_summary_result(row)
        for row in db.query(SummaryResult)
        .join(AnalysisRequest, SummaryResult.request_id == AnalysisRequest.id)
        .filter(AnalysisRequest.user_id == current_user.id)
        .all()
    )
    rows.extend(
        serialize_tone_result(row)
        for row in db.query(ToneResult)
        .join(AnalysisRequest, ToneResult.request_id == AnalysisRequest.id)
        .filter(AnalysisRequest.user_id == current_user.id)
        .all()
    )
    rows.extend(
        serialize_evaluation_result(row)
        for row in db.query(EvaluationResult)
        .join(AnalysisRequest, EvaluationResult.request_id == AnalysisRequest.id)
        .filter(AnalysisRequest.user_id == current_user.id)
        .all()
    )
    rows.extend(
        serialize_title_result(row)
        for row in db.query(TitleResult)
        .join(AnalysisRequest, TitleResult.request_id == AnalysisRequest.id)
        .filter(AnalysisRequest.user_id == current_user.id)
        .all()
    )
    return sorted(rows, key=lambda item: item.created_at, reverse=True)


@app.get("/history/requests", response_model=list[HistoryRequestResponse])
def list_history_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requests_rows = (
        db.query(AnalysisRequest)
        .filter(AnalysisRequest.user_id == current_user.id)
        .order_by(AnalysisRequest.created_at.desc())
        .all()
    )
    return [serialize_history_request(row) for row in requests_rows]


@app.delete("/history/requests/{request_id}")
def delete_history_request(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_row = (
        db.query(AnalysisRequest)
        .filter(AnalysisRequest.id == request_id, AnalysisRequest.user_id == current_user.id)
        .first()
    )
    if not request_row:
        raise HTTPException(status_code=404, detail="삭제할 기록을 찾을 수 없습니다.")
    db.delete(request_row)
    db.commit()
    return {"success": True}


@app.delete("/history/requests")
def delete_all_history_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_rows = (
        db.query(AnalysisRequest)
        .filter(AnalysisRequest.user_id == current_user.id)
        .all()
    )
    for request_row in request_rows:
        db.delete(request_row)
    db.commit()
    return {"success": True, "deleted_count": len(request_rows)}


@app.get("/settings", response_model=UserSettingsResponse)
def get_user_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = db.query(UserSetting).filter(UserSetting.user_id == current_user.id).first()
    if not settings:
        return UserSettingsResponse(has_settings=False)
    return UserSettingsResponse(
        has_settings=True,
        default_dark_mode=settings.default_dark_mode,
        history_enabled=settings.history_enabled,
        input_mode=settings.input_mode,
        replace_mode=settings.replace_mode,
        updated_at=settings.updated_at,
    )


@app.put("/settings", response_model=UserSettingsResponse)
def update_user_settings(
    data: UserSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    input_mode = data.input_mode if data.input_mode in {"clipboard", "realtime", "selection"} else "realtime"
    replace_mode = bool(data.replace_mode) and input_mode == "realtime"

    settings = db.query(UserSetting).filter(UserSetting.user_id == current_user.id).first()
    if not settings:
        settings = UserSetting(user_id=current_user.id)
        db.add(settings)

    settings.default_dark_mode = bool(data.default_dark_mode)
    settings.history_enabled = bool(data.history_enabled)
    settings.input_mode = input_mode
    settings.replace_mode = replace_mode
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)
    return UserSettingsResponse(
        has_settings=True,
        default_dark_mode=settings.default_dark_mode,
        history_enabled=settings.history_enabled,
        input_mode=settings.input_mode,
        replace_mode=settings.replace_mode,
        updated_at=settings.updated_at,
    )
