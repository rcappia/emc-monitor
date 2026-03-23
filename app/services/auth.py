"""Autenticação: hash de senha, validação de sessão por cookie."""
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Usuario

# Chave secreta para assinar os cookies — não altere após o primeiro uso
SECRET_KEY = "emc-radiodifusao-2024-chave-segura"
COOKIE_NAME = "emc_session"
SESSION_HOURS = 10  # horas antes de expirar o login

_serializer = URLSafeTimedSerializer(SECRET_KEY)


# ── Senhas ──────────────────────────────────────────────────────────────────

def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def verificar_senha(senha: str, hash_salvo: str) -> bool:
    return bcrypt.checkpw(senha.encode(), hash_salvo.encode())


# ── Sessão (cookie) ──────────────────────────────────────────────────────────

def criar_token(user_id: int) -> str:
    return _serializer.dumps(user_id)


def validar_token(token: str) -> int | None:
    try:
        user_id = _serializer.loads(token, max_age=SESSION_HOURS * 3600)
        return user_id
    except (BadSignature, SignatureExpired):
        return None


# ── Usuário atual ────────────────────────────────────────────────────────────

def get_usuario_atual(request: Request) -> Usuario | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user_id = validar_token(token)
    if not user_id:
        return None
    db: Session = SessionLocal()
    try:
        return db.get(Usuario, user_id)
    finally:
        db.close()


def requer_login(request: Request) -> Usuario:
    """Dependência FastAPI: redireciona para login se não autenticado."""
    usuario = get_usuario_atual(request)
    if not usuario or not usuario.ativo:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return usuario


def requer_admin(request: Request) -> Usuario:
    """Dependência FastAPI: exige perfil admin."""
    usuario = requer_login(request)
    if usuario.perfil != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito à administradora.")
    return usuario


# ── Inicialização de usuários padrão ────────────────────────────────────────

USUARIOS_INICIAIS = [
    {
        "nome": "Rita Farias",
        "email": "ritafarias@emcprojetos.com.br",
        "senha": "emc@2024",
        "perfil": "admin",
        "deve_trocar_senha": True,
    },
    {
        "nome": "Eduardo Cappia",
        "email": "cappia@emcprojetos.com.br",
        "senha": "emc@2024",
        "perfil": "tecnico",
        "deve_trocar_senha": True,
    },
    {
        "nome": "Angélica",
        "email": "angelica@emcprojetos.com.br",
        "senha": "emc@2024",
        "perfil": "secretaria",
        "deve_trocar_senha": True,
    },
]


def criar_usuarios_iniciais(db: Session):
    """Cria os usuários padrão se ainda não existirem."""
    for dados in USUARIOS_INICIAIS:
        existe = db.query(Usuario).filter(Usuario.email == dados["email"]).first()
        if not existe:
            usuario = Usuario(
                nome=dados["nome"],
                email=dados["email"],
                senha_hash=hash_senha(dados["senha"]),
                perfil=dados["perfil"],
                deve_trocar_senha=dados["deve_trocar_senha"],
            )
            db.add(usuario)
    db.commit()
