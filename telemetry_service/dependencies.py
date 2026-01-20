from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from telemetry_service.auth import decode_access_token
from telemetry_service.db import get_db_connection
from telemetry_service.models import UserPublic

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login",
    auto_error=False,
)
AUTH_COOKIE_NAME = "telemetry_token"


def resolve_user_from_token(token: str) -> UserPublic:
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


def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    return resolve_user_from_token(token)


def get_optional_user(
    token: str | None = Depends(optional_oauth2_scheme),
) -> UserPublic | None:
    if not token:
        return None
    return resolve_user_from_token(token)


def _extract_request_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME)
    return cookie_token.strip() if cookie_token else None


def get_ui_current_user(request: Request) -> UserPublic:
    token = _extract_request_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return get_current_user(token)


def get_ui_optional_user(request: Request) -> UserPublic | None:
    token = _extract_request_token(request)
    if not token:
        return None
    try:
        return get_current_user(token)
    except HTTPException:
        return None


def get_machine_for_user(machine_id: str, current_user: UserPublic) -> dict:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, user_id, machine_name, gpu_info, version, status, last_seen
            FROM machines
            WHERE id = ? AND user_id = ?
            """,
            (machine_id, current_user.id),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Machine not found",
            )
        return {
            "id": row[0],
            "user_id": row[1],
            "machine_name": row[2],
            "gpu_info": row[3],
            "version": row[4],
            "status": row[5],
            "last_seen": row[6],
        }
    finally:
        conn.close()


def get_current_admin_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    current_user = get_current_user(token)
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not an admin user",
        )
    return current_user
