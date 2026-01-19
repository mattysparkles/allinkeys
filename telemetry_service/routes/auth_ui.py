from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse

from fastapi import APIRouter, Form, Request, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config.telemetry import TOKEN_EXPIRY
from telemetry_service.auth import create_access_token, hash_password, verify_password
from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import resolve_user_from_token

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter()

SESSION_COOKIE = "telemetry_session"
DEFAULT_REDIRECT = "/dashboard/machines"


def _safe_next_path(next_path: Optional[str]) -> str:
    if not next_path:
        return DEFAULT_REDIRECT
    if not next_path.startswith("/") or next_path.startswith("//"):
        return DEFAULT_REDIRECT
    return next_path


def _build_redirect_path(next_path: Optional[str], code: Optional[str]) -> str:
    safe_path = _safe_next_path(next_path)
    if not code:
        return safe_path
    parsed = urlparse(safe_path)
    query = parsed.query
    if query:
        query += "&"
    query += urlencode({"code": code})
    return urlunparse(parsed._replace(query=query))


def _get_authenticated_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return resolve_user_from_token(token)
    except HTTPException:
        return None


def _set_session_cookie(response: RedirectResponse, token: str, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=TOKEN_EXPIRY * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: Optional[str] = None, code: Optional[str] = None):
    current_user = _get_authenticated_user(request)
    if current_user:
        redirect_path = _build_redirect_path(next, code)
        return RedirectResponse(url=redirect_path, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "next": _safe_next_path(next),
            "code": code,
        },
    )


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: Optional[str] = Form(None),
    code: Optional[str] = Form(None),
):
    username = username.strip()
    if not username or not password:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Enter both username and password.",
                "username": username,
                "next": _safe_next_path(next),
                "code": code,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT username, password_hash
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
        if not row or not verify_password(password, row[1]):
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Incorrect username or password.",
                    "username": username,
                    "next": _safe_next_path(next),
                    "code": code,
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
    finally:
        conn.close()
    token = create_access_token(subject=username)
    redirect_path = _build_redirect_path(next, code)
    response = RedirectResponse(
        url=redirect_path, status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(response, token, request)
    return response


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_page(request: Request, next: Optional[str] = None, code: Optional[str] = None):
    current_user = _get_authenticated_user(request)
    if current_user:
        redirect_path = _build_redirect_path(next, code)
        return RedirectResponse(url=redirect_path, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "next": _safe_next_path(next),
            "code": code,
        },
    )


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: Optional[str] = Form(None),
    code: Optional[str] = Form(None),
):
    username = username.strip()
    errors = []
    if len(username) < 3 or len(username) > 150:
        errors.append("Username must be 3-150 characters long.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    if errors:
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error": " ".join(errors),
                "username": username,
                "next": _safe_next_path(next),
                "code": code,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    conn = get_db_connection()
    try:
        password_hash = hash_password(password)
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            return templates.TemplateResponse(
                "signup.html",
                {
                    "request": request,
                    "error": "Username already exists.",
                    "username": username,
                    "next": _safe_next_path(next),
                    "code": code,
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    finally:
        conn.close()
    token = create_access_token(subject=username)
    redirect_path = _build_redirect_path(next, code)
    response = RedirectResponse(
        url=redirect_path, status_code=status.HTTP_303_SEE_OTHER
    )
    _set_session_cookie(response, token, request)
    return response


@router.post("/logout", include_in_schema=False)
def logout(request: Request, next: Optional[str] = Form(None)):
    redirect_path = _safe_next_path(next)
    response = RedirectResponse(
        url=redirect_path, status_code=status.HTTP_303_SEE_OTHER
    )
    response.delete_cookie(SESSION_COOKIE)
    return response
