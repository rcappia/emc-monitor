from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Configuracao
from app.services.auth import get_usuario_atual

router = APIRouter(prefix="/configuracoes", tags=["configuracoes"])
templates = Jinja2Templates(directory="app/templates")

CHAVES = [
    ("email_remetente", "E-mail remetente (Outlook)", "email"),
    ("email_senha", "Senha do e-mail", "password"),
    ("email_destinatarios", "Destinatários (separados por vírgula)", "text"),
    ("nome_escritorio", "Nome do escritório", "text"),
    ("advogada", "Advogada responsável", "text"),
    ("oab", "Registro OAB", "text"),
    ("engenheiro", "Engenheiro responsável", "text"),
    ("crea", "Registro CREA", "text"),
]


@router.get("/", response_class=HTMLResponse)
def listar(request: Request, db: Session = Depends(get_db)):
    valores = {}
    for chave, _, _ in CHAVES:
        item = db.query(Configuracao).filter(Configuracao.chave == chave).first()
        valores[chave] = item.valor if item else ""
    return templates.TemplateResponse(
        "configuracoes.html",
        {"request": request, "campos": CHAVES, "valores": valores, "usuario_atual": get_usuario_atual(request)}
    )


@router.post("/salvar")
async def salvar(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for chave, _, _ in CHAVES:
        valor = form.get(chave, "")
        item = db.query(Configuracao).filter(Configuracao.chave == chave).first()
        if item:
            item.valor = valor
        else:
            db.add(Configuracao(chave=chave, valor=valor))
    db.commit()
    return RedirectResponse(url="/configuracoes/?salvo=1", status_code=303)
