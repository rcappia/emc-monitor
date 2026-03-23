from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Cliente(Base):
    """Cliente do escritório — dados cadastrais completos."""
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    razao_social: Mapped[str] = mapped_column(String(300))
    responsavel: Mapped[str] = mapped_column(String(200), nullable=True, default="")
    email: Mapped[str] = mapped_column(String(200), nullable=True, default="")
    celular: Mapped[str] = mapped_column(String(30), nullable=True, default="")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Um cliente pode ter vários termos monitorados (nomes + processos)
    monitorados: Mapped[list["Monitorado"]] = relationship(back_populates="cliente", cascade="all, delete-orphan")


class Monitorado(Base):
    """Termo monitorado no DOU — pode ser nome de cliente ou número de processo."""
    __tablename__ = "monitorados"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nome_cliente: Mapped[str] = mapped_column(String(200))
    termo_busca: Mapped[str] = mapped_column(String(300))
    tipo: Mapped[str] = mapped_column(String(50), default="nome")  # "nome" | "processo"
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Vínculo opcional com cliente (para monitorados antigos que não tinham cliente)
    cliente_id: Mapped[int] = mapped_column(Integer, ForeignKey("clientes.id"), nullable=True)
    cliente: Mapped["Cliente"] = relationship(back_populates="monitorados")

    alertas: Mapped[list["AlertaDOU"]] = relationship(back_populates="monitorado", cascade="all, delete-orphan")


class AlertaDOU(Base):
    """Publicação encontrada no DOU para um termo monitorado."""
    __tablename__ = "alertas_dou"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    monitorado_id: Mapped[int] = mapped_column(ForeignKey("monitorados.id"))
    data_publicacao: Mapped[str] = mapped_column(String(20))
    secao: Mapped[str] = mapped_column(String(10))
    titulo: Mapped[str] = mapped_column(String(500))
    resumo: Mapped[str] = mapped_column(Text, nullable=True)
    paragrafo: Mapped[str] = mapped_column(Text, nullable=True)
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
    perfil: Mapped[str] = mapped_column(String(20), default="usuario")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deve_trocar_senha: Mapped[bool] = mapped_column(Boolean, default=True)


class Configuracao(Base):
    """Configurações do escritório (e-mail, nome, etc.)."""
    __tablename__ = "configuracoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    chave: Mapped[str] = mapped_column(String(100), unique=True)
    valor: Mapped[str] = mapped_column(Text, nullable=True)
