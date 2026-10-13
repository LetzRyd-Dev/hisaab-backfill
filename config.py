import os
import urllib.parse
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Load .env file only if locally present in hisaab-backfill
local_env = Path(__file__).resolve().parent / ".env"
if local_env.exists():
    with open(local_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

DB_HOST = os.getenv("DB_HOST", "35.200.196.113")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "8S5]U3@L^Xz)\\FH}")

_encoded_pass = urllib.parse.quote_plus(DB_PASS)
_encoded_user = urllib.parse.quote_plus(DB_USER)

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    f"postgresql://{_encoded_user}:{_encoded_pass}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
