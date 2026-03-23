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
    """
    Carrega clientes da tabela 'clientes' + processos de 'processos_cliente'.
    Cada cliente gera um item tipo 'nome' (busca pela razão social).
    Cada processo gera um item tipo 'processo'.
    Fallback: clientes.json se não tiver banco.
    """
    if DATABASE_URL:
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()

            # Clientes ativos — busca pelo nome/razão social
            cur.execute(
                "SELECT id, razao_social, termo_busca FROM clientes WHERE ativo = TRUE"
            )
            clientes_rows = cur.fetchall()

            itens = []
            for cid, razao, termo in clientes_rows:
                termo_busca = (termo or "").strip() or razao.strip()
                itens.append({
                    "id": cid,
                    "nome_cliente": razao,
                    "termo_busca": termo_busca,
                    "tipo": "nome",
                })

            # Processos ativos de cada cliente
            cur.execute("""
                SELECT pc.numero_processo, c.id, c.razao_social
                FROM processos_cliente pc
                JOIN clientes c ON c.id = pc.cliente_id
                WHERE pc.ativo = TRUE AND c.ativo = TRUE
            """)
            for num_proc, cid, razao in cur.fetchall():
                itens.append({
                    "id": cid,
                    "nome_cliente": razao,
                    "termo_busca": num_proc.strip(),
                    "tipo": "processo",
                })

            cur.close()
            conn.close()
            print(f"Carregados do banco: {len(clientes_rows)} clientes + {len(itens) - len(clientes_rows)} processos = {len(itens)} buscas")
            return itens
        except Exception as exc:
            print(f"[AVISO] Falha ao conectar ao banco: {exc} — usando clientes.json")

    arquivo = Path(__file__).parent.parent / "clientes.json"
    clientes = json.loads(arquivo.read_text(encoding="utf-8"))
    for c in clientes:
        c.setdefault("id", None)
    print(f"Clientes carregados de clientes.json: {len(clientes)}")
    return clientes


