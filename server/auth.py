import os
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REMEMBER_ACCESS_TOKEN_EXPIRE_DAYS = 3650
PBKDF2_ITERATIONS = 260_000
PBKDF2_PREFIX = "pbkdf2_sha256"

if not SECRET_KEY:
    raise ValueError("SECRET_KEY 환경변수가 설정되지 않았습니다.")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"{PBKDF2_PREFIX}${PBKDF2_ITERATIONS}${salt_text}${digest_text}"


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    if password_hash.startswith(f"{PBKDF2_PREFIX}$"):
        return _verify_pbkdf2_password(plain_password, password_hash)
    if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        return _verify_legacy_bcrypt_password(plain_password, password_hash)
    return False


def _verify_pbkdf2_password(plain_password: str, password_hash: str) -> bool:
    try:
        _scheme, iterations, salt_text, digest_text = password_hash.split("$", 3)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _verify_legacy_bcrypt_password(plain_password: str, password_hash: str) -> bool:
    try:
        import bcrypt

        password_bytes = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_remember_access_token(data: dict) -> str:
    return create_access_token(
        data,
        expires_delta=timedelta(days=REMEMBER_ACCESS_TOKEN_EXPIRE_DAYS),
    )


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
