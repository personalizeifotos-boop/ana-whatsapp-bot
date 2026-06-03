import os
import re
import json
import imaplib
import email
import threading
import time
import gspread
import pytz
from datetime import datetime
from flask import Flask, request
from google.oauth2.service_account import Credentials

BRASILIA = pytz.timezone("America/Sao_Paulo")

app = Flash(__name__)

GMAIL_USER         = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SPREADSHEET_ID     = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"

# Mapeamento em memoria: telefone -> numero_pedido
telefone_pedido = {}

# ââ Google Sheets

def _gc():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        return None
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def get_sheet(nome="Pedidos", colunas=None):
    gc = _gc()
    if gc is None:
        return None
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(nome)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=nome, rows=1000, cols=len(colunas or []) + 2)
        if colunas:
            ws.append_row(colunas)
        return ws


def pedido_existe(numero):
    try:
        ws = get_sheet("Pedidos")
        if ws is None:
            return False
        col_a = ws.col_values(1)
        return numero.upper() in [v.strip().upper() for v in col_a if v.strip()]
    except Exception:
        return False


def atualizar_telefone_na_planilha(numero_pedido, telefone):
    try:
        ws = get_sheet("Pedidos")
        if ws is None:
            return
        cell = ws.find(numero_pedido)
        if cell:
            ws.update_cell(cell.row, 8, telefone)
    except Exception as e:
        print(f"[Sheets] Erro ao atualizar telefone: {e}")


def salvar_pedido(numero_pedido, produto="", quantidade="", sku="",
                  cliente="", prazo="", telefone="",
                  status="Pagamento confirmado", obs=""):
    try:
        ws = get_sheet("Pedidos")
        if ws is None:
            return
        data = datetime.now(BRASILBA).strftime("%d/%m/%Y %H:%M")
        ws.append_row([numero_pedido, data, produto, quantidade,
                       sku, cliente, prazo, telefone, status, obs])
        print(f"[Sheets] Pedido {numero_pedido} salvo.")
    except Exception as e:
        print(f"[Sheets] Erro ao salvar pedido: {e}")
        raise


def salvar_imagem_pendente(phone, image_url, pedido=""):
    try:
        ws = get_sheet("Imagens", ["Telefone", "URL", "Data", "Status", "Pedido"])
        if ws is None:
            return
        data = datetime.now(BRASILBA).strftime("%d/%m/%Y %H:%M")
        ws.append_row([phone, image_url, data, "pendente", pedido])
        print(f"[Imagens] Registrada imagem de {phone} (pedido: {pedido or 'nao vinculado'})")
    except Exception as e:
        print(f"[Imagens] Erro ao registrar: {e}")


PEDIDO_REGEX = re.compile(r'\b([A-Z0-9]{10,20})\b')


def extrair_numero_pedido(texto):
    candidatos = PEDIDO_REGEX.findall(texto.upper())
    for c in candidatos:
        if any(ch.isalpha() for ch in c) and any(ch.isdigit() for ch in c):
            return c
    return candidatos[0] if candidatos else None


def extrair_corpo_email(msg):
    corpo = ""
    corpo_html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not corpo:
                try:
                    corpo = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                except Exception:
                    pass
            elif ct == "text/html" and not corpo_html:
                try:
                    corpo_html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                except Exception:
                    pass
    else:
        try:
            raw = msg.get_payload(decode=True)
            if raw:
                decoded = raw.decode("utf-8", errors="ignore")
                if msg.get_content_type() == "text/html":
                    corpo_html = decoded
                else:
                    corpo = decoded
        except Exception:
            pass
    if not corpo and corpo_html:
        corpo = re.sub(r'<[^>]+>', ' ', corpo_html)
        corpo = re.sub(r'&nbsp;', ' ', corpo)
        corpo = re.sub(r'&amp;', '&', corpo)
        corpo = re.sub(r'\s+', ' ', corpo)
    return corpo


pedidos_processados = set()


def verificar_gmail():
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("[IMAP] Credenciais nao configuradas.")
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")
        _, msgs = mail.search(None, '(FROM "info@mail.shopee.com.br" SUBJECT "Hora de enviar")')
        ids = msgs[0].split()
        pedidos_na_planilha = set()
        try:
            ws = get_sheet("Pedidos")
            if ws:
                col_a = ws.col_values(1)
                pedidos_na_planilha = set(v.strip().upper() for v in col_a if v.strip())
        except Exception as e:
            print(f"[IMAP] Aviso ao carregar planilha: {e}")
        novos = 0
        for eid in ids:
            if eid in pedidos_processados:
                continue
            try:
                _, data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                assunto = msg.get("Subject", "")
                corpo = extrair_corpo_email(msg)
                m_subj = re.search(r'pedido\s+([A-Z0-9]{10,20})', assunto, re.IGNORECASE)
                numero = (m_subj.group(1).upper() if m_subj
                          else (extrair_numero_pedido(assunto) or extrair_numero_pedido(corpo)))
                if numero and numero.upper() not in pedidos_na_planilha:
                    produto = quantidade = sku = cliente = prazo = ""
                    m_prod = re.search(r'ID do pedido:\s*#?' + re.escape(numero) + r'[\s\S]{0,50}?([A-Za-z\U00C0-\U00FF][^\n\t]{10,})', corpo, re.IGNORECASE)
                    if m_prod: produto = m_prod.group(1).strip().rstrip('.')
                    m_qtd = re.search(r'Quantidade\s+(\d+)', corpo)
                    if m_qtd: quantidade = m_qtd.group(1).strip()
                    m_sku = re.search(r'Varia[\U00E7\U00C3\U00E3o]{2,}[: s]+([^\n\t<]{3,60})', corpo, re.IGNORECASE) or \
                             re.search(r'SKU[:\s]+([^\n\t<]{3,60})', corpo, re.IGNORECASE)
                    if m_sku: sku = re.sub(r'\d+-', '', m_sku.group(1).strip()).strip()
                    if not sku:
                        m_kit = re.search(r'(KIT\s+(?:AT[E\U00E9]\s+)?\d+\s+FOTOS' + '?)', corpo, re.IGNORECASE)
                        if m_kit: sku = m_kit.group(1).strip().upper()
                    m_num = re.search(r'(\d+)\s*FOTO', sku.upper())
                    if m_num: sku = m_num.group(1) + ' fotos'
                    mc = re.search(r'Envie o pedido para ([^\.\n,]+)', corpo)
                    if mc: cliente = mc.group(1).strip()
                    mp = re.search(r'At[e\U00E9]\s+\d+\s+de\s+\w+', corpo, re.IGNORECASE)
                    if mp: prazo = mp.group(0).strip()
                    salvar_pedido(numero_pedido=numero, produto=produto, quantidade=quantidade, sku=sku, cliente=cliente, prazo=prazo, status="Pagamento confirmado", obs=f"Detectado via Gmail em {datetime.now(BRASILDIA.strftime('%d/%m/%Y %H:%M')}")
                    pedidos_na_planilha.add(numero.upper())
                    novos += 1
                    time.sleep(3)
            except Exception as e:
                print(f"[IMAP] Erro {eid}: {e}")
                time.sleep(2)
            finally:
                pedidos_processados.add(eid)
        print(f"[IMAP] {novos} novos pedidos.")
        mail.logout()
    except Exception as e:
        print(f"[IMAP] Erro: {e}")


def thread_gmail():
    print("[IMAP] Thread Gmail iniciada")
    while True:
        verificar_gmail()
        time.sleep(60)


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    try:
        data = request.get_json(force=True, silent=True) or {}
        if data.get("fromMe", False):
            return "ok", 200
        phone = (data.get("phone", "").replace("@s.whatsapp.net", "").replace("@c.us", ""))
        msg_type = data.get("type", "")
        body = data.get("body", "") or ""
        if msg_type == "chat":
            numero = extrair_numero_pedido(body)
            if numero and pedido_existe(numero):
                telefone_pedido[phone] = numero
                atualizar_telefone_na_planilha(numero, phone)
                print(f"[Webhook] Pedido {numero} vinculado ao telefone {phone}")
        elif msg_type == "image":
            image_url = body
            if image_url:
                pedido_vinculado = telefone_pedido.get(phone, "")
                salvar_imagem_pendente(phone, image_url, pedido_vinculado)
        return "ok", 200
    except Exception as e:
        print(f"[Webhook] Erro: {e}")
        return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "Ana Bot OK", 200


_imap_thread = threading.Thread(target=thread_gmail, daemon=True)
_imap_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
