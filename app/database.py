import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Usa Supabase/PostgreSQL em produção ou SQLite local para desenvolvimento
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./emc.db")

# Render/Supabase retorna "postgres://" mas SQLAlchemy exige "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# pool_pre_ping: testa a conexão antes de usar. O pooler do Supabase encerra
# conexões ociosas, e sem esse teste a primeira consulta depois de um período
# parado falha com "server closed the connection unexpectedly".
# pool_recycle: descarta conexões com mais de 5 minutos, antes que o pooler
# as encerre por conta própria.
engine_kwargs = {"connect_args": connect_args}
if not DATABASE_URL.startswith("sqlite"):
    engine_kwargs.update(pool_pre_ping=True, pool_recycle=300)

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401 — garante que modelos sejam registrados
    Base.metadata.create_all(bind=engine)
