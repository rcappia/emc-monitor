from fastapi import APIRouter, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from datetime import date
from app.database import get_db
from app.models import Monitorado, AlertaDOU, Configuracao
from app.services import dou_api, email_sender
from app.services.auth import get_usuario_atual

router = APIRouter(prefix="/alertas", tags=["alertas"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def listar(request: Request, filtro: str = "", db: Session = Depends(get_db)):
    query = db.query(AlertaDOU).options(joinedload(AlertaDOU.cliente))

    titulo_filtro = "Todas as publicações"
    if filtro == "hoje":
        hoje = date.today().strftime("%Y-%m-%d")
        query = query.filter(AlertaDOU.data_publicacao == hoje)
        titulo_filtro = "Publicações de hoje"

    alertas = query.order_by(AlertaDOU.encontrado_em.desc()).limit(200).all()

    return templates.TemplateResponse("alertas.html", {
        "request": request,
        "alertas": alertas,
        "titulo_filtro": titulo_filtro,
        "filtro_ativo": filtro,
        "usuario_atual": get_usuario_atual(request),
    })


@router.get("/historico-buscas", response_class=HTMLResponse)
def historico_buscas(request: Request, db: Session = Depends(get_db)):
    """Mostra todos os dias e horários em que buscas foram realizadas."""
    from sqlalchemy import func, distinct, cast, Date

    # Busca todas as datas/horas distintas de encontrado_em (agrupadas por minuto)
    registros = (
        db.query(
            func.date_trunc('minute', AlertaDOU.encontrado_em).label('momento'),
            func.count(AlertaDOU.id).label('total_encontrados'),
        )
        .filter(AlertaDOU.encontrado_em != None)
        .group_by(func.date_trunc('minute', AlertaDOU.encontrado_em))
        .order_by(func.date_trunc('minute', AlertaDOU.encontrado_em).desc())
        .all()
    )

    return templates.TemplateResponse("historico_buscas.html", {
        "request": request,
        "registros": registros,
        "usuario_atual": get_usuario_atual(request),
    })


@router.post("/buscar-agora")
def buscar_agora(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Dispara busca manual no DOU para todos os termos ativos."""
    background_tasks.add_task(_executar_busca, db)
    return RedirectResponse(url="/alertas/?msg=busca_iniciada", status_code=303)


def _executar_busca(db: Session):
    """Lógica de busca executada em background."""
    monitorados = db.query(Monitorado).filter(Monitorado.ativo == True).all()
    novos_alertas = []

    hoje = date.today().strftime("%d-%m-%Y")

    for mon in monitorados:
        resultados = dou_api.buscar_no_dou(mon.termo_busca, hoje, hoje)
        for res in resultados:
            # Evita duplicatas para o mesmo dia
            existe = (
                db.query(AlertaDOU)
                .filter(
                    AlertaDOU.monitorado_id == mon.id,
                    AlertaDOU.data_publicacao == res["data_publicacao"],
                    AlertaDOU.titulo == res["titulo"][:500],
                )
                .first()
            )
            if not existe:
                alerta = AlertaDOU(
                    monitorado_id=mon.id,
                    data_publicacao=res["data_publicacao"],
                    secao=res["secao"],
                    titulo=res["titulo"][:500],
                    resumo=res.get("resumo", "")[:500],
                    paragrafo=res.get("paragrafo", ""),
                    url=res.get("url", ""),
                )
                db.add(alerta)
                novos_alertas.append({
                    **res,
                    "nome_cliente": mon.nome_cliente,
                    "tipo": mon.tipo,
                    "termo_busca": mon.termo_busca,
                })

    db.commit()

    # Envia e-mail se houver novidades
    if novos_alertas:
        _enviar_email_alertas(db, novos_alertas)


def _enviar_email_alertas(db: Session, alertas: list[dict]):
    """Busca configurações de e-mail e envia notificação."""
    def cfg(chave):
        item = db.query(Configuracao).filter(Configuracao.chave == chave).first()
        return item.valor if item else ""

    remetente = cfg("email_remetente")
    senha = cfg("email_senha")
    destinatarios_raw = cfg("email_destinatarios")

    if not remetente or not senha or not destinatarios_raw:
        return  # e-mail não configurado

    destinatarios = [e.strip() for e in destinatarios_raw.split(",") if e.strip()]
    email_sender.enviar_alertas_dou(remetente, senha, destinatarios, alertas)
