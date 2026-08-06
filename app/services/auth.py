"""Autenticação: hash de senha, validação de sessão por cookie."""
import os
import secrets

import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Usuario

# Chave que assina os cookies de login.
#
# Antes estava escrita aqui no código, num repositório público. Isso permitia
# a qualquer pessoa FORJAR um cookie de administradora sem saber senha alguma
# — e, uma vez dentro, ler a senha do Gmail na tela de configurações.
# Trocar a senha não resolvia; só trocar a chave resolve.
#
# Agora vem de variável de ambiente. Sem ela, o sistema gera uma chave
# aleatória: continua seguro, mas os logins caem a cada reinício, o que
# torna o problema visível em vez de silencioso.
SECRET_KEY = os.environ.get("EMC_SECRET_KEY", "")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
    print(
        "[ATENÇÃO] EMC_SECRET_KEY não configurada — usando chave temporária.\n"
        "          Os logins vão expirar a cada reinício do servidor.\n"
        "          Configure EMC_SECRET_KEY nas variáveis de ambiente."
    )

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

# Senha usada só na PRIMEIRA criação de cada usuário (todos são obrigados a
# trocar no primeiro acesso). Antes o valor "emc@2024" estava escrito aqui,
# num repositório público — ou seja, a senha inicial das três contas era de
# conhecimento geral. Agora vem de variável de ambiente e, se não houver,
# é sorteada e mostrada uma única vez no log de inicialização.
_SENHA_INICIAL = os.environ.get("EMC_SENHA_INICIAL", "")
if not _SENHA_INICIAL:
    _SENHA_INICIAL = secrets.token_urlsafe(12)

USUARIOS_INICIAIS = [
    {
        "nome": "Rita Farias",
        "email": "ritafarias@emcprojetos.com.br",
        "senha": _SENHA_INICIAL,
        "perfil": "admin",
        "deve_trocar_senha": True,
    },
    {
        "nome": "Eduardo Cappia",
        "email": "cappia@emcprojetos.com.br",
        "senha": _SENHA_INICIAL,
        "perfil": "tecnico",
        "deve_trocar_senha": True,
    },
    {
        "nome": "Angélica",
        "email": "angelica@emcprojetos.com.br",
        "senha": _SENHA_INICIAL,
        "perfil": "secretaria",
        "deve_trocar_senha": True,
    },
]


def criar_usuarios_iniciais(db: Session):
    """Cria os usuários padrão se ainda não existirem."""
    criados = []
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
            criados.append(dados["email"])
    db.commit()

    # Mostra a senha sorteada uma única vez, e só quando de fato criou alguém.
    if criados and not os.environ.get("EMC_SENHA_INICIAL"):
        print("=" * 72)
        print("USUÁRIO(S) CRIADO(S) COM SENHA PROVISÓRIA SORTEADA:")
        for email in criados:
            print(f"  {email}")
        print(f"  Senha provisória: {_SENHA_INICIAL}")
        print("  Troca obrigatória no primeiro acesso.")
        print("=" * 72)
