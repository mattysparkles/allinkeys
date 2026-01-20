from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from telemetry_service.auth import create_access_token, hash_password, verify_password
from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import AUTH_COOKIE_NAME, get_ui_optional_user
from telemetry_service.models import UserPublic

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter()

DEFAULT_DASHBOARD_NEXT = "/dashboard/machines"


def _sanitize_next(next_path: str | None, default: str = DEFAULT_DASHBOARD_NEXT) -> str:
    if not next_path:
        return default
    if not next_path.startswith("/") or next_path.startswith("//"):
        return default
    return next_path


def _with_query(path: str, params: dict[str, str]) -> str:
    parts = urlparse(path)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        if value:
            query[key] = value
    new_query = urlencode(query)
    return urlunparse(("", "", parts.path, parts.params, new_query, parts.fragment))


def _next_with_code(next_path: str, code: str | None) -> str:
    safe_next = _sanitize_next(next_path, default=DEFAULT_DASHBOARD_NEXT)
    return _with_query(safe_next, {"code": (code or "").strip().upper()})


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https"


@router.api_route(
    "/",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
def landing_page(
    request: Request,
    current_user: UserPublic | None = Depends(get_ui_optional_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "landing.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.api_route(
    "/login",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
def login_page(
    request: Request,
    next: str | None = None,
    code: str | None = None,
    current_user: UserPublic | None = Depends(get_ui_optional_user),
) -> HTMLResponse:
    safe_next = _sanitize_next(next, default=DEFAULT_DASHBOARD_NEXT)
    if current_user is not None:
        return RedirectResponse(
            _next_with_code(safe_next, code),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "next": safe_next,
            "code": (code or "").strip().upper(),
            "current_user": current_user,
            "signup_url": _with_query("/signup", {"next": safe_next, "code": code or ""}),
        },
    )


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard/machines"),
    code: str = Form(""),
) -> HTMLResponse:
    safe_next = _sanitize_next(next, default=DEFAULT_DASHBOARD_NEXT)
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT username, password_hash
            FROM users
            WHERE username = ?
            """,
            (username.strip(),),
        ).fetchone()
    finally:
        conn.close()
    try:
        valid = bool(row and verify_password(password, row[1]))
    except Exception:
        valid = False
    if not valid:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password.",
                "next": safe_next,
                "code": code.strip().upper(),
                "current_user": None,
                "signup_url": _with_query("/signup", {"next": safe_next, "code": code}),
            },
        )
    token = create_access_token(subject=row[0])
    response = RedirectResponse(
        _next_with_code(safe_next, code),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(request),
    )
    return response


@router.api_route(
    "/signup",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    include_in_schema=False,
)
def signup_page(
    request: Request,
    next: str | None = None,
    code: str | None = None,
    current_user: UserPublic | None = Depends(get_ui_optional_user),
) -> HTMLResponse:
    safe_next = _sanitize_next(next, default=DEFAULT_DASHBOARD_NEXT)
    if current_user is not None:
        return RedirectResponse(
            _next_with_code(safe_next, code),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "error": None,
            "next": safe_next,
            "code": (code or "").strip().upper(),
            "current_user": current_user,
            "login_url": _with_query("/login", {"next": safe_next, "code": code or ""}),
        },
    )


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard/machines"),
    code: str = Form(""),
) -> HTMLResponse:
    safe_next = _sanitize_next(next, default=DEFAULT_DASHBOARD_NEXT)
    normalized_username = username.strip()
    if not normalized_username:
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error": "Username is required.",
                "next": safe_next,
                "code": code.strip().upper(),
                "current_user": None,
                "login_url": _with_query("/login", {"next": safe_next, "code": code}),
            },
        )
    if len(password.encode("utf-8")) > 72:
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error": "Password must be 72 bytes or fewer.",
                "next": safe_next,
                "code": code.strip().upper(),
                "current_user": None,
                "login_url": _with_query("/login", {"next": safe_next, "code": code}),
            },
        )
    conn = get_db_connection()
    try:
        try:
            password_hash = hash_password(password)
        except Exception:
            return templates.TemplateResponse(
                "signup.html",
                {
                    "request": request,
                    "error": "Unable to process password. Try a shorter one.",
                    "next": safe_next,
                    "code": code.strip().upper(),
                    "current_user": None,
                    "login_url": _with_query(
                        "/login", {"next": safe_next, "code": code}
                    ),
                },
            )
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (normalized_username, password_hash),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return templates.TemplateResponse(
                "signup.html",
                {
                    "request": request,
                    "error": "Username already exists.",
                    "next": safe_next,
                    "code": code.strip().upper(),
                    "current_user": None,
                    "login_url": _with_query("/login", {"next": safe_next, "code": code}),
                },
            )
    finally:
        conn.close()
    token = create_access_token(subject=normalized_username)
    response = RedirectResponse(
        _next_with_code(safe_next, code),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(request),
    )
    return response


@router.post("/logout", response_class=HTMLResponse, include_in_schema=False)
def logout_action(
    request: Request,
    next: str = Form("/login"),
) -> HTMLResponse:
    safe_next = _sanitize_next(next, default="/login")
    response = RedirectResponse(
        safe_next,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response
