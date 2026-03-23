from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Usuario
from app.services.auth import (
    verificar_senha, criar_token, get_usuario_atual,
    hash_senha, COOKIE_NAME, SESSION_HOURS
)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def pagina_login(request: Request, erro: str = None, proximo: str = "/"):
    # Se já está logado, vai direto
    if get_usuario_atual(request):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "erro": erro,
        "proximo": proximo,
    })


@router.post("/login")
def fazer_login(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
    proximo: str = Form("/"),
    db: Session = Depends(get_db),
):
    usuario = db.query(Usuario).filter(
        Usuario.email == email.strip().lower(),
        Usuario.ativo == True,
    ).first()

    if not usuario or not verificar_senha(senha, usuario.senha_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "erro": "E-mail ou senha incorretos.",
            "proximo": proximo,
        }, status_code=401)

    token = criar_token(usuario.id)
    destino = "/trocar-senha" if usuario.deve_trocar_senha else proximo
    response = RedirectResponse(url=destino, status_code=303)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/trocar-senha", response_class=HTMLResponse)
def pagina_trocar_senha(request: Request):
    usuario = get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("trocar_senha.html", {
        "request": request,
        "usuario": usuario,
        "erro": None,
    })


@router.post("/trocar-senha")
def trocar_senha(
    request: Request,
    senha_atual: str = Form(...),
    nova_senha: str = Form(...),
    confirmar_senha: str = Form(...),
    db: Session = Depends(get_db),
):
    usuario = get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    def erro(msg):
        return templates.TemplateResponse("trocar_senha.html", {
            "request": request, "usuario": usuario, "erro": msg,
        }, status_code=400)

    if not verificar_senha(senha_atual, usuario.senha_hash):
        return erro("Senha atual incorreta.")
    if len(nova_senha) < 6:
        return erro("A nova senha precisa ter pelo menos 6 caracteres.")
    if nova_senha != confirmar_senha:
        return erro("As senhas não coincidem.")

    db_usuario = db.get(Usuario, usuario.id)
    db_usuario.senha_hash = hash_senha(nova_senha)
    db_usuario.deve_trocar_senha = False
    db.commit()

    return RedirectResponse(url="/?senha_trocada=1", status_code=303)