def _salvar_alerta_db(cliente_id: int, resultado: dict):
    """Salva um alerta no Supabase vinculado ao cliente, evitando duplicatas."""
    if not DATABASE_URL or cliente_id is None:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM alertas_dou WHERE cliente_id = %s AND data_publicacao = %s AND titulo = %s",
            (cliente_id, resultado["data_publicacao"], resultado["titulo"][:500]),
        )
        if not cur.fetchone():
            cur.execute(
                """INSERT INTO alertas_dou
                   (cliente_id, data_publicacao, secao, titulo, resumo, paragrafo, url,
                    termo_encontrado, email_enviado, encontrado_em)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)""",
                (
                    cliente_id,
                    resultado["data_publicacao"],
                    resultado["secao"],
                    resultado["titulo"][:500],
                    "",
                    resultado.get("paragrafo", ""),
                    resultado.get("url", ""),
                    resultado.get("termo_busca", ""),
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
        cliente_id = cliente.get("id")
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
                            _salvar_alerta_db(cliente_id, item)
            except Exception as exc:
                print(f"\n  [AVISO] Erro em {secao}: {exc}")
        print(f"{len(encontrados)} resultado(s)")
        todos.extend(encontrados)
    return todos


# ── E-mail ────────────────────────────────────────────────────────────────────

def _formatar_paragrafo(texto: str) -> str:
    """Separa visualmente o trecho do DOU com quebras antes de elementos estruturais."""
    if not texto:
        return "(texto não disponível)"
    for palavra in ["RESOLVE", "CONSIDERANDO", "DETERMINA", "AUTORIZA", "RATIFICA", "HOMOLOGA", "TORNA PÚBLICO"]:
        texto = re.sub(rf'(?<=[.;,])\s+({palavra})', rf'<br><br>\1', texto)
    texto = re.sub(r'\s+(Art\.\s*\d)',         r'<br><br>\1', texto)
    texto = re.sub(r'\s+(§\s*\d)',             r'<br>\1',     texto)
    texto = re.sub(r'\s+([IVX]+\s*[-–]\s)',    r'<br>\1',     texto)
    texto = re.sub(r'\s+(Parágrafo\s+único)',   r'<br><br>\1', texto, flags=re.IGNORECASE)
    return texto.strip()


def enviar_email(alertas: list[dict]) -> bool:
    hoje = date.today().strftime("%d/%m/%Y")
    qtd = len(alertas)
    clientes_nomes = list({a["nome_cliente"] for a in alertas})
    resumo = ", ".join(clientes_nomes[:3]) + (" e outros" if len(clientes_nomes) > 3 else "")
    assunto = f"[EMC Monitor] Alerta DOU {hoje} — {qtd} publicação{'ões' if qtd > 1 else ''}: {resumo}"

    blocos_html = ""
    linhas_txt = [f"EMC Monitor — Alertas DOU {hoje}", f"Total: {qtd} publicação(ões)", "=" * 60]

    for i, a in enumerate(alertas, 1):
        # Badge tipo
        if a.get("tipo") == "processo":
            badge = '<span style="background:#fef9c3;color:#b45309;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">PROCESSO</span>'
        else:
            badge = '<span style="background:#dcfce7;color:#15803d;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">NOME</span>'

        # Linha de processo
        if a.get("tipo") == "processo":
            linha_processo = f'<tr><td style="padding:5px 0;color:#6b7280;font-size:12px;width:150px;">Nº do Processo</td><td style="padding:5px 0;font-weight:600;font-family:monospace;font-size:13px;">{a.get("termo_busca","")}</td></tr>'
        elif a.get("processo_dou"):
            linha_processo = f'<tr><td style="padding:5px 0;color:#6b7280;font-size:12px;width:150px;">Processo no DOU</td><td style="padding:5px 0;font-weight:600;font-family:monospace;font-size:13px;">{a.get("processo_dou","")}</td></tr>'
        else:
            linha_processo = ""

        trecho_html = _formatar_paragrafo(a.get("paragrafo") or a.get("resumo") or "")
        link_btn = f'<a href="{a["url"]}" style="display:inline-block;margin-top:12px;background:#16a34a;color:#fff;padding:8px 18px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;">Ver publicação no DOU ↗</a>' if a.get("url") else ""

        blocos_html += f"""
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;padding:22px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.05);">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid #f1f5f9;">
            {badge}
            <span style="font-size:17px;font-weight:700;color:#0f172a;">{a["nome_cliente"]}</span>
          </div>
          <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
            {linha_processo}
            <tr><td style="padding:5px 0;color:#6b7280;font-size:12px;width:150px;">Seção do DOU</td><td style="padding:5px 0;font-size:13px;color:#374151;">{a["secao"]}</td></tr>
            <tr><td style="padding:5px 0;color:#6b7280;font-size:12px;">Data de publicação</td><td style="padding:5px 0;font-size:13px;color:#374151;">{a.get("data_publicacao", hoje)}</td></tr>
            <tr><td style="padding:5px 0;color:#6b7280;font-size:12px;vertical-align:top;">Assunto</td><td style="padding:5px 0;font-size:13px;font-weight:600;color:#0f172a;">{a["titulo"]}</td></tr>
          </table>
          <div style="background:#f8fafc;border-left:4px solid #16a34a;padding:12px 16px;border-radius:0 8px 8px 0;">
            <div style="font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px;">Trecho onde aparece</div>
            <div style="font-size:13.5px;color:#374151;line-height:1.7;">{trecho_html}</div>
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

    html = f"""<html>
    <body style="font-family:Arial,Helvetica,sans-serif;background:#f1f5f9;margin:0;padding:24px 16px;">
      <div style="max-width:660px;margin:auto;">
        <div style="background:#0f172a;border-radius:10px 10px 0 0;padding:0;">
          <div style="background:#16a34a;height:4px;border-radius:10px 10px 0 0;"></div>
          <div style="padding:22px 28px 20px;">
            <div style="display:flex;align-items:center;gap:10px;">
              <span style="font-size:22px;">📡</span>
              <div>
                <div style="color:#fff;font-size:18px;font-weight:700;line-height:1.2;">EMC Monitor</div>
                <div style="color:rgba(255,255,255,.55);font-size:12px;margin-top:2px;">Alerta do Diário Oficial da União</div>
              </div>
            </div>
            <div style="margin-top:14px;background:rgba(255,255,255,.06);border-radius:6px;padding:10px 14px;display:inline-block;">
              <span style="color:#4ade80;font-weight:600;font-size:14px;">{qtd} publicação{'ões' if qtd>1 else ''}</span>
              <span style="color:rgba(255,255,255,.6);font-size:13px;">&nbsp;encontrada{'s' if qtd>1 else ''} em {hoje}</span>
            </div>
          </div>
        </div>
        <div style="background:#f1f5f9;padding:20px 0;">{blocos_html}</div>
        <div style="text-align:center;padding:8px 0 24px;">
          <div style="font-size:11px;color:#94a3b8;">Enviado automaticamente pelo EMC Monitor · Busca diária às 6h00</div>
        </div>
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

def _registrar_busca_log(total_encontrados: int, sucesso: bool, observacao: str):
    """Registra a busca no banco de dados para o histórico."""
    if not DATABASE_URL:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO busca_log (tipo, origem, total_encontrados, sucesso, observacao, realizada_em)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            ("automatica", "github_actions", total_encontrados, sucesso, observacao, datetime.utcnow()),
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"Log de busca registrado no banco.")
    except Exception as exc:
        print(f"[AVISO] Falha ao registrar log de busca: {exc}")


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
        _registrar_busca_log(0, False, "Falha no login INLABS")
        exit(1)

    print(f"\nBuscando {len(CLIENTES)} clientes no DOU...")
    alertas = buscar_hoje(session)

    print(f"\nTotal encontrado: {len(alertas)} publicação(ões)")

    # Registra a busca no histórico
    _registrar_busca_log(
        total_encontrados=len(alertas),
        sucesso=True,
        observacao=f"Busca concluída. {len(alertas)} publicação(ões) encontrada(s)."
    )

    if alertas:
        print("Enviando e-mail...")
        ok = enviar_email(alertas)
        print("E-mail:", "ENVIADO" if ok else "FALHOU")
    else:
        print("Nenhum cliente apareceu no DOU hoje — nenhum e-mail enviado.")
