from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Monitorado(Base):
    """Termo monitorado no DOU — pode ser nome de cliente ou número de processo."""
    __tablename__ = "monitorados"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nome_cliente: Mapped[str] = mapped_column(String(200))
    termo_busca: Mapped[str] = mapped_column(String(300))  # nome ou número do processo
    tipo: Mapped[str] = mapped_column(String(50), default="nome")  # "nome" | "processo"
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    alertas: Mapped[list["AlertaDOU"]] = relationship(back_populates="monitorado", cascade="all, delete-orphan")


class AlertaDOU(Base):
    """Publicação encontrada no DOU para um termo monitorado."""
    __tablename__ = "alertas_dou"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    monitorado_id: Mapped[int] = mapped_column(ForeignKey("monitorados.id"))
    data_publicacao: Mapped[str] = mapped_column(String(20))   # ex: "2024-03-22"
    secao: Mapped[str] = mapped_column(String(10))             # "do1", "do2", "do3"
    titulo: Mapped[str] = mapped_column(String(500))
    resumo: Mapped[str] = mapped_column(Text, nullable=True)
    paragrafo: Mapped[str] = mapped_column(Text, nullable=True)   # trecho completo onde apareceu
    url: Mapped[str] = mapped_column(String(1000), nullable=True)
    email_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    encontrado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    monitorado: Mapped["Monitorado"] = relationship(back_populates="alertas")


class Usuario(Base):
    """Usuários do sistema com login e senha."""
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nome: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(200))
    perfil: Mapped[str] = mapped_column(String(20), default="usuario")  # "admin" | "tecnico" | "secretaria"
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deve_trocar_senha: Mapped[bool] = mapped_column(Boolean, default=True)


class Configuracao(Base):
    """Configurações do escritório (e-mail, nome, etc.)."""
    __tablename__ = "configuracoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    chave: Mapped[str] = mapped_column(String(100), unique=True)
    valor: Mapped[str] = mapped_column(Text, nullable=True)
