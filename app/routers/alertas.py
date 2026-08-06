from fastapi import APIRouter, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from datetime import date, datetime, time
from app.database import get_db
from app.models import AlertaDOU, Configuracao, BuscaLog, Cliente, ProcessoCliente
from app.services import dou_api, email_sender
from app.services.auth import get_usuario_atual

router = APIRouter(prefix="/alertas", tags=["alertas"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def listar(request: Request, filtro: str = "", db: Session = Depends(get_db)):
    query = db.query(AlertaDOU).options(joinedload(AlertaDOU.cliente))

    titulo_filtro = "Todas as publicações"
    if filtro == "hoje":
        # Filtra por 'encontrado_em', que é data/hora de verdade.
        # Antes comparava 'data_publicacao' (texto vindo do próprio DOU) com
        # o formato "aaaa-mm-dd", que nunca batia — o painel mostrava sempre
        # zero publicações de hoje, mesmo havendo alertas.
        inicio = datetime.combine(date.today(), time.min)
        fim = datetime.combine(date.today(), time.max)
        query = query.filter(AlertaDOU.encontrado_em.between(inicio, fim))
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
    registros = (
        db.query(BuscaLog)
        .order_by(BuscaLog.realizada_em.desc())
        .limit(100)
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


def _carregar_termos_de_busca(db: Session) -> list[dict]:
    """
    Monta a lista de termos a pesquisar a partir da tabela 'clientes'
    (razão social ou termo de busca) e de 'processos_cliente'.

    Antes esta busca lia a tabela LEGADA 'monitorados', que tinha 9 registros
    antigos, enquanto o cadastro real tem 103 clientes. O botão "Buscar agora"
    do painel pesquisava a lista errada e quase nunca achava nada — sem que
    isso aparecesse como erro em lugar nenhum.
    """
    termos = []

    for c in db.query(Cliente).filter(Cliente.ativo == True).all():
        termo = (c.termo_busca or "").strip() or (c.razao_social or "").strip()
        if termo:
            termos.append({
                "cliente_id": c.id,
                "nome_cliente": c.razao_social,
                "termo_busca": termo,
                "tipo": "nome",
            })

    processos = (
        db.query(ProcessoCliente, Cliente)
        .join(Cliente, Cliente.id == ProcessoCliente.cliente_id)
        .filter(ProcessoCliente.ativo == True, Cliente.ativo == True)
        .all()
    )
    for proc, cli in processos:
        numero = (proc.numero_processo or "").strip()
        if numero:
            termos.append({
                "cliente_id": cli.id,
                "nome_cliente": cli.razao_social,
                "termo_busca": numero,
                "tipo": "processo",
            })

    return termos


def _executar_busca(db: Session, tipo: str = "manual"):
    """Lógica de busca executada em background."""
    novos_alertas = []
    registros_novos = []          # objetos AlertaDOU criados nesta busca
    hoje = date.today().strftime("%d-%m-%Y")

    try:
        alvos = _carregar_termos_de_busca(db)

        for alvo in alvos:
            resultados = dou_api.buscar_no_dou(alvo["termo_busca"], hoje, hoje)
            for res in resultados:
                # Evita duplicatas para o mesmo dia
                existe = (
                    db.query(AlertaDOU)
                    .filter(
                        AlertaDOU.cliente_id == alvo["cliente_id"],
                        AlertaDOU.data_publicacao == res["data_publicacao"],
                        AlertaDOU.titulo == res["titulo"][:500],
                    )
                    .first()
                )
                if not existe:
                    alerta = AlertaDOU(
                        cliente_id=alvo["cliente_id"],
                        data_publicacao=res["data_publicacao"],
                        secao=res["secao"],
                        titulo=res["titulo"][:500],
                        resumo=res.get("resumo", "")[:500],
                        paragrafo=res.get("paragrafo", ""),
                        url=res.get("url", ""),
                        termo_encontrado=alvo["termo_busca"],
                        # Só vira True depois que o e-mail sair de fato.
                        email_enviado=False,
                    )
                    db.add(alerta)
                    registros_novos.append(alerta)
                    novos_alertas.append({
                        **res,
                        "nome_cliente": alvo["nome_cliente"],
                        "tipo": alvo["tipo"],
                        "termo_busca": alvo["termo_busca"],
                    })

        db.commit()

        # Envia o e-mail ANTES de registrar o resultado, para que o registro
        # conte a verdade sobre o envio. Antes o log era gravado com
        # sucesso=True antes de tentar enviar: uma falha de e-mail ficava
        # registrada como busca bem-sucedida.
        email_ok = True
        if novos_alertas:
            email_ok = _enviar_email_alertas(db, novos_alertas)
            if email_ok:
                # Marca apenas os alertas criados nesta busca — não todos os
                # pendentes do banco, que podem ser de execuções anteriores.
                for alerta in registros_novos:
                    alerta.email_enviado = True
                db.commit()

        _registrar_log(
            db, tipo,
            total=len(novos_alertas),
            sucesso=email_ok,
            observacao=(
                f"Busca concluída. {len(novos_alertas)} nova(s) publicação(ões)."
                if email_ok
                else f"{len(novos_alertas)} publicação(ões) encontrada(s), "
                     "mas o e-mail NÃO pôde ser enviado."
            ),
        )

    except Exception as e:
        # A sessão pode estar inutilizável depois do erro; o rollback garante
        # que ainda seja possível gravar o registro da falha.
        try:
            db.rollback()
        except Exception:
            pass
        _registrar_log(db, tipo, total=0, sucesso=False,
                       observacao=f"Erro: {str(e)[:500]}")
        raise


def _registrar_log(db: Session, tipo: str, total: int, sucesso: bool, observacao: str):
    """Grava o resultado da busca no histórico, sem deixar o log derrubar a busca."""
    try:
        db.add(BuscaLog(
            tipo=tipo,
            origem="web",
            total_encontrados=total,
            sucesso=sucesso,
            observacao=observacao,
            realizada_em=datetime.utcnow(),
        ))
        db.commit()
    except Exception:
        pass


def _enviar_email_alertas(db: Session, alertas: list[dict]) -> bool:
    """
    Busca configurações de e-mail e envia notificação.
    Retorna True se o e-mail saiu, False caso contrário.

    Antes esta função não retornava nada e engolia qualquer problema: se o
    e-mail não estivesse configurado ou o envio falhasse, a busca era
    registrada como bem-sucedida do mesmo jeito.
    """
    def cfg(chave):
        item = db.query(Configuracao).filter(Configuracao.chave == chave).first()
        return item.valor if item else ""

    remetente = cfg("email_remetente")
    senha = cfg("email_senha")
    destinatarios_raw = cfg("email_destinatarios")

    if not remetente or not senha or not destinatarios_raw:
        print("[AVISO] E-mail não enviado: remetente, senha ou destinatários "
              "não estão configurados no painel.")
        return False

    destinatarios = [e.strip() for e in destinatarios_raw.split(",") if e.strip()]
    if not destinatarios:
        print("[AVISO] E-mail não enviado: lista de destinatários vazia.")
        return False

    try:
        email_sender.enviar_alertas_dou(remetente, senha, destinatarios, alertas)
        return True
    except Exception as exc:
        print(f"[ERRO] Falha ao enviar e-mail pelo painel: {exc}")
        return False
