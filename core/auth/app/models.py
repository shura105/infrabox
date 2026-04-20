from typing import Optional, Any
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    role:         str = ""
    display_name: str = ""


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


class PermissionsUpdate(BaseModel):
    pages:   Optional[list[str]] = None   # slugs; None = no restriction
    objects: Optional[list[str]] = None   # object names; None or ["*"] = all


class UserPublic(BaseModel):
    username:     str
    role:         str
    display_name: str
    active:       str
    permissions:  Optional[dict[str, Any]] = None
