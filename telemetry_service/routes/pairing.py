from __future__ import annotations

import sqlite3
import string
import logging
from datetime import datetime
from pathlib import Path
from secrets import choice
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from telemetry_service.auth import create_access_token, verify_password
from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import get_ui_current_user, get_ui_optional_user
from telemetry_service.models import (
    PairClaimRequest,
    PairClaimResponse,
    PairInitResponse,
    PairStatusResponse,
    UserPublic,
)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(prefix="/v1/pair", tags=["Pairing"])
ui_router = APIRouter()
logger = logging.getLogger("telemetry")


def _generate_pair_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(choice(alphabet) for _ in range(length))


@router.post(
    "/init",
    response_model=PairInitResponse,
    summary="Initialize a pairing request.",
)
def init_pairing(request: Request) -> PairInitResponse:
    conn = get_db_connection()
    pair_code = None
    try:
        for _ in range(5):
            candidate = _generate_pair_code()
            try:
                conn.execute(
                    """
                    INSERT INTO pairing_requests (pair_code, status)
                    VALUES (?, 'pending')
                    """,
                    (candidate,),
                )
                conn.commit()
                pair_code = candidate
                break
            except sqlite3.IntegrityError:
                continue
    finally:
        conn.close()
    if not pair_code:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to allocate pair code",
        )
    pair_url = str(request.base_url).rstrip("/") + "/pair"
    return PairInitResponse(
        pair_code=pair_code,
        pair_url=pair_url,
        poll_interval_seconds=3,
    )


@router.get(
    "/status",
    response_model=PairStatusResponse,
    summary="Check pairing status.",
)
def pairing_status(pair_code: str) -> PairStatusResponse:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT status, token
            FROM pairing_requests
            WHERE pair_code = ?
            """,
            (pair_code,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pair code not found",
        )
    status_value, token = row
    return PairStatusResponse(
        status=str(status_value),
        token=token if status_value == "approved" else None,
    )


@router.post(
    "/claim",
    response_model=PairClaimResponse,
    summary="Claim a pairing request with user credentials.",
)
def claim_pairing(payload: PairClaimRequest) -> PairClaimResponse:
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, status
            FROM pairing_requests
            WHERE pair_code = ?
            """,
            (payload.pair_code,),
        ).fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pair code not found",
            )
        pair_id, status_value = row
        if status_value != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pair code already claimed",
            )
        user_row = conn.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE username = ?
            """,
            (payload.username,),
        ).fetchone()
        if not user_row or not verify_password(payload.password, user_row[1]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )
        user_id = int(user_row[0])
        token = create_access_token(subject=payload.username)
        conn.execute(
            """
            UPDATE pairing_requests
            SET status = 'approved',
                user_id = ?,
                token = ?,
                claimed_at = ?
            WHERE id = ?
            """,
            (user_id, token, datetime.utcnow().isoformat() + "Z", pair_id),
        )
        conn.commit()
    finally:
        conn.close()
    return PairClaimResponse(status="approved", token=token, message="Pairing approved")


@ui_router.get("/pair", response_class=HTMLResponse, include_in_schema=False)
def pairing_page(
    request: Request,
    code: str | None = None,
    current_user: UserPublic | None = Depends(get_ui_optional_user),
) -> HTMLResponse:
    if current_user is None:
        params = {"next": "/pair"}
        if code:
            params["code"] = code.strip().upper()
        return RedirectResponse(
            f"/login?{urlencode(params)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return templates.TemplateResponse(
        "pair.html",
        {
            "request": request,
            "pair_code": (code or "").strip().upper(),
            "current_user": current_user,
        },
    )


@ui_router.post("/pair/approve", response_class=HTMLResponse, include_in_schema=False)
def approve_pairing(
    request: Request,
    pair_code: str = Form(...),
    current_user: UserPublic = Depends(get_ui_current_user),
) -> HTMLResponse:
    normalized_code = pair_code.strip().upper()
    if not normalized_code:
        return templates.TemplateResponse(
            "pair.html",
            {
                "request": request,
                "pair_code": "",
                "current_user": current_user,
            },
        )
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, status
            FROM pairing_requests
            WHERE pair_code = ?
            """,
            (normalized_code,),
        ).fetchone()
        if not row:
            return templates.TemplateResponse(
                "pair.html",
                {
                    "request": request,
                    "pair_code": normalized_code,
                    "current_user": current_user,
                },
            )
        pair_id, status_value = row
        if status_value != "pending":
            return templates.TemplateResponse(
                "pair.html",
                {
                    "request": request,
                    "pair_code": normalized_code,
                    "current_user": current_user,
                },
            )
        token = create_access_token(subject=current_user.username)
        updated = conn.execute(
            """
            UPDATE pairing_requests
            SET status = 'approved',
                user_id = ?,
                token = ?,
                claimed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                current_user.id,
                token,
                datetime.utcnow().isoformat() + "Z",
                pair_id,
            ),
        )
        conn.commit()
        if updated.rowcount != 1:
            return templates.TemplateResponse(
                "pair.html",
                {
                    "request": request,
                    "pair_code": normalized_code,
                    "current_user": current_user,
                },
            )
    finally:
        conn.close()
    client_host = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "unknown")
    logger.info(
        "pairing_approved code=%s user_id=%s machine_id=unregistered ip=%s ua=%s",
        normalized_code,
        current_user.id,
        client_host,
        user_agent,
    )
    return templates.TemplateResponse(
        "pair.html",
        {
            "request": request,
            "pair_code": normalized_code,
            "current_user": current_user,
        },
    )
