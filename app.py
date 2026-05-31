import os
import re
import json
import imaplib
import email
import threading
import time
import gspread
from datetime import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ─── Configurações ────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP    = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

GMAIL_USER         = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

SPREADSHEET_ID     = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"

# ─── Google Sheets ────────────────────────────────────────────────────────────
def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        return None
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet("Pedidos")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Pedidos", rows=1000, cols=10)
        ws.append_row([
            "Número do Pedido", "Data", "Produto", "Quantidade",
            "Telefone", "Status", "Obs"
        ])
    return ws


def salvar_pedido(numero_pedido, produto="—", quantidade="—",
                  telefone="—", status="Aguardando imagens", obs=""):
    try:
        ws = get_sheet()
        if ws is None:
            return
        data = datetime.now().strftime("%d/%m/%Y %H:%M")
        ws.append_row([numero_pedido, data, produto, quantidade,
                       telefone, status, obs])
        print(f"[Sheets] Pedido {numero_pedido} salvo.")
    except Exception as e:
        print(f"[Sheets] Erro ao salvar: {e}")


def atualizar_status(numero_pedido, novo_status, obs=""):
    try:
        ws = get_sheet()
        if ws is None:
            return
        cell = ws.find(numero_pedido)
        if cell:
            ws.update_cell(cell.row, 6, novo_status)
            if obs:
                ws.update_cell(cell.row, 7, obs)
            print(f"[Sheets] Status de {numero_pedido} → {novo_status}")
    except Exception as e:
        print(f"[Sheets] Erro ao atualizar: {e}")


def buscar_telefone_pedido(numero_pedido):
    """Retorna o telefone associado ao pedido, ou None."""
    try:
        ws = get_sheet()
        if ws is None:
            return None
        cell = ws.find(numero_pedido)
        if cell:
            return ws.cell(cell.row, 5).value or None
        return None
    except Exception:
        return None


def pedido_existe(numero_pedido):
    try:
        ws = get_sheet()
        if ws is None:
            return False
        cell = ws.find(numero_pedido)
        return cell is not None
    except Exception:
        return False


# ─── Extração do número de pedido Shopee ─────────────────────────────────────
PEDIDO_REGEX = re.compile(r'\b([A-Z0-9]{10,20})\b')


def extrair_numero_pedido(texto):
    candidatos = PEDIDO_REGEX.findall(texto.upper())
    for c in candidatos:
        tem_letra = any(ch.isalpha() for ch in c)
        tem_digito = any(ch.isdigit() for ch in c)
        if tem_letra and tem_digito:
            return c
    return candidatos[0] if candidatos else None


# ─── IMAP — monitoramento do Gmail ───────────────────────────────────────────
pedidos_processados = set()


def verificar_gmail():
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("[IMAP] Credenciais Gmail não configuradas.")
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")
        _, msgs = mail.search(
            None,
            '(FROM "noreply@shopee.com.br" SUBJECT "Pagamento Confirmado")'
        )
        ids = msgs[0].split()
        novos = 0
        for eid in ids:
            if eid in pedidos_processados:
                continue
            _, data = mail.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            assunto = msg.get("Subject", "")
            corpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        corpo = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
            else:
                corpo = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            numero = extrair_numero_pedido(assunto) or extrair_numero_pedido(corpo)
            if numero and numero not in pedidos_processados:
                salvar_pedido(
                    numero_pedido=numero,
                    produto="—",
                    quantidade="—",
                    telefone="—",
                    status="Pagamento confirmado",
                    obs=f"Detectado via Gmail em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                )
                pedidos_processados.add(numero)
                novos += 1
            pedidos_processados.add(eid)
        print(f"[IMAP] {novos} novos pedidos da Shopee.")
        mail.logout()
    except Exception as e:
        print(f"[IMAP] Erro: {e}")


def thread_gmail():
    print("[IMAP] Thread Gmail iniciada")
    while True:
        verificar_gmail()
        time.sleep(60)


# ─── Estado das conversas WhatsApp ───────────────────────────────────────────
conversas = {}


def responder_ana(telefone, mensagem):
    msg = mensagem.strip()
    estado = conversas.get(telefone, {"etapa": "inicio"})
    etapa = estado.get("etapa", "inicio")
    if etapa == "inicio":
        numero = extrair_numero_pedido(msg.upper())
        if numero:
            if pedido_existe(numero):
                estado = {"etapa": "aguardando_imagens", "pedido": numero}
                conversas[telefone] = estado
                try:
                    ws = get_sheet()
                    if ws:
                        cell = ws.find(numero)
                        if cell:
                            ws.update_cell(cell.row, 5, telefone)
                except Exception:
                    pass
                return (
                    f"Olá! 😊 Encontrei seu pedido *{numero}* aqui.\n\n"
                    "Para prosseguir, por favor me envie as fotos que deseja usar no produto. "
                    "Pode mandar todas de uma vez!"
                )
            else:
                return (
                    f"Oi! Não encontrei o pedido *{numero}* no sistema ainda. "
                    "Aguarde alguns minutos após a confirmação do pagamento e tente novamente. "
                    "Se o problema persistir, me informe e eu verifico! 😊"
                )
        else:
            return (
                "Olá! Sou a Ana, assistente da *Personalizei Fotos* 📸\n\n"
                "Para começar, me informe o *número do seu pedido* da Shopee. "
                "Você encontra esse número no app da Shopee em Meus Pedidos."
            )
    elif etapa == "aguardando_imagens":
        pedido = estado.get("pedido", "")
        numero = extrair_numero_pedido(msg.upper())
        if numero and numero != pedido:
            estado = {"etapa": "aguardando_imagens", "pedido": numero}
            conversas[telefone] = estado
            return f"Ok, mudando para o pedido *{numero}*. Me envie as fotos! 📷"
        return (
            "Recebi! Assim que todas as fotos chegarem, nossa equipe começa a produção. "
            "Prazo médio de entrega: *3 a 5 dias úteis*. "
            "Qualquer dúvida, estou aqui! 😊"
        )
    return "Olá! Para um novo atendimento, me informe o número do seu pedido da Shopee. 😊"


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    telefone  = request.form.get("From", "")
    mensagem  = request.form.get("Body", "")
    media_url = request.form.get("MediaUrl0", "")
    estado = conversas.get(telefone, {"etapa": "inicio"})
    if media_url and estado.get("etapa") == "aguardando_imagens":
        pedido = estado.get("pedido", "")
        atualizar_status(pedido, "Imagens recebidas",
                         obs=f"Imagem recebida em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        resposta_texto = (
            "Imagem recebida com sucesso! ✅\n"
            "Nossa equipe já foi notificada e vai iniciar a produção em breve. "
            "Obrigada pela confiança! 💜"
        )
        conversas[telefone] = {"etapa": "imagens_recebidas", "pedido": pedido}
    else:
        resposta_texto = responder_ana(telefone, mensagem)
    resp = MessagingResponse()
    resp.message(resposta_texto)
    return str(resp)


@app.route("/", methods=["GET"])
def health():
    return "Ana Bot OK", 200


if __name__ == "__main__":
    t = threading.Thread(target=thread_gmail, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
