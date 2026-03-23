from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Monitorado
from app.services.auth import get_usuario_atual

router = APIRouter(prefix="/monitorados", tags=["monitorados"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def listar(request: Request, db: Session = Depends(get_db)):
    monitorados = db.query(Monitorado).order_by(Monitorado.nome_cliente).all()
    return templates.TemplateResponse("monitorados.html", {
        "request": request,
        "monitorados": monitorados,
        "usuario_atual": get_usuario_atual(request),
    })


@router.post("/adicionar")
def adicionar(
    nome_cliente: str = Form(...),
    termo_busca: str = Form(...),
    tipo: str = Form("nome"),
    db: Session = Depends(get_db),
):
    item = Monitorado(nome_cliente=nome_cliente, termo_busca=termo_busca, tipo=tipo)
    db.add(item)
    db.commit()
    return RedirectResponse(url="/monitorados/", status_code=303)


@router.post("/remover/{item_id}")
def remover(item_id: int, db: Session = Depends(get_db)):
    item = db.get(Monitorado, item_id)
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(url="/monitorados/", status_code=303)


@router.post("/toggle/{item_id}")
def toggle_ativo(item_id: int, db: Session = Depends(get_db)):
    item = db.get(Monitorado, item_id)
    if item:
        item.ativo = not item.ativo
        db.commit()
    return RedirectResponse(url="/monitorados/", status_code=303)
