"""
Aplicação principal do EMC Monitor.
Inicia o servidor web, o banco de dados e o agendamento automático.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, joinedload
from datetime import date, datetime

from sqlalchemy import text

from app.database import init_db, SessionLocal, engine
from app.models import AlertaDOU, Monitorado, Cliente, ProcessoCliente
from app.routers import monitorados, alertas, configuracoes, clientes
from app.routers.alertas import _executar_busca
from app.routers import auth_router
from app.services.auth import get_usuario_atual, criar_usuarios_iniciais, requer_login


def tarefa_diaria():
    """Executada automaticamente todo dia às 7h — busca no DOU."""
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M')}] Iniciando busca diária no DOU...")
    db: Session = SessionLocal()
    try:
        _executar_busca(db)
        print(f"[{datetime.now().strftime('%H:%M')}] Busca diária concluída.")
    finally:
        db.close()


def _adicionar_coluna(conn, tabela, coluna, tipo):
    """Tenta adicionar uma coluna se ela não existir."""
    try:
        conn.execute(text(f"SELECT {coluna} FROM {tabela} LIMIT 1"))
    except Exception:
        conn.rollback()
        try:
            conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}"))
            conn.commit()
            print(f"Migração: coluna {coluna} adicionada em {tabela}")
        except Exception:
            conn.rollback()


def migrar_colunas_banco():
    """
    Adiciona colunas novas em tabelas existentes (ALTER TABLE).
    create_all() só cria tabelas novas, não altera as existentes.
    """
    with engine.connect() as conn:
        # ── Colunas novas em clientes ──
        _adicionar_coluna(conn, "clientes", "responsavel", "VARCHAR(200) DEFAULT ''")
        _adicionar_coluna(conn, "clientes", "email", "VARCHAR(200) DEFAULT ''")
        _adicionar_coluna(conn, "clientes", "celular", "VARCHAR(30) DEFAULT ''")

        # ── Colunas novas em alertas_dou ──
        _adicionar_coluna(conn, "alertas_dou", "cliente_id", "INTEGER REFERENCES clientes(id)")
        _adicionar_coluna(conn, "alertas_dou", "termo_encontrado", "VARCHAR(300) DEFAULT ''")


def migrar_monitorados_para_clientes(db: Session):
    """
    Migração automática:
    1. Cria Cliente para cada monitorado tipo 'nome' (se não existir)
    2. Cria ProcessoCliente para cada monitorado tipo 'processo'
    3. Vincula alertas antigos (que tem monitorado_id) ao cliente correspondente
    """
    try:
        orfaos = db.query(Monitorado).all()
    except Exception as e:
        print(f"Aviso: tabela monitorados não acessível ({e}). Migração ignorada.")
        return
    if not orfaos:
        return

    criados = 0
    for m in orfaos:
        # Verifica se já existe cliente com essa razão social
        cliente = db.query(Cliente).filter(Cliente.razao_social == m.nome_cliente.strip()).first()

        if not cliente:
            cliente = Cliente(
                razao_social=m.nome_cliente.strip(),
                termo_busca=m.termo_busca.strip() if m.tipo == "nome" else "",
                ativo=m.ativo,
            )
            db.add(cliente)
            db.flush()
            criados += 1

        # Se for processo, cria registro em processos_cliente
        if m.tipo == "processo":
            ja_existe = (
                db.query(ProcessoCliente)
                .filter(ProcessoCliente.cliente_id == cliente.id)
                .filter(ProcessoCliente.numero_processo == m.termo_busca.strip())
                .first()
            )
            if not ja_existe:
                proc = ProcessoCliente(
                    cliente_id=cliente.id,
                    numero_processo=m.termo_busca.strip(),
                    ativo=m.ativo,
                )
                db.add(proc)
        elif not cliente.termo_busca:
            # Atualiza termo de busca se ainda não tinha
            cliente.termo_busca = m.termo_busca.strip()

        # Vincula alertas antigos ao cliente
        alertas_sem_cliente = (
            db.query(AlertaDOU)
            .filter(AlertaDOU.monitorado_id == m.id)
            .filter(AlertaDOU.cliente_id == None)
            .all()
        )
        for a in alertas_sem_cliente:
            a.cliente_id = cliente.id

    db.commit()
    if criados:
        print(f"Migração: {criados} cliente(s) criado(s) a partir de monitorados existentes.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa banco de dados e usuários padrão
    init_db()
    try:
        migrar_colunas_banco()
    except Exception as e:
        print(f"Aviso migração de colunas: {e}")
    db = SessionLocal()
    try:
        criar_usuarios_iniciais(db)
        try:
            migrar_monitorados_para_clientes(db)
        except Exception as e:
            print(f"Aviso migração monitorados→clientes: {e}")
    finally:
        db.close()

    # Inicia agendador: busca DOU toda segunda a sexta às 7h
    scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(tarefa_diaria, "cron", day_of_week="mon-fri", hour=6, minute=0)
    scheduler.start()
    print("Agendador iniciado — busca DOU: segunda a sexta às 6h00")

    yield

    scheduler.shutdown()


app = FastAPI(title="EMC Monitor", lifespan=lifespan)

# Arquivos estáticos (CSS, imagens)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="app/templates")


# Handler de erro para mostrar detalhes ao invés de "Internal Server Error"
@app.exception_handler(500)
async def erro_interno(request: Request, exc):
    import traceback
    erro = traceback.format_exc()
    print(f"ERRO 500 em {request.url.path}: {erro}")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(f"Erro interno:\n{erro}", status_code=500)


@app.exception_handler(Exception)
async def erro_geral(request: Request, exc):
    import traceback
    erro = traceback.format_exception(type(exc), exc, exc.__traceback__)
    erro_str = "".join(erro)
    print(f"ERRO em {request.url.path}: {erro_str}")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(f"Erro interno:\n{erro_str}", status_code=500)

# Rotas públicas (sem login)
app.include_router(auth_router.router)

# Rotas protegidas
app.include_router(clientes.router)
app.include_router(monitorados.router)
app.include_router(alertas.router)
app.include_router(configuracoes.router)


@app.middleware("http")
async def verificar_autenticacao(request: Request, call_next):
    """Redireciona para login se não autenticado (exceto rotas públicas)."""
    rotas_publicas = ["/login", "/static"]
    caminho = request.url.path

    if any(caminho.startswith(r) for r in rotas_publicas):
        return await call_next(request)

    usuario = get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url=f"/login?proximo={caminho}", status_code=302)

    # Força troca de senha no primeiro acesso
    if usuario.deve_trocar_senha and caminho != "/trocar-senha":
        return RedirectResponse(url="/trocar-senha", status_code=302)

    response = await call_next(request)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    usuario = get_usuario_atual(request)
    db: Session = SessionLocal()
    try:
        hoje = date.today().strftime("%Y-%m-%d")

        total_monitorados = db.query(Cliente).filter(Cliente.ativo == True).count()
        total_alertas = db.query(AlertaDOU).count()
        total_alertas_hoje = (
            db.query(AlertaDOU)
            .filter(AlertaDOU.data_publicacao == hoje)
            .count()
        )
        alertas_recentes = (
            db.query(AlertaDOU)
            .options(joinedload(AlertaDOU.cliente))
            .order_by(AlertaDOU.encontrado_em.desc())
            .limit(10)
            .all()
        )
        ultimo = db.query(AlertaDOU).order_by(AlertaDOU.encontrado_em.desc()).first()
        ultima_busca = ultimo.encontrado_em.strftime("%d/%m %H:%M") if ultimo else None

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "usuario_atual": usuario,
            "total_monitorados": total_monitorados,
            "total_alertas": total_alertas,
            "total_alertas_hoje": total_alertas_hoje,
            "alertas_recentes": alertas_recentes,
            "ultima_busca": ultima_busca,
        })
    finally:
        db.close()
