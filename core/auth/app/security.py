import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

JWT_SECRET       = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

VALID_ROLES = {"admin", "constructor", "operator"}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(username: str, role: str, display_name: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub":          username,
        "role":         role,
        "display_name": display_name,
        "exp":          expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    # raises jwt.InvalidTokenError (incl. ExpiredSignatureError) on failure
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
