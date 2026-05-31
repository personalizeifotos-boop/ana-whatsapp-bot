from flask import Flask, request, jsonify
import requests
import os
import re
import imaplib
import email
import threading
import time
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

app = Flask(__name__)

INSTANCE_ID = "3F353F900771725020A0F6B0730C054E"
TOKEN = "2E4ECDD70099CF7EDCEAF35E"
CLIENT_TOKEN = "Fd7f15657ef534ae09757eefa5368120cS"
ZAPI_BASE = f"https://api.z-api.io/instances/{INSTANCE_ID}/token/{TOKEN}"
ZAPI_HEADERS = {"Client-Token": CLIENT_TOKEN, "Content-Type": "application/json"}

GMAIL_USER = os.environ.get("GMAIL_USER", "personalizei.fotos@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
SHEET_ID = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"

S_WELCOME = "welcome"
S_WAITING_ORDER = "waiting_order"
S_WAITING_IMAGES = "waiting_images"
S_WAITING_EXTRA = "waiting_extra_confirm"
S_WAITING_PIX = "waiting_pix"

PRICES = {"10X15": 1.00, "15X21": 1.50, "POLAROIDE": 1.00, "A4": 3.00, "IMA": 2.50, "TAG": 1.00, "ADESIVO": 1.00, "CARTAO DE VISITA": 1.00}
KEYWORDS_RULE_A = ["tag", "cartao de visita", "adesivo"]
LOJA_LINK = "https://shopee.com.br/personalizei_fotografias?shop=1331254404"
MSG_FECHAMENTO = "Perfeito, seu pedido ja esta sendo preparado. Segue o link da loja:\n" + LOJA_LINK
MSG_EXTRA = "O valor das {n} fotos a mais e de R$ {valor:.2f}.\n\nChave PIX\nTitular: Rodrigo Vieira Monteiro\nChave: 58733941000114\n\nApos pagar envie o comprovante."

pedidos_confirmados = set()
sessions = {}
bot_paused = False
sheets_client = None
worksheet = None

def init_sheets():
    global sheets_client, worksheet
    if not GOOGLE_CREDENTIALS_JSON:
        print("[Sheets] sem credenciais")
        return
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        sheets_client = gspread.authorize(creds)
        sh = sheets_client.open_by_key(SHEET_ID)
        worksheet = sh.sheet1
        headers = worksheet.row_values(1)
        if not headers or headers[0] != "Numero do Pedido":
            worksheet.update("A1:G1", [["Numero do Pedido","Data","Produto","Quantidade","Telefone","Status","Obs"]])
        print("[Sheets] OK!")
    except Exception as e:
        print(f"[Sheets] Erro: {e}")

def salvar_pedido_sheets(num, produto="", qtd="", tel="", status="Confirmado", obs=""):
    if worksheet is None: return
    try:
        data = datetime.now().strftime("%d/%m/%Y %H:%M")
        worksheet.append_row([num, data, produto, str(qtd), tel, status, obs])
        print(f"[Sheets] Salvo: {num}")
    except Exception as e:
        print(f"[Sheets] Erro salvar: {e}")

def atualizar_status_sheets(num, status, obs=""):
    if worksheet is None or not num: return
    try:
        cell = worksheet.find(num)
        if cell:
            worksheet.update_cell(cell.row, 6, status)
            if obs: worksheet.update_cell(cell.row, 7, obs)
    except Exception as e:
        print(f"[Sheets] Erro atualizar: {e}")

def clean_subject(s):
    decoded = email.header.decode_header(s)
    return "".join(p.decode(e or "utf-8", errors="ignore") if isinstance(p, bytes) else p for p, e in decoded)

def verificar_gmail():
    if not GMAIL_APP_PASSWORD: return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")
        _, ids = mail.search(None, '(UNSEEN FROM "Shopee")')
        novos = 0
        for mid in ids[0].split():
            _, data = mail.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            subject = clean_subject(msg.get("Subject", ""))
            if "pagamento" in subject.lower():
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ("text/plain","text/html"):
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore"); break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                m = re.search(r'\b([0-9A-Z]{10,20})\b', body + " " + subject)
                if m:
                    num = m.group(1)
                    if num not in pedidos_confirmados:
                        pedidos_confirmados.add(num)
                        p = re.search(r'Produto[:\s]+([^\n]+)', body)
                        q = re.search(r'Quantidade[:\s]+(\d+)', body)
                        salvar_pedido_sheets(num, p.group(1).strip() if p else "", q.group(1) if q else "", "", "Aguardando imagens")
                        novos += 1
                mail.store(mid, "+FLAGS", "\\Seen")
        mail.logout()
        print(f"[IMAP] {novos} emails novos da Shopee")
    except Exception as e:
        print(f"[IMAP] Erro: {e}")

def imap_thread():
    print("[IMAP] Thread Gmail iniciada")
    while True:
        verificar_gmail()
        time.sleep(60)

def clean_phone(p): return re.sub(r'@.*$', '', str(p)).strip()

def send_text(phone, message):
    phone = clean_phone(phone)
    resp = requests.post(f"{ZAPI_BASE}/send-text", json={"phone": phone, "message": message}, headers=ZAPI_HEADERS)
    print(f"[send] {phone}: {message[:50]} -> {resp.status_code}")
    return resp

def get_session(phone):
    phone = clean_phone(phone)
    if phone not in sessions:
        sessions[phone] = {"state": S_WELCOME, "order": None, "product": None, "qty": 0, "images": 0}
    return sessions[phone]

def detect_product(text):
    for p in PRICES:
        if p in text.upper(): return p
    return None

def is_order_valid(num):
    if not num: return False
    num = num.strip().upper()
    if re.match(r'^[A-Z0-9]{5,20}$', num):
        return num in pedidos_confirmados or len(pedidos_confirmados) == 0
    return False

@app.route('/webhook', methods=['POST'])
def webhook():
    global bot_paused
    data = request.get_json(force=True)
    print(f"[webhook] {json.dumps(data)[:200]}")
    phone = clean_phone(data.get("phone") or data.get("from") or data.get("sender") or "")
    msg_type = data.get("type", "")
    if msg_type == "TEXT":
        text = (data.get("text", {}).get("message") or data.get("message") or "").strip()
    elif msg_type in ("IMAGE","DOCUMENT","STICKER"):
        text = "__IMAGE__"
    else:
        text = (data.get("message") or data.get("body") or "").strip()
        if not text: return jsonify({"status": "ignored"}), 200
    if not phone: return jsonify({"status": "no phone"}), 200

    if text.startswith("/pausar-ana"):
        bot_paused = True; send_text(phone, "Ana pausada."); return jsonify({"status": "ok"}), 200
    if text.startswith("/retomar-ana"):
        bot_paused = False; send_text(phone, "Ana reativada."); return jsonify({"status": "ok"}), 200
    if text.startswith("/status-ana"):
        send_text(phone, f"Status: {'Pausada' if bot_paused else 'Ativa'}\nPedidos: {len(pedidos_confirmados)}\nSessoes: {len(sessions)}")
        return jsonify({"status": "ok"}), 200
    if text.startswith("/pedido "):
        parts = text.split()
        if len(parts) >= 4:
            p_phone = clean_phone(parts[1]); produto = parts[2].upper(); qty = int(parts[3]) if parts[3].isdigit() else 0
            num = f"MANUAL-{p_phone[-4:]}-{int(time.time())}"
            pedidos_confirmados.add(num); salvar_pedido_sheets(num, produto, qty, p_phone, "Manual")
            sess = get_session(p_phone)
            sess.update({"state": S_WAITING_IMAGES, "product": produto, "qty": qty, "order": num})
            send_text(p_phone, f"Pedido confirmado! Envie {qty} foto(s) para {produto}.")
            send_text(phone, f"Pedido {num} criado.")
        return jsonify({"status": "ok"}), 200

    if bot_paused: return jsonify({"status": "paused"}), 200

    sess = get_session(phone)
    state = sess["state"]

    if state == S_WELCOME:
        sess["state"] = S_WAITING_ORDER
        send_text(phone, "Ola, seja bem vindo!!! Antes de enviar as suas imagens preciso que voce me envie o numero do pedido.")
    elif state == S_WAITING_ORDER:
        m = re.search(r'[A-Z0-9]{5,20}', text.upper())
        order_num = m.group(0) if m else text.strip()
        if is_order_valid(order_num):
            sess["order"] = order_num
            produto = detect_product(text)
            if produto: sess["product"] = produto
            sess["state"] = S_WAITING_IMAGES
            msg = f"Pedido {order_num} confirmado! " + (f"Envie suas fotos para {produto}." if produto else "Agora pode enviar suas fotos.")
            send_text(phone, msg)
            atualizar_status_sheets(order_num, "Aguardando imagens", f"Tel: {phone}")
        else:
            send_text(phone, "Numero de pedido nao encontrado. Verifique e tente novamente.")
    elif state == S_WAITING_IMAGES:
        if text == "__IMAGE__":
            sess["images"] = sess.get("images", 0) + 1
            qty = sess.get("qty", 0); produto = sess.get("product", "")
            if qty > 0 and sess["images"] >= qty:
                if produto and produto.lower() in KEYWORDS_RULE_A:
                    extra = sess["images"] - qty
                    if extra > 0:
                        valor = extra * PRICES.get(produto.upper(), 1.0)
                        sess["state"] = S_WAITING_EXTRA
                        send_text(phone, MSG_EXTRA.format(n=extra, valor=valor))
                        return jsonify({"status": "ok"}), 200
                sess["state"] = S_WAITING_PIX
                send_text(phone, MSG_FECHAMENTO)
                atualizar_status_sheets(sess.get("order",""), "Imagens recebidas")
            else:
                send_text(phone, f"Foto {sess['images']} recebida! Continue enviando.")
        else:
            send_text(phone, "Por favor, envie suas fotos.")
    elif state == S_WAITING_EXTRA:
        if text == "__IMAGE__":
            sess["state"] = S_WAITING_PIX; send_text(phone, MSG_FECHAMENTO)
            atualizar_status_sheets(sess.get("order",""), "Pagamento recebido")
        else:
            send_text(phone, "Aguardando comprovante PIX.")
    elif state == S_WAITING_PIX:
        if text == "__IMAGE__":
            sess["state"] = S_WAITING_IMAGES; send_text(phone, "Comprovante recebido! Obrigado, pedido em processamento.")
            atualizar_status_sheets(sess.get("order",""), "Concluido")
        else:
            send_text(phone, "Aguardando o comprovante de pagamento.")

    return jsonify({"status": "ok"}), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status":"ok","paused":bot_paused,"pedidos":len(pedidos_confirmados),"sessions":len(sessions)}), 200

if __name__ == '__main__':
    init_sheets()
    threading.Thread(target=imap_thread, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
else:
    init_sheets()
    threading.Thread(target=imap_thread, daemon=True).start()
