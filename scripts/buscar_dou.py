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
import sys
import re
import smtplib
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from zoneinfo import ZoneInfo
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
if not EMAIL_DESTINATARIOS:
    # Sem esta checagem, uma lista vazia ou mal formatada faria o robô rodar
    # a busca inteira e "enviar" para ninguém, terminando em verde.
    raise SystemExit(
        "ERRO: EMAIL_DESTINATARIOS está vazio ou mal formatado. "
        "Configure o secret com um ou mais e-mails separados por vírgula."
    )

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

# ── Estado de degradação ──────────────────────────────────────────────────────
# Quando o banco de dados não responde, o robô continua buscando usando a cópia
# local (clientes.json), mas isso é uma EMERGÊNCIA, não o funcionamento normal:
# os alertas não são gravados no histórico e o painel web não é atualizado.
# Estas variáveis garantem que essa situação apareça no e-mail e faça o run
# falhar (vermelho no GitHub), em vez de passar despercebida.
MODO_DEGRADADO = False
MOTIVO_DEGRADACAO = ""

# Guarda o erro real do servidor de e-mail. Sem isso, uma recusa de login do
# Gmail (senha de aplicativo revogada, por exemplo) só aparecia no texto do
# run e nunca chegava ao histórico do painel.
ULTIMO_ERRO_EMAIL = ""

# Duas situações diferentes que antes eram indistinguíveis de "dia tranquilo":
#
# SECOES_COM_FALHA  — não foi possível ACESSAR o DOU (rede fora, INLABS fora do
#                     ar, erro HTTP). É problema de verdade: pode haver
#                     publicação que passou batido. Dispara alarme.
#
# SECOES_INDISPONIVEIS — o DOU respondeu, mas aquela edição não existe (ainda
#                     não publicada, feriado). É situação normal, não é falha.
#                     Vira apenas informação, sem alarme falso.
SECOES_COM_FALHA: list[str] = []
SECOES_INDISPONIVEIS: list[str] = []

# O INLABS (sistema do governo) entra em manutenção de vez em quando.
# É indisponibilidade externa, não defeito do EMC Monitor — mas precisa
# aparecer, porque durante a manutenção nada é verificado.
INLABS_EM_MANUTENCAO = False


def hoje_brasil() -> date:
    """
    Data de hoje no horário de Brasília.

    Os servidores do GitHub rodam em UTC (3 horas à frente). No horário
    normal da busca (6h da manhã) as duas datas coincidem, mas fora dele
    o robô procurava o Diário do dia seguinte — que ainda não existe.
    """
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


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
            global MODO_DEGRADADO, MOTIVO_DEGRADACAO
            MODO_DEGRADADO = True
            MOTIVO_DEGRADACAO = str(exc)
            print("=" * 72)
            print("[ERRO GRAVE] Não foi possível conectar ao banco de dados.")
            print(f"  Motivo: {exc}")
            print("  O robô vai continuar usando a cópia local (clientes.json),")
            print("  mas ATENÇÃO: nesta condição os alertas NÃO são gravados no")
            print("  histórico e o painel web NÃO é atualizado. A cópia local")
            print("  também pode estar desatualizada em relação ao cadastro real.")
            print("  Este run será marcado como FALHA para que o problema apareça.")
            print("=" * 72)

    arquivo = Path(__file__).parent.parent / "clientes.json"
    clientes = json.loads(arquivo.read_text(encoding="utf-8"))
    for c in clientes:
        c.setdefault("id", None)
    print(f"Clientes carregados de clientes.json: {len(clientes)}")
    return clientes


