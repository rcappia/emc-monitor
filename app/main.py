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
from sqlalchemy.orm import Session
from datetime import date, datetime

from app.database import init_db, SessionLocal
from app.models import AlertaDOU, Monitorado
from app.routers import monitorados, alertas, configuracoes
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializa banco de dados e usuários padrão
    init_db()
    db = SessionLocal()
    try:
        criar_usuarios_iniciais(db)
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

# Rotas públicas (sem login)
app.include_router(auth_router.router)

# Rotas protegidas
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

        total_monitorados = db.query(Monitorado).filter(Monitorado.ativo == True).count()
        total_alertas = db.query(AlertaDOU).count()
        total_alertas_hoje = (
            db.query(AlertaDOU)
            .filter(AlertaDOU.data_publicacao == hoje)
            .count()
        )
        alertas_recentes = (
            db.query(AlertaDOU)
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
