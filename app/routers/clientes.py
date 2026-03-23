from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models import Cliente, Monitorado
from app.services.auth import get_usuario_atual

router = APIRouter(prefix="/clientes", tags=["clientes"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def listar_clientes(request: Request, db: Session = Depends(get_db)):
    """Lista todos os clientes cadastrados."""
    clientes = (
        db.query(Cliente)
        .options(joinedload(Cliente.monitorados))
        .order_by(Cliente.razao_social)
        .all()
    )
    return templates.TemplateResponse("clientes.html", {
        "request": request,
        "clientes": clientes,
        "usuario_atual": get_usuario_atual(request),
    })


@router.get("/novo", response_class=HTMLResponse)
def form_novo_cliente(request: Request):
    """Formulário para cadastrar novo cliente."""
    return templates.TemplateResponse("cliente_form.html", {
        "request": request,
        "cliente": None,
        "usuario_atual": get_usuario_atual(request),
    })


@router.post("/novo")
def criar_cliente(
    razao_social: str = Form(...),
    responsavel: str = Form(""),
    email: str = Form(""),
    celular: str = Form(""),
    db: Session = Depends(get_db),
):
    """Cria novo cliente."""
    cliente = Cliente(
        razao_social=razao_social.strip(),
        responsavel=responsavel.strip(),
        email=email.strip(),
        celular=celular.strip(),
    )
    db.add(cliente)
    db.commit()
    return RedirectResponse(url=f"/clientes/{cliente.id}?msg=criado", status_code=303)


@router.get("/{cliente_id}", response_class=HTMLResponse)
def detalhe_cliente(cliente_id: int, request: Request, db: Session = Depends(get_db)):
    """Página de detalhes e edição do cliente."""
    cliente = (
        db.query(Cliente)
        .options(joinedload(Cliente.monitorados).joinedload(Monitorado.alertas))
        .filter(Cliente.id == cliente_id)
        .first()
    )
    if not cliente:
        return RedirectResponse(url="/clientes/", status_code=302)

    return templates.TemplateResponse("cliente_detalhe.html", {
        "request": request,
        "cliente": cliente,
        "usuario_atual": get_usuario_atual(request),
    })


@router.post("/{cliente_id}/editar")
def editar_cliente(
    cliente_id: int,
    razao_social: str = Form(...),
    responsavel: str = Form(""),
    email: str = Form(""),
    celular: str = Form(""),
    db: Session = Depends(get_db),
):
    """Atualiza dados do cliente."""
    cliente = db.get(Cliente, cliente_id)
    if cliente:
        cliente.razao_social = razao_social.strip()
        cliente.responsavel = responsavel.strip()
        cliente.email = email.strip()
        cliente.celular = celular.strip()
        db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}?msg=salvo", status_code=303)


@router.post("/{cliente_id}/monitorado")
def adicionar_monitorado(
    cliente_id: int,
    termo_busca: str = Form(...),
    tipo: str = Form("nome"),
    db: Session = Depends(get_db),
):
    """Adiciona um termo monitorado vinculado ao cliente."""
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        return RedirectResponse(url="/clientes/", status_code=302)

    # Para tipo "nome", o nome_cliente é a razão social
    nome = cliente.razao_social if tipo == "nome" else cliente.razao_social

    item = Monitorado(
        nome_cliente=nome,
        termo_busca=termo_busca.strip(),
        tipo=tipo,
        cliente_id=cliente_id,
    )
    db.add(item)
    db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}?msg=termo_adicionado", status_code=303)


@router.post("/{cliente_id}/monitorado/{item_id}/remover")
def remover_monitorado(cliente_id: int, item_id: int, db: Session = Depends(get_db)):
    """Remove um termo monitorado do cliente."""
    item = db.get(Monitorado, item_id)
    if item and item.cliente_id == cliente_id:
        db.delete(item)
        db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)


@router.post("/{cliente_id}/monitorado/{item_id}/toggle")
def toggle_monitorado(cliente_id: int, item_id: int, db: Session = Depends(get_db)):
    """Pausa ou reativa um termo monitorado."""
    item = db.get(Monitorado, item_id)
    if item and item.cliente_id == cliente_id:
        item.ativo = not item.ativo
        db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)
