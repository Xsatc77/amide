from fastapi import APIRouter, Request

from app.templating import templates

router = APIRouter()


@router.get("/legal/disclaimer")
def disclaimer(request: Request):
    return templates.TemplateResponse(request, "legal/disclaimer.html")


@router.get("/legal/terms")
def terms(request: Request):
    return templates.TemplateResponse(request, "legal/terms.html")


@router.get("/legal/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(request, "legal/privacy.html")
