from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from telemetry_service.dependencies import _extract_request_token, get_ui_optional_user
from telemetry_service.models import UserPublic
from telemetry_service.routes.machines import _get_machine_for_user_or_admin

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_home() -> RedirectResponse:
    return RedirectResponse(
        "/dashboard/machines",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/dashboard/machines", response_class=HTMLResponse)
def dashboard_machines(
    request: Request,
    current_user: UserPublic | None = Depends(get_ui_optional_user),
) -> HTMLResponse:
    if current_user is None:
        login_url = f"/login?{urlencode({'next': '/dashboard/machines'})}"
        return RedirectResponse(login_url, status_code=status.HTTP_303_SEE_OTHER)
    token = _extract_request_token(request) or ""
    return templates.TemplateResponse(
        "dashboard_machines.html",
        {
            "request": request,
            "current_user": current_user,
            "auth_token": token,
        },
    )


@router.get("/dashboard/machine/{machine_id}", response_class=HTMLResponse)
def dashboard_machine(
    request: Request,
    machine_id: str,
    current_user: UserPublic | None = Depends(get_ui_optional_user),
) -> HTMLResponse:
    if current_user is None:
        login_url = f"/login?{urlencode({'next': f'/dashboard/machine/{machine_id}'})}"
        return RedirectResponse(login_url, status_code=status.HTTP_303_SEE_OTHER)
    _get_machine_for_user_or_admin(machine_id, current_user)
    token = _extract_request_token(request) or ""
    return templates.TemplateResponse(
        "machine.html",
        {
            "request": request,
            "current_user": current_user,
            "auth_token": token,
            "machine_id": machine_id,
        },
    )


@router.get("/dashboard/pairing", response_class=HTMLResponse)
def dashboard_pairing(
    request: Request,
    current_user: UserPublic | None = Depends(get_ui_optional_user),
) -> HTMLResponse:
    if current_user is None:
        login_url = f"/login?{urlencode({'next': '/dashboard/pairing'})}"
        return RedirectResponse(login_url, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "pairing_instructions.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )
