from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from telemetry_service.auth import decode_access_token
from telemetry_service.db import get_db_connection
from telemetry_service.models import UserPublic

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, username, created_at, is_admin
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
            )
        return UserPublic(
            id=row[0],
            username=row[1],
            created_at=row[2],
            is_admin=bool(row[3]),
        )
    finally:
        conn.close()
