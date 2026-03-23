"""Envio de e-mail via SMTP Outlook / Microsoft 365."""
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
    """
    Envia e-mail com os alertas do DOU.

    Cada alerta deve ter:
      - nome_cliente: nome do cliente
      - tipo: "nome" ou "processo"
      - termo_busca: o termo que foi buscado (nome ou nº processo)
      - secao: seção do DOU onde apareceu
      - titulo: título da publicação
      - paragrafo: trecho completo onde o cliente/processo apareceu
      - url: link para a publicação original
      - data_publicacao: data da publicação

    Returns True se enviado com sucesso.
    """
    if not alertas:
        return True

    hoje = date.today().strftime("%d/%m/%Y")
    qtd = len(alertas)
    clientes = list({a["nome_cliente"] for a in alertas})
    nomes_resumo = ", ".join(clientes[:3]) + (" e outros" if len(clientes) > 3 else "")

    assunto = f"[EMC Monitor] Alerta DOU {hoje} — {qtd} publicação{'ões' if qtd > 1 else ''}: {nomes_resumo}"

    html = _montar_html(alertas, hoje)
    texto = _montar_texto(alertas, hoje)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = remetente
        msg["To"] = ", ".join(destinatarios)
        msg.attach(MIMEText(texto, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(remetente, senha)
            server.sendmail(remetente, destinatarios, msg.as_string())

        return True
    except Exception as exc:
        print(f"[email_sender] Erro ao enviar e-mail: {exc}")
        return False


def _montar_texto(alertas: list[dict], hoje: str) -> str:
    """Versão texto simples do e-mail (para quem não suporta HTML)."""
    linhas = [
        f"EMC Monitor — Alertas do Diário Oficial da União",
        f"Data: {hoje}",
        f"Total de publicações encontradas: {len(alertas)}",
        "=" * 60,
    ]
    for i, a in enumerate(alertas, 1):
        linhas += [
            f"\n[{i}] CLIENTE: {a.get('nome_cliente', '')}",
        ]
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
    linhas.append("\nAcesse o sistema para ver todos os detalhes.")
    return "\n".join(linhas)


def _montar_html(alertas: list[dict], hoje: str) -> str:
    """Versão HTML formatada do e-mail."""
    blocos = ""
    for a in alertas:
        tipo_badge = (
            f'<span style="background:#f59e0b;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">Processo</span>'
            if a.get("tipo") == "processo"
            else f'<span style="background:#3b82f6;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">Nome</span>'
        )

        numero_processo = ""
        if a.get("tipo") == "processo":
            numero_processo = f"""
            <tr>
              <td style="padding:4px 0;color:#6b7280;font-size:13px;width:140px;">Nº do Processo</td>
              <td style="padding:4px 0;font-weight:600;font-family:monospace;">{a.get('termo_busca','')}</td>
            </tr>"""
        elif a.get("processo_dou"):
            numero_processo = f"""
            <tr>
              <td style="padding:4px 0;color:#6b7280;font-size:13px;width:140px;">Processo no DOU</td>
              <td style="padding:4px 0;font-weight:600;font-family:monospace;">{a.get('processo_dou','')}</td>
            </tr>"""

        paragrafo = a.get("paragrafo") or a.get("resumo") or "(texto não disponível)"
        url = a.get("url", "")
        link_btn = f'<a href="{url}" style="display:inline-block;margin-top:10px;background:#003087;color:#fff;padding:7px 16px;border-radius:6px;text-decoration:none;font-size:13px;">Ver publicação no DOU</a>' if url else ""

        blocos += f"""
        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:20px;margin-bottom:20px;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;">
            {tipo_badge}
            <span style="font-size:18px;font-weight:700;color:#111827;">{a.get('nome_cliente','')}</span>
          </div>
          <table style="width:100%;border-collapse:collapse;margin-bottom:14px;">
            {numero_processo}
            <tr>
              <td style="padding:4px 0;color:#6b7280;font-size:13px;width:140px;">Seção do DOU</td>
              <td style="padding:4px 0;">{a.get('secao','')}</td>
            </tr>
            <tr>
              <td style="padding:4px 0;color:#6b7280;font-size:13px;">Data</td>
              <td style="padding:4px 0;">{a.get('data_publicacao', hoje)}</td>
            </tr>
            <tr>
              <td style="padding:4px 0;color:#6b7280;font-size:13px;vertical-align:top;">Assunto</td>
              <td style="padding:4px 0;font-weight:600;">{a.get('titulo','')}</td>
            </tr>
          </table>

          <div style="background:#f9fafb;border-left:4px solid #003087;padding:12px 16px;border-radius:0 6px 6px 0;">
            <div style="font-size:11px;color:#6b7280;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">Trecho onde aparece</div>
            <div style="font-size:14px;color:#374151;line-height:1.6;">{paragrafo}</div>
          </div>
          {link_btn}
        </div>
        """

    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px;">
      <div style="max-width:680px;margin:auto;">

        <!-- Cabeçalho -->
        <div style="background:#003087;color:#fff;padding:20px 24px;border-radius:8px 8px 0 0;">
          <div style="font-size:20px;font-weight:700;">📡 EMC Monitor — Alerta DOU</div>
          <div style="opacity:0.75;font-size:13px;margin-top:4px;">
            {len(alertas)} publicação{'ões' if len(alertas)>1 else ''} encontrada{'s' if len(alertas)>1 else ''} em {hoje}
          </div>
        </div>

        <!-- Corpo -->
        <div style="background:#f4f6f9;padding:20px 0;">
          {blocos}
        </div>

        <!-- Rodapé -->
        <div style="text-align:center;font-size:11px;color:#9ca3af;padding:10px 0 20px;">
          Enviado automaticamente pelo EMC Monitor às 6h00 de hoje.
        </div>
      </div>
    </body>
    </html>
    """
