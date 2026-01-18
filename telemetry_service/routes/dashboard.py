from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from telemetry_service.dependencies import get_current_user
from telemetry_service.models import UserPublic

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter()


@router.get("/dashboard/machines", response_class=HTMLResponse)
def dashboard_machines(
    request: Request,
    current_user: UserPublic = Depends(get_current_user),
) -> HTMLResponse:
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1]
    return templates.TemplateResponse(
        "dashboard_machines.html",
        {
            "request": request,
            "current_user": current_user,
            "auth_token": token,
        },
    )
