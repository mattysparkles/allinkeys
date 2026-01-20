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

from telemetry_service.auth import create_access_token
from telemetry_service.db import get_db_connection
from telemetry_service.dependencies import (
    get_current_user,
    get_ui_current_user,
    get_ui_optional_user,
)
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


@router.post("/init", response_model=PairInitResponse, summary="Initialize a pairing request.")
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

    pair_url = str(request.base_url).rstrip("/") + f"/pair?code={pair_code}"
    return PairInitResponse(
        pair_code=pair_code,
        pair_url=pair_url,
        poll_interval_seconds=3,
    )


@router.get("/status", response_model=PairStatusResponse, summary="Check pairing status.")
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


def _approve_pairing(pair_code: str, current_user: UserPublic) -> str:
    normalized = pair_code.strip().upper()
    conn = get_db_connection()
    try:
        row = conn.execute(
            """
            SELECT id, status
            FROM pairing_requests
            WHERE pair_code = ?
            """,
            (normalized,),
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

        token = create_access_token(subject=current_user.username)
        conn.execute(
            """
            UPDATE pairing_requests
            SET status = 'approved',
                user_id = ?,
                token = ?,
                claimed_at = ?
            WHERE id = ?
            """,
            (
                current_user.id,
                token,
                datetime.utcnow().isoformat() + "Z",
                pair_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return token


@router.post("/claim", response_model=PairClaimResponse, summary="Claim a pairing request.")
def claim_pairing(
    payload: PairClaimRequest,
    current_user: UserPublic = Depends(get_current_user),
) -> PairClaimResponse:
    _approve_pairing(payload.pair_code, current_user)
    return PairClaimResponse(status="approved", message="Pairing approved")


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
    normalized = pair_code.strip().upper()
    error = None
    approved = False

    if not normalized:
        error = "Missing pairing code."
    else:
        try:
            _approve_pairing(normalized, current_user)
            approved = True
            logger.info(
                "pairing_approved code=%s user_id=%s ip=%s ua=%s",
                normalized,
                current_user.id,
                request.client.host if request.client else "unknown",
                request.headers.get("User-Agent", "unknown"),
            )
        except HTTPException as exc:
            error = exc.detail

    return templates.TemplateResponse(
        "pair.html",
        {
            "request": request,
            "pair_code": normalized,
            "current_user": current_user,
            "approved": approved,
            "error": error,
        },
    )
