"""The two HTML pages: the dashboard and the ward resources."""

import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import config

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=config.TEMPLATES_DIR)
# Stamped onto every script and stylesheet URL, so a browser never runs yesterday's
# JavaScript against today's page after a restart.
templates.env.globals["asset_v"] = str(int(time.time()))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "home.html")


@router.get("/resources", response_class=HTMLResponse)
async def resources(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "resources.html")
