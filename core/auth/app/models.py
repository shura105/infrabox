from typing import Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    username:     str
    password:     str
    role:         str   # admin | constructor | operator
    display_name: str


class UserUpdate(BaseModel):
    password:     Optional[str] = None
    role:         Optional[str] = None
    display_name: Optional[str] = None
    active:       Optional[str] = None  # "1" | "0"


class UserPublic(BaseModel):
    username:     str
    role:         str
    display_name: str
    active:       str