def _salvar_alerta_db(cliente_id: int, resultado: dict) -> bool:
    """
    Salva um alerta no Supabase vinculado ao cliente, evitando duplicatas.

    Retorna True se o alerta é NOVO (foi gravado agora), False se já existia.

    Esse retorno é o que permite o robô rodar várias vezes por dia sem
    reenviar o mesmo alerta: só entram no e-mail os que voltaram True.
    Sem isso, três execuções matinais mandariam três e-mails idênticos.
    """
    if not DATABASE_URL or cliente_id is None:
        # Sem banco não há como saber o que já foi enviado. Trata como novo
        # para não perder alerta — é o comportamento seguro na emergência.
        return True
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM alertas_dou WHERE cliente_id = %s AND data_publicacao = %s AND titulo = %s",
            (cliente_id, resultado["data_publicacao"], resultado["titulo"][:500]),
        )
        ja_existe = cur.fetchone() is not None
        if not ja_existe:
            cur.execute(
                # email_enviado entra como FALSE: o alerta acabou de ser
                # encontrado e o e-mail ainda nem foi tentado. Só vira TRUE
                # em _marcar_alertas_enviados(), depois do envio confirmado.
                # Antes era gravado TRUE aqui, então o painel exibia
                # "e-mail enviado" mesmo quando nenhum e-mail saía.
                """INSERT INTO alertas_dou
                   (cliente_id, data_publicacao, secao, titulo, resumo, paragrafo, url,
                    termo_encontrado, email_enviado, encontrado_em)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s)""",
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
        return not ja_existe
    except Exception as exc:
        print(f"[AVISO] Erro ao salvar alerta no banco: {exc}")
        # Na dúvida, trata como novo: perder um alerta é pior que repetir um.
        return True


CLIENTES = _carregar_clientes()


# ── INLABS ────────────────────────────────────────────────────────────────────

def login_inlabs(tentativas: int = 3) -> requests.Session:
    """
    Faz login no INLABS, com novas tentativas em caso de falha de rede.

    O servidor do INLABS às vezes derruba a conexão ("RemoteDisconnected"),
    sobretudo em horário de pico. Antes isso encerrava o robô com um erro
    técnico e a busca do dia inteiro se perdia por causa de uma oscilação
    de alguns segundos.
    """
    s = requests.Session()
    for tentativa in range(1, tentativas + 1):
        try:
            resp = s.post(
                URL_LOGIN,
                data={"email": INLABS_EMAIL, "password": INLABS_SENHA},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            # Diagnóstico: sem isso, "login falhou" não distingue senha
            # errada de manutenção do sistema do governo.
            if not s.cookies.get("inlabs_session_cookie", ""):
                corpo = (resp.text or "")
                if "manuten" in corpo.lower() or resp.status_code in (502, 503):
                    global INLABS_EM_MANUTENCAO
                    INLABS_EM_MANUTENCAO = True
                    print("  O INLABS (sistema do governo que publica o Diário "
                          "Oficial) está EM MANUTENÇÃO no momento.")
                    print(f"  Isso é uma indisponibilidade do próprio governo "
                          f"(HTTP {resp.status_code}), não um problema do EMC Monitor.")
                else:
                    trecho = corpo[:200].replace("\n", " ").strip()
                    print(f"  [DIAGNÓSTICO] HTTP {resp.status_code} — resposta: {trecho!r}")
            break
        except requests.RequestException as exc:
            print(f"  [AVISO] Tentativa {tentativa}/{tentativas} de login "
                  f"falhou: {type(exc).__name__}")
            if tentativa == tentativas:
                print("  Não foi possível conectar ao INLABS.")
                return s
            time.sleep(10 * tentativa)   # 10s, depois 20s
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


def _marcar_alertas_enviados():
    """
    Marca como enviados os alertas desta execução, depois que o e-mail saiu
    de fato. Chamado apenas quando enviar_email() retorna True.
    """
    if not DATABASE_URL:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=15)
        cur = conn.cursor()
        cur.execute(
            "UPDATE alertas_dou SET email_enviado = TRUE "
            "WHERE email_enviado = FALSE AND encontrado_em::date = CURRENT_DATE"
        )
        atualizados = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        print(f"Marcados como enviados: {atualizados} alerta(s).")
    except Exception as exc:
        print(f"[AVISO] Falha ao marcar alertas como enviados: {exc}")


def _marcar_falha_secao(secao: str, motivo: str):
    """
    Registra, uma única vez por seção, que ela não pôde ser ACESSADA.

    Como cada seção é baixada uma vez por cliente (103 vezes), sem essa
    deduplicação a mesma falha apareceria centenas de vezes no aviso.
    """
    if not any(f.startswith(secao) for f in SECOES_COM_FALHA):
        SECOES_COM_FALHA.append(f"{secao} ({motivo})")


def _marcar_secao_indisponivel(secao: str):
    """Registra que a edição do dia não existe para essa seção (situação normal)."""
    if secao not in SECOES_INDISPONIVEIS:
        SECOES_INDISPONIVEIS.append(secao)


def _baixar_secao(session: requests.Session, cookie: str, dia: str, secao: str) -> list[bytes]:
    """
    Baixa uma seção do DOU e devolve o conteúdo dos XMLs de dentro do ZIP.

    Devolve lista vazia quando a edição não existe (feriado, ainda não
    publicada) ou quando houve falha de acesso — as duas situações ficam
    registradas em listas separadas, porque só a segunda é problema.
    """
    url = f"{URL_BASE}{dia}&dl={dia}-{secao}.zip"
    try:
        # Tenta até 3 vezes: o INLABS derruba conexões esporadicamente, e
        # perder uma seção inteira por causa disso significa perder
        # publicações sem saber.
        resp = None
        for tentativa in range(1, 4):
            try:
                resp = session.get(
                    url,
                    headers={"Cookie": f"inlabs_session_cookie={cookie}",
                             "origem": "736372697074"},
                    timeout=60,
                )
                break
            except requests.RequestException as exc:
                print(f"  [AVISO] {secao}: tentativa {tentativa}/3 falhou "
                      f"({type(exc).__name__})")
                if tentativa == 3:
                    raise
                time.sleep(10 * tentativa)

        if resp.status_code == 404:
            _marcar_secao_indisponivel(secao)
            return []
        if resp.status_code != 200:
            _marcar_falha_secao(secao, f"HTTP {resp.status_code}")
            return []

        # O INLABS responde 200 com uma página HTML quando a edição ainda não
        # saiu. Um ZIP de verdade sempre começa com "PK".
        if not resp.content.startswith(b"PK"):
            _marcar_secao_indisponivel(secao)
            return []

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            return [
                zf.read(nome) for nome in zf.namelist() if nome.endswith(".xml")
            ]
    except Exception as exc:
        print(f"  [AVISO] Erro ao baixar {secao}: {exc}")
        _marcar_falha_secao(secao, type(exc).__name__)
        return []


def buscar_hoje(session: requests.Session) -> list[dict]:
    """
    Baixa o DOU do dia UMA vez por seção e procura todos os clientes nele.

    Antes o download acontecia dentro do laço de clientes: as mesmas 3 seções
    eram baixadas e descompactadas uma vez para CADA cliente — 103 × 3 = 309
    downloads do Diário inteiro por execução. Era a causa dos 20-28 minutos
    de duração e um convite a bloqueio por excesso de requisições.

    Com o download fora do laço são 3 downloads, e a busca dos 103 clientes
    acontece em memória.
    """
    hoje = hoje_brasil().strftime("%Y-%m-%d")
    cookie = session.cookies.get("inlabs_session_cookie", "")
    if not cookie:
        print("ERRO: falha no login INLABS")
        return []

    # 1) Baixa cada seção uma única vez.
    print("Baixando as seções do DOU...")
    xmls_por_secao: dict[str, list[bytes]] = {}
    for secao in SECOES:
        arquivos = _baixar_secao(session, cookie, hoje, secao)
        if arquivos:
            xmls_por_secao[secao] = arquivos
            print(f"  {secao}: {len(arquivos)} arquivo(s)")
        else:
            print(f"  {secao}: indisponível")

    if not xmls_por_secao:
        return []

    # 2) Procura cada cliente no conteúdo já baixado.
    print(f"\nProcurando {len(CLIENTES)} clientes no conteúdo baixado...")
    todos = []
    for cliente in CLIENTES:
        nome = cliente["nome_cliente"]
        termo = cliente["termo_busca"]
        tipo = cliente["tipo"]
        cliente_id = cliente.get("id")
        encontrados = []

        for secao, arquivos in xmls_por_secao.items():
            for conteudo in arquivos:
                for item in buscar_em_xml(conteudo, termo, secao):
                    item["nome_cliente"] = nome
                    item["tipo"] = tipo
                    item["termo_busca"] = termo
                    # Só entra no e-mail se for alerta novo. É isso que permite
                    # rodar várias vezes de manhã, para pegar o DOU assim que
                    # sai, sem repetir o mesmo aviso.
                    if _salvar_alerta_db(cliente_id, item):
                        encontrados.append(item)

        if encontrados:
            print(f"  {nome}: {len(encontrados)} publicação(ões) NOVA(S)")
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
    hoje = hoje_brasil().strftime("%d/%m/%Y")
    qtd = len(alertas)
    clientes_nomes = list({a["nome_cliente"] for a in alertas})
    resumo = ", ".join(clientes_nomes[:3]) + (" e outros" if len(clientes_nomes) > 3 else "")
    prefixo = "[!] " if MODO_DEGRADADO else ""
    assunto = f"{prefixo}[EMC Monitor] Alerta DOU {hoje} — {qtd} publicação{'ões' if qtd > 1 else ''}: {resumo}"

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

    # Aviso de emergência: banco fora do ar. Precisa ser impossível de ignorar.
    if MODO_DEGRADADO:
        banner_degradado = f"""
        <div style="background:#fef2f2;border:2px solid #dc2626;border-radius:10px;padding:18px 22px;margin-bottom:18px;">
          <div style="font-size:15px;font-weight:700;color:#b91c1c;margin-bottom:8px;">
            ⚠️ Atenção: o sistema está funcionando em modo de emergência
          </div>
          <div style="font-size:13px;color:#7f1d1d;line-height:1.65;">
            O banco de dados não respondeu, então esta busca usou a <strong>cópia local
            de segurança</strong> da lista de clientes.<br><br>
            <strong>O que isso significa:</strong><br>
            • Esta busca foi feita e os resultados abaixo são válidos.<br>
            • Porém os alertas <strong>não foram gravados no histórico</strong>.<br>
            • O painel no site <strong>não foi atualizado</strong>.<br>
            • A lista usada pode estar desatualizada em relação ao seu cadastro.<br><br>
            <strong>Providência:</strong> é preciso restabelecer o banco de dados.
          </div>
        </div>"""
        linhas_txt.insert(0, "*** ATENCAO: MODO DE EMERGENCIA — banco de dados fora do ar. ***")
        linhas_txt.insert(1, "Alertas nao gravados no historico; painel web nao atualizado.")
        linhas_txt.insert(2, "=" * 60)
    else:
        banner_degradado = ""

    html = f"""<html>
    <body style="font-family:Arial,Helvetica,sans-serif;background:#f1f5f9;margin:0;padding:24px 16px;">
      <div style="max-width:660px;margin:auto;">
        {banner_degradado}
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
        # timeout obrigatório: sem ele, uma conexão travada pendura o robô
        # até o limite do GitHub Actions (horas), sem enviar nada.
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(EMAIL_REMETENTE, EMAIL_SENHA)
            srv.sendmail(EMAIL_REMETENTE, EMAIL_DESTINATARIOS, msg.as_string())
        return True
    except Exception as exc:
        global ULTIMO_ERRO_EMAIL
        ULTIMO_ERRO_EMAIL = f"{type(exc).__name__}: {exc}"
        print(f"ERRO ao enviar e-mail: {exc}")
        return False


def enviar_aviso_falha() -> bool:
    """
    Envia aviso quando o banco está fora do ar e não houve publicações.
    Sem isso, um dia sem publicações e um dia com o sistema quebrado
    seriam indistinguíveis: nos dois casos não chegaria e-mail nenhum.
    """
    hoje = hoje_brasil().strftime("%d/%m/%Y")
    assunto = f"[!] [EMC Monitor] Sistema em emergência — {hoje}"

    if MODO_DEGRADADO:
        titulo_falha = "O banco de dados não respondeu"
        detalhe = MOTIVO_DEGRADACAO
        explicacao_html = (
            "A busca no Diário Oficial foi feita normalmente e "
            "<strong>nenhuma publicação</strong> foi encontrada.<br><br>"
            "Porém o banco de dados está fora do ar. Por isso a lista de clientes "
            "usada foi a <strong>cópia local de segurança</strong>, que pode não "
            "refletir o cadastro atual."
        )
        providencia = "restabelecer o banco de dados."
    else:
        titulo_falha = "Parte do Diário Oficial não pôde ser lida"
        detalhe = ", ".join(SECOES_COM_FALHA)
        explicacao_html = (
            "A busca foi executada, mas <strong>uma ou mais seções do Diário "
            "Oficial não puderam ser baixadas</strong>.<br><br>"
            "Nenhuma publicação foi encontrada nas seções que funcionaram — mas "
            "como parte do Diário não foi lida, <strong>pode haver publicações "
            "não detectadas hoje</strong>."
        )
        providencia = "verificar manualmente o Diário Oficial de hoje."

    texto = (
        f"EMC Monitor — aviso de falha ({hoje})\n"
        f"{'=' * 60}\n\n"
        f"{titulo_falha.upper()}\n\n"
        f"Detalhe tecnico: {detalhe}\n\n"
        f"Providencia: {providencia}\n"
    )

    html = f"""<html>
    <body style="font-family:Arial,Helvetica,sans-serif;background:#f1f5f9;margin:0;padding:24px 16px;">
      <div style="max-width:660px;margin:auto;">
        <div style="background:#0f172a;border-radius:10px 10px 0 0;">
          <div style="background:#dc2626;height:4px;border-radius:10px 10px 0 0;"></div>
          <div style="padding:22px 28px 20px;">
            <div style="color:#fff;font-size:18px;font-weight:700;">📡 EMC Monitor</div>
            <div style="color:rgba(255,255,255,.55);font-size:12px;margin-top:2px;">Aviso de falha do sistema</div>
          </div>
        </div>
        <div style="background:#fff;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 10px 10px;padding:24px 28px;">
          <div style="font-size:15px;font-weight:700;color:#b91c1c;margin-bottom:12px;">
            ⚠️ {titulo_falha}
          </div>
          <div style="font-size:13.5px;color:#374151;line-height:1.75;">
            Data da busca: <strong>{hoje}</strong>.<br><br>
            {explicacao_html}<br><br>
            Você está recebendo este aviso para que um dia sem publicações não seja
            confundido com um sistema quebrado.<br><br>
            <strong>Providência:</strong> {providencia}
          </div>
          <div style="background:#f8fafc;border-left:4px solid #dc2626;padding:12px 16px;border-radius:0 8px 8px 0;margin-top:18px;">
            <div style="font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;">Detalhe técnico</div>
            <div style="font-size:12px;color:#6b7280;font-family:monospace;word-break:break-all;">{detalhe}</div>
          </div>
        </div>
        <div style="text-align:center;padding:14px 0 24px;">
          <div style="font-size:11px;color:#94a3b8;">Enviado automaticamente pelo EMC Monitor</div>
        </div>
      </div>
    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = EMAIL_REMETENTE
        msg["To"] = ", ".join(EMAIL_DESTINATARIOS)
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        # timeout obrigatório: sem ele, uma conexão travada pendura o robô
        # até o limite do GitHub Actions (horas), sem enviar nada.
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(EMAIL_REMETENTE, EMAIL_SENHA)
            srv.sendmail(EMAIL_REMETENTE, EMAIL_DESTINATARIOS, msg.as_string())
        return True
    except Exception as exc:
        global ULTIMO_ERRO_EMAIL
        ULTIMO_ERRO_EMAIL = f"{type(exc).__name__}: {exc}"
        print(f"ERRO ao enviar aviso de falha: {exc}")
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


def autoteste() -> int:
    """
    Verificação do sistema sem fazer a busca completa (que leva ~25 minutos).

    Confere, em ordem, as três coisas que precisam funcionar para o alerta
    chegar na caixa de entrada:
      1. o banco de dados responde e a lista de clientes está lá;
      2. o login no INLABS (assinatura do Diário Oficial) funciona;
      3. o envio de e-mail funciona de verdade — manda uma mensagem de teste.

    Retorna 0 se está tudo certo, 1 se alguma etapa falhou.
    """
    print("=" * 72)
    print("AUTOTESTE DO EMC MONITOR")
    print("=" * 72)
    problemas = []

    # 1. Banco de dados e lista de clientes
    print("\n[1/3] Banco de dados e lista de clientes")
    clientes = _carregar_clientes()
    if MODO_DEGRADADO:
        problemas.append(f"banco de dados nao respondeu ({MOTIVO_DEGRADACAO})")
        print(f"      FALHOU — usando copia local com {len(clientes)} cliente(s)")
    else:
        print(f"      OK — {len(clientes)} termo(s) carregado(s) do banco")
        if len(clientes) < 50:
            problemas.append(
                f"apenas {len(clientes)} clientes carregados — esperado ~103"
            )

    # 2. Login no INLABS
    print("\n[2/3] Acesso ao Diario Oficial (INLABS)")
    try:
        sessao = login_inlabs()
        cookie = sessao.cookies.get("inlabs_session_cookie", "")
        if cookie:
            print("      OK — login realizado")
        else:
            problemas.append("login no INLABS falhou (usuario ou senha)")
            print("      FALHOU — sem cookie de sessao")
    except Exception as exc:
        problemas.append(f"erro ao acessar o INLABS: {exc}")
        print(f"      FALHOU — {exc}")

    # 3. Envio de e-mail — exercita o caminho real, com uma publicacao ficticia
    print("\n[3/3] Envio de e-mail")
    print(f"      Destinatarios: {', '.join(EMAIL_DESTINATARIOS)}")
    exemplo = [{
        "nome_cliente": "TESTE DO SISTEMA — nenhum cliente real",
        "tipo": "nome",
        "termo_busca": "teste",
        "secao": "DO1",
        "titulo": "Mensagem de teste do EMC Monitor",
        "data_publicacao": hoje_brasil().strftime("%d/%m/%Y"),
        "paragrafo": (
            "Esta e uma mensagem de teste enviada manualmente para verificar "
            "se o sistema de alertas esta funcionando. Nenhuma publicacao real "
            "do Diario Oficial foi encontrada. RESOLVE: se voce recebeu este "
            "e-mail, o envio de alertas esta operacional. Art. 1o Nenhuma acao "
            "e necessaria da sua parte."
        ),
        "url": "https://www.in.gov.br/",
    }]
    if enviar_email(exemplo):
        print("      OK — e-mail de teste enviado")
    else:
        problemas.append(f"envio de e-mail falhou: {ULTIMO_ERRO_EMAIL}")
        print(f"      FALHOU — {ULTIMO_ERRO_EMAIL}")

    # Resultado
    print("\n" + "=" * 72)
    if problemas:
        print(f"AUTOTESTE FALHOU — {len(problemas)} problema(s):")
        for p in problemas:
            print(f"  - {p}")
        print("=" * 72)
        _registrar_busca_log(0, False, f"Autoteste falhou: {'; '.join(problemas)}")
        return 1

    print("AUTOTESTE PASSOU — banco, Diario Oficial e e-mail estao funcionando.")
    print("Confira a caixa de entrada: deve ter chegado um e-mail de teste.")
    print("=" * 72)
    _registrar_busca_log(0, True, "Autoteste do sistema: banco, INLABS e e-mail OK.")
    return 0


if __name__ == "__main__":
    if "--autoteste" in sys.argv:
        sys.exit(autoteste())

    hoje = hoje_brasil()
    print(f"EMC Monitor — Busca DOU {hoje.strftime('%d/%m/%Y')}")
    print(f"Dia da semana: {hoje.strftime('%A')}")
    print(f"Banco de dados: {'Supabase' if DATABASE_URL else 'clientes.json (sem banco)'}")
    print()

    session = login_inlabs()
    cookie = session.cookies.get("inlabs_session_cookie", "")
    print(f"Login INLABS: {'OK' if cookie else 'FALHOU'}")
    if not cookie:
        _registrar_busca_log(
            0, False,
            "Diário Oficial (INLABS) em manutenção — indisponibilidade do "
            "governo, nada pôde ser verificado hoje."
            if INLABS_EM_MANUTENCAO
            else "Falha no login INLABS — verificar usuário e senha."
        )
        exit(1)

    print(f"\nBuscando {len(CLIENTES)} clientes no DOU...")
    alertas = buscar_hoje(session)

    print(f"\nTotal encontrado: {len(alertas)} publicação(ões)")

    if SECOES_INDISPONIVEIS:
        print(f"[INFO] Edição do dia ainda não publicada para: "
              f"{', '.join(SECOES_INDISPONIVEIS)} — situação normal em feriado "
              f"ou fora do horário de publicação.")
    if SECOES_COM_FALHA:
        print(f"[ATENÇÃO] {len(SECOES_COM_FALHA)} seção(ões) do DOU não puderam "
              f"ser lidas: {', '.join(SECOES_COM_FALHA)}")

    # Se NENHUMA seção existe, o Diário simplesmente não saiu hoje. Não é
    # falha — mas também não dá para afirmar que não há publicações, então
    # o dia é registrado como "não verificado".
    dou_nao_publicado = (
        len(SECOES_INDISPONIVEIS) == len(SECOES) and not SECOES_COM_FALHA
    )

    # O envio vem ANTES do registro no histórico, de propósito.
    # Antes o log era gravado com sucesso=True antes de tentar enviar: se o
    # Gmail recusasse o envio, o painel mostrava a busca como bem-sucedida e
    # o erro real desaparecia.
    email_ok = True
    if alertas:
        print("Enviando e-mail...")
        email_ok = enviar_email(alertas)
        print("E-mail:", "ENVIADO" if email_ok else "FALHOU")
        if email_ok:
            _marcar_alertas_enviados()
    elif MODO_DEGRADADO or SECOES_COM_FALHA:
        print("Nenhuma publicação encontrada, mas houve falha no sistema — enviando aviso.")
        email_ok = enviar_aviso_falha()
        print("E-mail de aviso:", "ENVIADO" if email_ok else "FALHOU")
    elif dou_nao_publicado:
        print("O Diário Oficial de hoje não foi publicado — nada a verificar.")
    else:
        print("Nenhum cliente apareceu no DOU hoje — nenhum e-mail enviado.")

    # Agora sim, o histórico registra o que realmente aconteceu.
    tudo_certo = (not MODO_DEGRADADO) and email_ok and not SECOES_COM_FALHA
    if not email_ok:
        observacao = f"FALHA NO ENVIO DO E-MAIL: {ULTIMO_ERRO_EMAIL}"
    elif MODO_DEGRADADO:
        observacao = f"MODO DE EMERGÊNCIA (banco fora do ar): {MOTIVO_DEGRADACAO}"
    elif SECOES_COM_FALHA:
        observacao = ("Busca incompleta — não foi possível ler: "
                      f"{', '.join(SECOES_COM_FALHA)}")
    elif dou_nao_publicado:
        observacao = "Diário Oficial não publicado hoje — nenhuma verificação possível."
    else:
        observacao = f"Busca concluída. {len(alertas)} publicação(ões) encontrada(s)."

    _registrar_busca_log(
        total_encontrados=len(alertas),
        sucesso=tudo_certo,
        observacao=observacao,
    )

    # O run precisa ficar VERMELHO no GitHub quando algo deu errado.
    # Antes, uma falha de envio ou de banco terminava com código 0 (verde),
    # dando a impressão de que estava tudo certo.
    if MODO_DEGRADADO:
        print("\n[FALHA] Run concluído em MODO DE EMERGÊNCIA — o banco de dados não respondeu.")
        print("        Restabeleça o banco para voltar ao funcionamento normal.")
        exit(1)
    if not email_ok:
        print("\n[FALHA] A busca funcionou, mas o e-mail não pôde ser enviado.")
        print(f"        Erro do servidor de e-mail: {ULTIMO_ERRO_EMAIL}")
        exit(1)
    if SECOES_COM_FALHA:
        print(f"\n[FALHA] A busca foi incompleta: não foi possível ler "
              f"{', '.join(SECOES_COM_FALHA)} do DOU.")
        print("        Pode haver publicações não detectadas hoje.")
        exit(1)
