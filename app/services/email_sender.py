"""Envio de e-mail via Gmail SMTP."""
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def enviar_alertas_dou(
    remetente: str,
    senha: str,
    destinatarios: list[str],
    alertas: list[dict],
) -> bool:
    if not alertas:
        return True

    hoje = date.today().strftime("%d/%m/%Y")
    qtd = len(alertas)
    clientes = list({a["nome_cliente"] for a in alertas})
    nomes_resumo = ", ".join(clientes[:3]) + (" e outros" if len(clientes) > 3 else "")

    assunto = f"[EMC Monitor] Alerta DOU {hoje} — {qtd} publicação{'ões' if qtd > 1 else ''}: {nomes_resumo}"

    html  = _montar_html(alertas, hoje)
    texto = _montar_texto(alertas, hoje)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"]    = remetente
        msg["To"]      = ", ".join(destinatarios)
        msg.attach(MIMEText(texto, "plain",  "utf-8"))
        msg.attach(MIMEText(html,  "html",   "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(remetente, senha)
            server.sendmail(remetente, destinatarios, msg.as_string())

        return True
    except Exception as exc:
        print(f"[email_sender] Erro ao enviar e-mail: {exc}")
        return False


# ─── Formatação do trecho do DOU ──────────────────────────────────────────────

def _formatar_paragrafo(texto: str) -> str:
    """
    Separa visualmente o trecho do DOU no e-mail HTML.
    Insere quebras antes de elementos estruturais comuns em atos oficiais.
    """
    if not texto:
        return "(texto não disponível)"

    # Quebra dupla antes de palavras-chave que iniciam novos blocos
    for palavra in [
        "RESOLVE", "CONSIDERANDO", "DETERMINA", "AUTORIZA",
        "RATIFICA", "HOMOLOGA", "TORNA PÚBLICO",
    ]:
        texto = re.sub(
            rf'(?<=[.;,])\s+({palavra})',
            rf'<br><br>\1',
            texto,
        )

    # Quebra dupla antes de artigos: Art. 1º, Art. 2º ...
    texto = re.sub(r'\s+(Art\.\s*\d)', r'<br><br>\1', texto)

    # Quebra simples antes de parágrafos: § 1º, § 2º ...
    texto = re.sub(r'\s+(§\s*\d)', r'<br>\1', texto)

    # Quebra simples antes de alíneas romanas: I -, II -, III -, IV - ...
    texto = re.sub(r'\s+([IVX]+\s*[-–]\s)', r'<br>\1', texto)

    # Quebra antes de "Parágrafo único"
    texto = re.sub(r'\s+(Parágrafo\s+único)', r'<br><br>\1', texto, flags=re.IGNORECASE)

    return texto.strip()


# ─── Corpo texto simples ───────────────────────────────────────────────────────

def _montar_texto(alertas: list[dict], hoje: str) -> str:
    linhas = [
        "EMC Monitor — Alertas do Diário Oficial da União",
        f"Data: {hoje}",
        f"Total de publicações encontradas: {len(alertas)}",
        "=" * 60,
    ]
    for i, a in enumerate(alertas, 1):
        linhas += [f"\n[{i}] CLIENTE: {a.get('nome_cliente', '')}"]
        if a.get("tipo") == "processo":
            linhas.append(f"    Nº PROCESSO: {a.get('termo_busca', '')}")
        elif a.get("processo_dou"):
            linhas.append(f"    PROCESSO NO DOU: {a.get('processo_dou', '')}")
        linhas += [
            f"    SEÇÃO DO DOU: {a.get('secao', '')}",
            f"    DATA: {a.get('data_publicacao', hoje)}",
            f"    ASSUNTO: {a.get('titulo', '')}",
            f"    TRECHO:",
            f"    {a.get('paragrafo', a.get('resumo', '(sem texto disponível)'))}",
        ]
        if a.get("url"):
            linhas.append(f"    LINK: {a.get('url')}")
        linhas.append("-" * 60)
    linhas.append("\nAcesse o painel EMC Monitor para ver todos os detalhes.")
    return "\n".join(linhas)


# ─── Corpo HTML ───────────────────────────────────────────────────────────────

def _montar_html(alertas: list[dict], hoje: str) -> str:
    blocos = ""
    for a in alertas:

        # Badge tipo
        if a.get("tipo") == "processo":
            badge = '<span style="background:#fef9c3;color:#b45309;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">PROCESSO</span>'
        else:
            badge = '<span style="background:#dcfce7;color:#15803d;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">NOME</span>'

        # Linha de processo
        if a.get("tipo") == "processo":
            linha_processo = f"""
            <tr>
              <td style="padding:5px 0;color:#6b7280;font-size:12px;width:150px;">Nº do Processo</td>
              <td style="padding:5px 0;font-weight:600;font-family:monospace;font-size:13px;">{a.get('termo_busca','')}</td>
            </tr>"""
        elif a.get("processo_dou"):
            linha_processo = f"""
            <tr>
              <td style="padding:5px 0;color:#6b7280;font-size:12px;width:150px;">Processo no DOU</td>
              <td style="padding:5px 0;font-weight:600;font-family:monospace;font-size:13px;">{a.get('processo_dou','')}</td>
            </tr>"""
        else:
            linha_processo = ""

        # Trecho formatado com quebras de parágrafo
        trecho_html = _formatar_paragrafo(
            a.get("paragrafo") or a.get("resumo") or ""
        )

        # Botão link
        if a.get("url"):
            link_btn = f'''<a href="{a['url']}"
              style="display:inline-block;margin-top:12px;background:#16a34a;color:#fff;
                     padding:8px 18px;border-radius:6px;text-decoration:none;
                     font-size:13px;font-weight:600;">
              Ver publicação no DOU ↗
            </a>'''
        else:
            link_btn = ""

        blocos += f"""
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;
                    padding:22px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.05);">

          <!-- Cabeçalho do card -->
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;
                      padding-bottom:14px;border-bottom:1px solid #f1f5f9;">
            {badge}
            <span style="font-size:17px;font-weight:700;color:#0f172a;">
              {a.get('nome_cliente','')}
            </span>
          </div>

          <!-- Informações -->
          <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
            {linha_processo}
            <tr>
              <td style="padding:5px 0;color:#6b7280;font-size:12px;width:150px;">Seção do DOU</td>
              <td style="padding:5px 0;font-size:13px;color:#374151;">{a.get('secao','')}</td>
            </tr>
            <tr>
              <td style="padding:5px 0;color:#6b7280;font-size:12px;">Data de publicação</td>
              <td style="padding:5px 0;font-size:13px;color:#374151;">{a.get('data_publicacao', hoje)}</td>
            </tr>
            <tr>
              <td style="padding:5px 0;color:#6b7280;font-size:12px;vertical-align:top;">Assunto</td>
              <td style="padding:5px 0;font-size:13px;font-weight:600;color:#0f172a;">{a.get('titulo','')}</td>
            </tr>
          </table>

          <!-- Trecho do DOU -->
          <div style="background:#f8fafc;border-left:4px solid #16a34a;
                      padding:12px 16px;border-radius:0 8px 8px 0;">
            <div style="font-size:10px;color:#94a3b8;font-weight:600;
                        text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px;">
              Trecho onde aparece
            </div>
            <div style="font-size:13.5px;color:#374151;line-height:1.7;">
              {trecho_html}
            </div>
          </div>

          {link_btn}
        </div>
        """

    return f"""
    <html>
    <body style="font-family:Arial,Helvetica,sans-serif;background:#f1f5f9;margin:0;padding:24px 16px;">
      <div style="max-width:660px;margin:auto;">

        <!-- Cabeçalho -->
        <div style="background:#0f172a;border-radius:10px 10px 0 0;padding:0;">
          <div style="background:#16a34a;height:4px;border-radius:10px 10px 0 0;"></div>
          <div style="padding:22px 28px 20px;">
            <div style="display:flex;align-items:center;gap:10px;">
              <span style="font-size:22px;">📡</span>
              <div>
                <div style="color:#fff;font-size:18px;font-weight:700;line-height:1.2;">EMC Monitor</div>
                <div style="color:rgba(255,255,255,.55);font-size:12px;margin-top:2px;">
                  Alerta do Diário Oficial da União
                </div>
              </div>
            </div>
            <div style="margin-top:14px;background:rgba(255,255,255,.06);border-radius:6px;
                        padding:10px 14px;display:inline-block;">
              <span style="color:#4ade80;font-weight:600;font-size:14px;">
                {len(alertas)} publicação{'ões' if len(alertas)>1 else ''}
              </span>
              <span style="color:rgba(255,255,255,.6);font-size:13px;">
                &nbsp;encontrada{'s' if len(alertas)>1 else ''} em {hoje}
              </span>
            </div>
          </div>
        </div>

        <!-- Cards de alertas -->
        <div style="background:#f1f5f9;padding:20px 0;">
          {blocos}
        </div>

        <!-- Rodapé -->
        <div style="text-align:center;padding:8px 0 24px;">
          <div style="font-size:11px;color:#94a3b8;">
            Enviado automaticamente pelo EMC Monitor · Busca diária às 6h00
          </div>
        </div>

      </div>
    </body>
    </html>
    """
