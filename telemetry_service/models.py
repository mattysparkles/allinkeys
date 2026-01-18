from __future__ import annotations

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=150)
    password: str = Field(..., min_length=8)


class UserPublic(BaseModel):
    id: int
    username: str
    created_at: str
    is_admin: bool = False
