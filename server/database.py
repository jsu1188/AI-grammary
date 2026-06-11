import os
from pathlib import Path

from sqlalchemy import create_engine
from dotenv import load_dotenv
from sqlalchemy.orm import declarative_base, sessionmaker


load_dotenv(Path(__file__).resolve().parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")


def mask_database_url(url: str) -> str:
    if "://" not in url or "@" not in url:
        return url

    scheme, rest = url.split("://", 1)
    credentials, host_part = rest.split("@", 1)
    if ":" not in credentials:
        return url

    username, _password = credentials.split(":", 1)
    return f"{scheme}://{username}:***@{host_part}"


print(f"[database] using {mask_database_url(DATABASE_URL)}")

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs) #이건 db 객체가 아닌 db 연결용 api

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()  # 모든 table 클래스의 부모
