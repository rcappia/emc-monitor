"""
Integração com o Diário Oficial da União via INLABS (Imprensa Nacional).
Baixa os arquivos XML do dia, busca os termos e extrai parágrafos completos.
"""
import io
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
import requests
from datetime import date, timedelta

INLABS_EMAIL = "rcappia@gmail.com"
INLABS_SENHA = "emc@2026"
URL_LOGIN = "https://inlabs.in.gov.br/logar.php"
URL_BASE = "https://inlabs.in.gov.br/index.php?p="

SECOES = ["DO1", "DO2", "DO3"]

_session: requests.Session = None


def _get_session() -> requests.Session:
    """Faz login no INLABS e retorna sessão autenticada."""
    global _session
    if _session and _session.cookies.get("inlabs_session_cookie"):
        return _session

    s = requests.Session()
    s.post(
        URL_LOGIN,
        data={"email": INLABS_EMAIL, "password": INLABS_SENHA},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    _session = s
    return s


def buscar_no_dou(termo: str, data_ini: str = None, data_fim: str = None) -> list[dict]:
    """
    Busca um termo no DOU via INLABS.

    Args:
        termo: texto a buscar (nome do cliente ou número do processo)
        data_ini: data no formato dd-MM-yyyy (padrão: hoje)
        data_fim: ignorado — busca sempre um dia por vez
    """
    if not data_ini:
        data_alvo = date.today()
    else:
        # converte dd-MM-yyyy → date
        partes = data_ini.split("-")
        data_alvo = date(int(partes[2]), int(partes[1]), int(partes[0]))

    data_str = data_alvo.strftime("%Y-%m-%d")   # formato INLABS: 2026-03-22
    resultados = []

    session = _get_session()
    cookie = session.cookies.get("inlabs_session_cookie", "")

    if not cookie:
        print("[dou_api] Falha no login INLABS — verifique e-mail e senha.")
        return resultados

    for secao in SECOES:
        url_arquivo = f"{URL_BASE}{data_str}&dl={data_str}-{secao}.zip"
        try:
            resp = session.get(
                url_arquivo,
                headers={
                    "Cookie": f"inlabs_session_cookie={cookie}",
                    "origem": "736372697074",
                },
                timeout=60,
            )
            if resp.status_code == 404:
                continue  # seção não publicada hoje (ex: feriado)
            if resp.status_code != 200:
                print(f"[dou_api] HTTP {resp.status_code} ao baixar {secao}")
                continue

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for nome_arquivo in zf.namelist():
                    if not nome_arquivo.endswith(".xml"):
                        continue
                    with zf.open(nome_arquivo) as f:
                        xml_bytes = f.read()
                    itens = _buscar_em_xml(xml_bytes, termo, secao)
                    resultados.extend(itens)

        except Exception as exc:
            print(f"[dou_api] Erro ao processar {secao}: {exc}")
            continue

    return resultados


def _normalizar(texto: str) -> str:
    """Remove acentos e converte para minúsculas para comparação."""
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii").lower()


def _buscar_em_xml(xml_bytes: bytes, termo: str, secao: str) -> list[dict]:
    """Procura o termo no XML do DOU e retorna as matérias onde ele aparece."""
    resultados = []
    termo_norm = _normalizar(termo)

    try:
        root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return resultados

    for article in root.iter("article"):
        # Extrai todo o texto da matéria para verificar se o termo está presente
        texto_completo = _texto_do_elemento(article)
        if termo_norm not in _normalizar(texto_completo):
            continue

        # Campos principais
        titulo = _pegar_texto(article, ["body/Titulo", "body/Identifica", "body/Ementa"])
        texto_body = _texto_do_elemento(article.find("body/Texto") or article)

        pub_date = article.get("pubDate", "")   # formato: dd/MM/yyyy
        pdf_page = article.get("pdfPage", "")

        texto_para_extrair = texto_body or texto_completo
        paragrafo = _extrair_paragrafo(texto_para_extrair, termo)
        processo_dou = _extrair_processo_dou(texto_para_extrair, termo)

        resultados.append({
            "titulo": (titulo or f"Publicação {secao}")[:500],
            "resumo": (texto_body or texto_completo)[:300],
            "paragrafo": paragrafo,
            "processo_dou": processo_dou,
            "url": pdf_page,
            "data_publicacao": pub_date,
            "secao": f"Seção {secao[-1]}",
        })

    return resultados


def _pegar_texto(article, caminhos: list[str]) -> str:
    """Tenta cada caminho e retorna o primeiro texto não vazio."""
    for caminho in caminhos:
        elem = article.find(caminho)
        if elem is not None:
            t = _texto_do_elemento(elem).strip()
            if t:
                return t
    return ""


def _texto_do_elemento(elem) -> str:
    """Extrai todo o texto de um elemento XML e seus filhos, removendo HTML."""
    if elem is None:
        return ""
    partes = []
    for e in elem.iter():
        if e.text and e.text.strip():
            partes.append(_limpar_html(e.text.strip()))
        if e.tail and e.tail.strip():
            partes.append(_limpar_html(e.tail.strip()))
    return " ".join(p for p in partes if p)


def _limpar_html(texto: str) -> str:
    """Remove tags HTML e normaliza espaços."""
    if not texto:
        return ""
    sem_tags = re.sub(r"<[^>]+>", " ", texto)
    sem_entidades = (sem_tags
        .replace("&nbsp;", " ").replace("&amp;", "&")
        .replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", sem_entidades).strip()


def _extrair_processo_dou(texto: str, termo: str) -> str:
    """
    Extrai o número de processo ANATEL/MCOM que aparece próximo ao termo no DOU.
    Formato típico: 53500.000123/2026-00
    """
    pos = _normalizar(texto).find(_normalizar(termo))
    if pos == -1:
        return ""
    # O processo aparece ANTES do nome do cliente no DOU — busca nos 300 chars antes
    trecho_antes = texto[max(0, pos - 300): pos]
    # Padrão de processo ANATEL/MCOM: XXXXX.XXXXXX/XXXX-XX
    # Pega o último (mais próximo do nome)
    matches = re.findall(r'\d{5,6}\.\d{6}/\d{4}-\d{2}', trecho_antes)
    return matches[-1] if matches else ""


def _extrair_paragrafo(texto: str, termo: str) -> str:
    """
    Retorna o trecho do texto onde o termo aparece,
    com contexto suficiente para entender a publicação.
    Funciona mesmo quando o DOU publica sem acentuação.
    """
    if not texto:
        return ""

    if len(texto) <= 1500:
        return texto

    # Busca sem acento para encontrar mesmo quando DOU escreve diferente
    pos = _normalizar(texto).find(_normalizar(termo))
    if pos == -1:
        return texto[:800] + "..."

    inicio = max(0, pos - 200)
    fim = min(len(texto), pos + 600)

    if inicio > 0:
        espaco = texto.rfind(" ", 0, inicio)
        if espaco != -1:
            inicio = espaco + 1
    if fim < len(texto):
        espaco = texto.find(" ", fim)
        if espaco != -1:
            fim = espaco

    trecho = texto[inicio:fim].strip()
    if inicio > 0:
        trecho = "..." + trecho
    if fim < len(texto):
        trecho = trecho + "..."

    return trecho


# ── Atalhos ──────────────────────────────────────────────────────────────────

def buscar_hoje(termo: str) -> list[dict]:
    """Atalho para buscar publicações de hoje."""
    return buscar_no_dou(termo)


def buscar_ultimos_dias(termo: str, dias: int = 3) -> list[dict]:
    """Busca publicações dos últimos N dias."""
    hoje = date.today()
    resultados = []
    for i in range(dias):
        d = (hoje - timedelta(days=i)).strftime("%d-%m-%Y")
        resultados.extend(buscar_no_dou(termo, d))
    return resultados
