from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Cliente(Base):
    """Cliente do escritório — dados cadastrais + busca no DOU pela razão social."""
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    razao_social: Mapped[str] = mapped_column(String(300))
    termo_busca: Mapped[str] = mapped_column(String(300), nullable=True, default="")
    responsavel: Mapped[str] = mapped_column(String(200), nullable=True, default="")
    email: Mapped[str] = mapped_column(String(200), nullable=True, default="")
    celular: Mapped[str] = mapped_column(String(30), nullable=True, default="")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    processos: Mapped[list["ProcessoCliente"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan"
    )
    alertas: Mapped[list["AlertaDOU"]] = relationship(
        back_populates="cliente", cascade="all, delete-orphan"
    )


class ProcessoCliente(Base):
    """Número de processo ANATEL vinculado a um cliente."""
    __tablename__ = "processos_cliente"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    cliente_id: Mapped[int] = mapped_column(Integer, ForeignKey("clientes.id"))
    numero_processo: Mapped[str] = mapped_column(String(100))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    cliente: Mapped["Cliente"] = relationship(back_populates="processos")


class AlertaDOU(Base):
    """Publicação encontrada no DOU para um cliente."""
    __tablename__ = "alertas_dou"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    cliente_id: Mapped[int] = mapped_column(Integer, ForeignKey("clientes.id"), nullable=True)
    # campo legado — manter para não quebrar registros antigos
    monitorado_id: Mapped[int] = mapped_column(Integer, ForeignKey("monitorados.id", ondelete="SET NULL"), nullable=True)
    data_publicacao: Mapped[str] = mapped_column(String(20))
    secao: Mapped[str] = mapped_column(String(10))
    titulo: Mapped[str] = mapped_column(String(500))
    resumo: Mapped[str] = mapped_column(Text, nullable=True)
    paragrafo: Mapped[str] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=True)
    termo_encontrado: Mapped[str] = mapped_column(String(300), nullable=True, default="")
    email_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    encontrado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    cliente: Mapped["Cliente"] = relationship(back_populates="alertas")


# ── Tabela legada (mantida para não perder dados) ─────────────────────────────

class Monitorado(Base):
    """LEGADO — será descontinuado. Mantido para compatibilidade."""
    __tablename__ = "monitorados"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    nome_cliente: Mapped[str] = mapped_column(String(200))
    termo_busca: Mapped[str] = mapped_column(String(300))
    tipo: Mapped[str] = mapped_column(String(50), default="nome")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    cliente_id: Mapped[int] = mapped_column(Integer, ForeignKey("clientes.id"), nullable=True)


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
    """Configurações do escritório."""
    __tablename__ = "configuracoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    chave: Mapped[str] = mapped_column(String(100), unique=True)
    valor: Mapped[str] = mapped_column(Text, nullable=True)
