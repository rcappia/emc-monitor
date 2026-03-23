"""
Busca automática no DOU via INLABS e envia e-mail de alerta.
Executado pelo GitHub Actions todo dia útil às 6h (horário de Brasília).
Credenciais lidas de variáveis de ambiente (GitHub Secrets).

Se DATABASE_URL estiver configurado (Supabase), lê os clientes do banco
e salva os alertas encontrados — ficam visíveis no painel web.
Caso contrário, lê de clientes.json (modo local/fallback).
"""
import io
import json
import os
import re
import smtplib
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ── Credenciais (vêm do GitHub Secrets) ──────────────────────────────────────
INLABS_EMAIL = os.environ["INLABS_EMAIL"]
INLABS_SENHA = os.environ["INLABS_SENHA"]
EMAIL_REMETENTE = os.environ["EMAIL_REMETENTE"]
EMAIL_SENHA = os.environ["EMAIL_SENHA"]
EMAIL_DESTINATARIOS = [e.strip() for e in os.environ["EMAIL_DESTINATARIOS"].split(",") if e.strip()]

# ── Banco de dados (opcional — Supabase em produção) ──────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ── Configurações ─────────────────────────────────────────────────────────────
URL_LOGIN = "https://inlabs.in.gov.br/logar.php"
URL_BASE = "https://inlabs.in.gov.br/index.php?p="
SECOES = ["DO1", "DO2", "DO3"]
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ── Clientes: banco ou arquivo ────────────────────────────────────────────────

def _carregar_clientes() -> list[dict]:
    """Carrega lista de clientes do Supabase (se disponível) ou clientes.json."""
    if DATABASE_URL:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, nome_cliente, termo_busca, tipo FROM monitorados WHERE ativo = TRUE"
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
            print(f"Clientes carregados do banco de dados: {len(rows)}")
            return [{"id": r[0], "nome_cliente": r[1], "termo_busca": r[2], "tipo": r[3]} for r in rows]
        except Exception as exc:
            print(f"[AVISO] Falha ao conectar ao banco: {exc} — usando clientes.json")

    arquivo = Path(__file__).parent.parent / "clientes.json"
    clientes = json.loads(arquivo.read_text(encoding="utf-8"))
    for c in clientes:
        c.setdefault("id", None)
    print(f"Clientes carregados de clientes.json: {len(clientes)}")
    return clientes


def _salvar_alerta_db(monitorado_id: int, resultado: dict):
    """Salva um alerta no Supabase, evitando duplicatas."""
    if not DATABASE_URL or monitorado_id is None:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM alertas_dou WHERE monitorado_id = %s AND data_publicacao = %s AND titulo = %s",
            (monitorado_id, resultado["data_publicacao"], resultado["titulo"][:500]),
        )
        if not cur.fetchone():
            cur.execute(
                """INSERT INTO alertas_dou
                   (monitorado_id, data_publicacao, secao, titulo, resumo, paragrafo, url, email_enviado, encontrado_em)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s)""",
                (
                    monitorado_id,
                    resultado["data_publicacao"],
                    resultado["secao"],
                    resultado["titulo"][:500],
                    "",
                    resultado.get("paragrafo", ""),
                    resultado.get("url", ""),
                    datetime.utcnow(),
                ),
            )
            conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        print(f"[AVISO] Erro ao salvar alerta no banco: {exc}")


CLIENTES = _carregar_clientes()


# ── INLABS ────────────────────────────────────────────────────────────────────

