from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models import Cliente, ProcessoCliente
from app.services.auth import get_usuario_atual

router = APIRouter(prefix="/clientes", tags=["clientes"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def listar_clientes(request: Request, db: Session = Depends(get_db)):
    clientes = (
        db.query(Cliente)
        .options(joinedload(Cliente.processos), joinedload(Cliente.alertas))
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
    return templates.TemplateResponse("cliente_form.html", {
        "request": request,
        "cliente": None,
        "usuario_atual": get_usuario_atual(request),
    })


@router.post("/novo")
def criar_cliente(
    razao_social: str = Form(...),
    termo_busca: str = Form(""),
    responsavel: str = Form(""),
    email: str = Form(""),
    celular: str = Form(""),
    db: Session = Depends(get_db),
):
    cliente = Cliente(
        razao_social=razao_social.strip(),
        termo_busca=termo_busca.strip() or razao_social.strip(),
        responsavel=responsavel.strip(),
        email=email.strip(),
        celular=celular.strip(),
    )
    db.add(cliente)
    db.commit()
    return RedirectResponse(url=f"/clientes/{cliente.id}?msg=criado", status_code=303)


@router.get("/{cliente_id}", response_class=HTMLResponse)
def detalhe_cliente(cliente_id: int, request: Request, db: Session = Depends(get_db)):
    cliente = (
        db.query(Cliente)
        .options(joinedload(Cliente.processos), joinedload(Cliente.alertas))
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
    termo_busca: str = Form(""),
    responsavel: str = Form(""),
    email: str = Form(""),
    celular: str = Form(""),
    db: Session = Depends(get_db),
):
    cliente = db.get(Cliente, cliente_id)
    if cliente:
        cliente.razao_social = razao_social.strip()
        cliente.termo_busca = termo_busca.strip() or razao_social.strip()
        cliente.responsavel = responsavel.strip()
        cliente.email = email.strip()
        cliente.celular = celular.strip()
        db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}?msg=salvo", status_code=303)


# ── Processos ANATEL do cliente ───────────────────────────────────────────────

@router.post("/{cliente_id}/processo")
def adicionar_processo(
    cliente_id: int,
    numero_processo: str = Form(...),
    db: Session = Depends(get_db),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        return RedirectResponse(url="/clientes/", status_code=302)

    proc = ProcessoCliente(
        cliente_id=cliente_id,
        numero_processo=numero_processo.strip(),
    )
    db.add(proc)
    db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}?msg=processo_adicionado", status_code=303)


@router.post("/{cliente_id}/processo/{proc_id}/remover")
def remover_processo(cliente_id: int, proc_id: int, db: Session = Depends(get_db)):
    proc = db.get(ProcessoCliente, proc_id)
    if proc and proc.cliente_id == cliente_id:
        db.delete(proc)
        db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)


@router.post("/{cliente_id}/processo/{proc_id}/toggle")
def toggle_processo(cliente_id: int, proc_id: int, db: Session = Depends(get_db)):
    proc = db.get(ProcessoCliente, proc_id)
    if proc and proc.cliente_id == cliente_id:
        proc.ativo = not proc.ativo
        db.commit()
    return RedirectResponse(url=f"/clientes/{cliente_id}", status_code=303)
