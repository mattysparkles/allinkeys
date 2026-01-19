from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from telemetry_service.dependencies import _extract_request_token, get_ui_current_user
from telemetry_service.models import UserPublic
from telemetry_service.routes.machines import _get_machine_for_user_or_admin

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter()


@router.get("/dashboard/machines", response_class=HTMLResponse)
def dashboard_machines(
    request: Request,
    current_user: UserPublic = Depends(get_ui_current_user),
) -> HTMLResponse:
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
    current_user: UserPublic = Depends(get_ui_current_user),
) -> HTMLResponse:
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
