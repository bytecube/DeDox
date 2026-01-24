"""
UI routes for serving HTML templates.
"""
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dedox.ui import TEMPLATES_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_current_user_optional(request: Request):
    """Check if user is authenticated (optional)."""
    # For now, check for token in cookie or header
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
    return token


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, token: str = Depends(get_current_user_optional)):
    """Dashboard page."""
    if not token:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request, token: str = Depends(get_current_user_optional)):
    """Processing jobs page."""
    if not token:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("jobs.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, token: str = Depends(get_current_user_optional)):
    """Settings page."""
    if not token:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("settings.html", {"request": request})


@router.get("/logout")
async def logout(request: Request):
    """Logout - clear cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response