def login_inlabs() -> requests.Session:
    s = requests.Session()
    s.post(
        URL_LOGIN,
        data={"email": INLABS_EMAIL, "password": INLABS_SENHA},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    return s


def normalizar(texto: str) -> str:
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii").lower()


def limpar_html(texto: str) -> str:
    if not texto:
        return ""
    sem_tags = re.sub(r"<[^>]+>", " ", texto)
    sem_ent = sem_tags.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", sem_ent).strip()


def texto_elemento(elem) -> str:
    if elem is None:
        return ""
    partes = []
    for e in elem.iter():
        if e.text and e.text.strip():
            partes.append(limpar_html(e.text.strip()))
        if e.tail and e.tail.strip():
            partes.append(limpar_html(e.tail.strip()))
    return " ".join(p for p in partes if p)


def extrair_paragrafo(texto: str, termo: str) -> str:
    if not texto:
        return ""
    if len(texto) <= 1500:
        return texto
    pos = normalizar(texto).find(normalizar(termo))
    if pos == -1:
        return texto[:800] + "..."
    inicio = max(0, pos - 200)
    fim = min(len(texto), pos + 600)
    if inicio > 0:
        esp = texto.rfind(" ", 0, inicio)
        if esp != -1:
            inicio = esp + 1
    if fim < len(texto):
        esp = texto.find(" ", fim)
        if esp != -1:
            fim = esp
    trecho = texto[inicio:fim].strip()
    if inicio > 0:
        trecho = "..." + trecho
    if fim < len(texto):
        trecho = trecho + "..."
    return trecho


def extrair_processo_dou(texto: str, termo: str) -> str:
    pos = normalizar(texto).find(normalizar(termo))
    if pos == -1:
        return ""
    trecho_antes = texto[max(0, pos - 300): pos]
    matches = re.findall(r"\d{5,6}\.\d{6}/\d{4}-\d{2}", trecho_antes)
    return matches[-1] if matches else ""


def buscar_em_xml(xml_bytes: bytes, termo: str, secao: str) -> list[dict]:
    resultados = []
    termo_norm = normalizar(termo)
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return resultados
    for article in root.iter("article"):
        texto_completo = texto_elemento(article)
        if termo_norm not in normalizar(texto_completo):
            continue
        titulo = ""
        for caminho in ["body/Titulo", "body/Identifica", "body/Ementa"]:
            elem = article.find(caminho)
            if elem is not None:
                t = texto_elemento(elem).strip()
                if t:
                    titulo = t
                    break
        texto_body = texto_elemento(article.find("body/Texto") or article)
        texto_usar = texto_body or texto_completo
        resultados.append({
            "titulo": (titulo or f"Publicação {secao}")[:500],
            "paragrafo": extrair_paragrafo(texto_usar, termo),
            "processo_dou": extrair_processo_dou(texto_usar, termo),
            "url": article.get("pdfPage", ""),
            "data_publicacao": article.get("pubDate", ""),
            "secao": f"Seção {secao[-1]}",
        })
    return resultados


def buscar_hoje(session: requests.Session) -> list[dict]:
    hoje = date.today().strftime("%Y-%m-%d")
    cookie = session.cookies.get("inlabs_session_cookie", "")
    if not cookie:
        print("ERRO: falha no login INLABS")
        return []

    todos = []
    for cliente in CLIENTES:
        nome = cliente["nome_cliente"]
        termo = cliente["termo_busca"]
        tipo = cliente["tipo"]
        monitorado_id = cliente.get("id")
        print(f"  Buscando: {nome}...", end=" ", flush=True)
        encontrados = []
        for secao in SECOES:
            url = f"{URL_BASE}{hoje}&dl={hoje}-{secao}.zip"
            try:
                resp = session.get(
                    url,
                    headers={"Cookie": f"inlabs_session_cookie={cookie}", "origem": "736372697074"},
                    timeout=60,
                )
                if resp.status_code != 200:
                    continue
                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    for nome_arq in zf.namelist():
                        if not nome_arq.endswith(".xml"):
                            continue
                        with zf.open(nome_arq) as f:
                            itens = buscar_em_xml(f.read(), termo, secao)
                        for item in itens:
                            item["nome_cliente"] = nome
                            item["tipo"] = tipo
                            item["termo_busca"] = termo
                            encontrados.append(item)
                            # Salva no banco de dados (Supabase)
                            _salvar_alerta_db(monitorado_id, item)
            except Exception as exc:
                print(f"\n  [AVISO] Erro em {secao}: {exc}")
        print(f"{len(encontrados)} resultado(s)")
        todos.extend(encontrados)
    return todos


# ── E-mail ────────────────────────────────────────────────────────────────────

def enviar_email(alertas: list[dict]) -> bool:
    hoje = date.today().strftime("%d/%m/%Y")
    qtd = len(alertas)
    clientes_nomes = list({a["nome_cliente"] for a in alertas})
    resumo = ", ".join(clientes_nomes[:3]) + (" e outros" if len(clientes_nomes) > 3 else "")
    assunto = f"[EMC Monitor] Alerta DOU {hoje} — {qtd} publicação{'ões' if qtd > 1 else ''}: {resumo}"

    blocos_html = ""
    linhas_txt = [f"EMC Monitor — Alertas DOU {hoje}", f"Total: {qtd} publicação(ões)", "=" * 60]

    for i, a in enumerate(alertas, 1):
        processo_html = ""
        if a.get("tipo") == "processo":
            processo_html = f'<tr><td style="color:#6b7280;font-size:13px;width:140px;">Nº Processo</td><td style="font-weight:600;font-family:monospace;">{a.get("termo_busca","")}</td></tr>'
        elif a.get("processo_dou"):
            processo_html = f'<tr><td style="color:#6b7280;font-size:13px;width:140px;">Processo no DOU</td><td style="font-weight:600;font-family:monospace;">{a.get("processo_dou","")}</td></tr>'

        link_btn = f'<a href="{a["url"]}" style="display:inline-block;margin-top:10px;background:#003087;color:#fff;padding:7px 16px;border-radius:6px;text-decoration:none;font-size:13px;">Ver no DOU</a>' if a.get("url") else ""

        blocos_html += f"""
        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:20px;margin-bottom:20px;">
          <div style="font-size:18px;font-weight:700;color:#111827;margin-bottom:14px;">{a["nome_cliente"]}</div>
          <table style="width:100%;border-collapse:collapse;margin-bottom:14px;">
            {processo_html}
            <tr><td style="padding:4px 0;color:#6b7280;font-size:13px;width:140px;">Seção do DOU</td><td style="padding:4px 0;">{a["secao"]}</td></tr>
            <tr><td style="padding:4px 0;color:#6b7280;font-size:13px;">Data</td><td style="padding:4px 0;">{a.get("data_publicacao", hoje)}</td></tr>
            <tr><td style="padding:4px 0;color:#6b7280;font-size:13px;vertical-align:top;">Assunto</td><td style="padding:4px 0;font-weight:600;">{a["titulo"]}</td></tr>
          </table>
          <div style="background:#f9fafb;border-left:4px solid #003087;padding:12px 16px;border-radius:0 6px 6px 0;">
            <div style="font-size:11px;color:#6b7280;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">Trecho onde aparece</div>
            <div style="font-size:14px;color:#374151;line-height:1.6;">{a.get("paragrafo","")}</div>
          </div>
          {link_btn}
        </div>"""

        linhas_txt += [f"\n[{i}] {a['nome_cliente']}"]
        if a.get("processo_dou"):
            linhas_txt.append(f"    Processo: {a['processo_dou']}")
        linhas_txt += [
            f"    Seção: {a['secao']}",
            f"    Data: {a.get('data_publicacao', hoje)}",
            f"    Assunto: {a['titulo']}",
            f"    Trecho: {a.get('paragrafo', '')}",
        ]
        if a.get("url"):
            linhas_txt.append(f"    Link: {a['url']}")
        linhas_txt.append("-" * 60)

    html = f"""<html><body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px;">
      <div style="max-width:680px;margin:auto;">
        <div style="background:#003087;color:#fff;padding:20px 24px;border-radius:8px 8px 0 0;">
          <div style="font-size:20px;font-weight:700;">📡 EMC Monitor — Alerta DOU</div>
          <div style="opacity:0.75;font-size:13px;margin-top:4px;">{qtd} publicação{'ões' if qtd>1 else ''} encontrada{'s' if qtd>1 else ''} em {hoje}</div>
        </div>
        <div style="background:#f4f6f9;padding:20px 0;">{blocos_html}</div>
        <div style="text-align:center;font-size:11px;color:#9ca3af;padding:10px 0 20px;">Enviado automaticamente pelo EMC Monitor às 6h00.</div>
      </div>
    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = EMAIL_REMETENTE
        msg["To"] = ", ".join(EMAIL_DESTINATARIOS)
        msg.attach(MIMEText("\n".join(linhas_txt), "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(EMAIL_REMETENTE, EMAIL_SENHA)
            srv.sendmail(EMAIL_REMETENTE, EMAIL_DESTINATARIOS, msg.as_string())
        return True
    except Exception as exc:
        print(f"ERRO ao enviar e-mail: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    hoje = date.today()
    print(f"EMC Monitor — Busca DOU {hoje.strftime('%d/%m/%Y')}")
    print(f"Dia da semana: {hoje.strftime('%A')}")
    print(f"Banco de dados: {'Supabase' if DATABASE_URL else 'clientes.json (sem banco)'}")
    print()

    session = login_inlabs()
    cookie = session.cookies.get("inlabs_session_cookie", "")
    print(f"Login INLABS: {'OK' if cookie else 'FALHOU'}")
    if not cookie:
        exit(1)

    print(f"\nBuscando {len(CLIENTES)} clientes no DOU...")
    alertas = buscar_hoje(session)

    print(f"\nTotal encontrado: {len(alertas)} publicação(ões)")

    if alertas:
        print("Enviando e-mail...")
        ok = enviar_email(alertas)
        print("E-mail:", "ENVIADO" if ok else "FALHOU")
    else:
        print("Nenhum cliente apareceu no DOU hoje — nenhum e-mail enviado.")
