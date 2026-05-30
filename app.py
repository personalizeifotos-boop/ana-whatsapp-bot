from flask import Flask, request, jsonify
import requests
import os
import re
import imaplib
import email
import threading
import time

app = Flask(__name__)

INSTANCE_ID = "3F353F900771725020A0F6B0730C054E"
TOKEN = "2E4ECDD70099CF7EDCEAF35E"
CLIENT_TOKEN = "Fd7f15657ef534ae09757eefa5368120cS"
ZAPI_BASE = f"https://api.z-api.io/instances/{INSTANCE_ID}/token/{TOKEN}"
ZAPI_HEADERS = {"Client-Token": CLIENT_TOKEN, "Content-Type": "application/json"}

GMAIL_USER = os.environ.get("GMAIL_USER", "personalizei.fotos@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

S_WELCOME = "welcome"
S_WAITING_ORDER = "waiting_order"
S_WAITING_IMAGES = "waiting_images"
S_WAITING_EXTRA = "waiting_extra_confirm"
S_WAITING_PIX = "waiting_pix"

PRICES = {"10X15": 1.00, "15X21": 1.50, "POLAROIDE": 1.00, "A4": 3.00, "IMA": 2.50, "TAG": 1.00, "ADESIVO": 1.00, "CARTAO DE VISITA": 1.00}
KEYWORDS_RULE_A = ["tag", "cartao de visita", "adesivo"]
PRODUCT_FOLDERS = ["10X15", "15X21", "A4", "Mini foto", "tirinhas", "ima", "mini ima", "adesivo", "tag", "cartao de visita"]
LOJA_LINK = "https://shopee.com.br/personalizei_fotografias?shop=1331254404"
MSG_FECHAMENTO = "Perfeito, seu pedido ja esta sendo preparado. Segue o link da loja:\n" + LOJA_LINK
PIX_MSG = "O valor das {n} fotos a mais e de R$ {valor:.2f}.\n\nSegue a chave PIX\nTitular: Rodrigo Vieira Monteiro\nChave PIX: 58733941000114\n\nApos efetuar o pagamento nos envie o comprovante."

pedidos_confirmados = set()
sessions = {}

def new_session():
    return {"state": S_WELCOME, "order": None, "images_count": 0, "rule": None,
            "product_type": None, "qty_expected": None, "extra_count": 0,
            "extra_value": 0.0, "order_confirmed": False}

def get_session(phone):
    if phone not in sessions:
        sessions[phone] = new_session()
    return sessions[phone]

def reset_session(phone):
    sessions[phone] = new_session()

def clean_phone(phone):
    return re.sub(r'@.*$', '', str(phone)).strip()

def send_text(phone, msg):
    phone = clean_phone(phone)
    try:
        r = requests.post(f"{ZAPI_BASE}/send-text",
                          json={"phone": phone, "message": msg},
                          headers=ZAPI_HEADERS, timeout=15)
        print(f"[send_text] {phone}: {r.status_code} {r.text[:100]}")
        return r.json()
    except Exception as e:
        print(f"[send_text] Erro: {e}")
        return {}

def is_paused():
    return os.path.exists("/tmp/ana_paused")

def looks_like_order(t):
    t_clean = t.replace(" ", "").upper()
    return bool(re.search(r'[A-Z0-9]{8,}', t_clean) and re.search(r'[0-9]', t_clean))

def extract_order(t):
    t_clean = t.replace(" ", "").upper()
    m = re.search(r'[A-Z0-9]{8,}', t_clean)
    return m.group() if m else t.strip().upper()

def is_rule_a(d):
    return any(kw in d.lower() for kw in KEYWORDS_RULE_A)

def detect_pt(d):
    for p in PRODUCT_FOLDERS:
        if p.lower() in d.lower():
            return p
    return None

def ppu(pt):
    for k, v in PRICES.items():
        if k in pt.upper():
            return v
    return 1.00

def extract_qty(d):
    m = re.search(r'\b(\d{1,3})\b', d)
    return int(m.group(1)) if m else None

def extrair_numero_pedido(corpo):
    padroes = [
        r'ID do pedido[:\s]*([A-Z0-9]{8,25})',
        r'N[u\xfa]mero do pedido[:\s]*([A-Z0-9]{8,25})',
        r'Order ID[:\s]*([A-Z0-9]{8,25})',
        r'\b([0-9]{5}[A-Z0-9]{5,20})\b',
    ]
    for padrao in padroes:
        m = re.search(padrao, corpo, re.IGNORECASE)
        if m:
            return m.group(1).upper().strip()
    return None

def check_gmail_imap():
    global pedidos_confirmados
    if not GMAIL_APP_PASSWORD:
        print("[IMAP] GMAIL_APP_PASSWORD nao configurada")
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")
        status, msgs = mail.search(None, '(UNSEEN FROM "shopee" SUBJECT "Pagamento Confirmado")')
        if status != "OK":
            mail.logout()
            return
        ids = msgs[0].split()
        print(f"[IMAP] {len(ids)} emails novos da Shopee")
        for msg_id in ids:
            status2, data = mail.fetch(msg_id, "(RFC822)")
            if status2 != "OK":
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            corpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    if ct in ("text/plain", "text/html"):
                        try:
                            corpo += part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        except Exception:
                            pass
            else:
                try:
                    corpo = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                except Exception:
                    pass
            numero = extrair_numero_pedido(corpo)
            if numero:
                if numero not in pedidos_confirmados:
                    pedidos_confirmados.add(numero)
                    print(f"[IMAP] Pedido adicionado: {numero}")
                mail.store(msg_id, "+FLAGS", "\\Seen")
        mail.logout()
    except Exception as e:
        print(f"[IMAP] Erro: {e}")

def gmail_loop():
    print("[IMAP] Thread Gmail iniciada")
    while True:
        check_gmail_imap()
        time.sleep(60)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    if data.get("fromMe"):
        return jsonify({"status": "ok"})
    phone = clean_phone(data.get("phone", ""))
    if not phone:
        return jsonify({"status": "ok"})
    text = (data.get("text") or {}).get("message", "").strip()
    is_image = bool(data.get("image") or data.get("document") or data.get("video"))
    print(f"[webhook] phone={phone} text={text[:50] if text else ''} image={is_image}")
    if text.startswith("/pausar-ana"):
        open("/tmp/ana_paused", "w").close()
        return jsonify({"status": "paused"})
    if text.startswith("/retomar-ana"):
        if os.path.exists("/tmp/ana_paused"):
            os.remove("/tmp/ana_paused")
        return jsonify({"status": "active"})
    if text.startswith("/status-ana"):
        send_text(phone, f"Ana esta {'PAUSADA' if is_paused() else 'ATIVA'}.\nPedidos em memoria: {len(pedidos_confirmados)}")
        return jsonify({"status": "ok"})
    if text.startswith("/pedido "):
        parts = text.split(maxsplit=3)
        if len(parts) >= 4:
            target = clean_phone(parts[1])
            s = get_session(target)
            s["rule"] = "A" if is_rule_a(parts[2]) else "B"
            s["product_type"] = detect_pt(parts[2]) or parts[2]
            try:
                s["qty_expected"] = int(parts[3])
            except Exception:
                s["qty_expected"] = extract_qty(parts[2]) or 1
            s["order_confirmed"] = True
        return jsonify({"status": "ok"})
    if text.startswith("/pedidos"):
        lista = ", ".join(sorted(pedidos_confirmados)) if pedidos_confirmados else "Nenhum"
        send_text(phone, f"Pedidos confirmados ({len(pedidos_confirmados)}):\n{lista}")
        return jsonify({"status": "ok"})
    if is_paused():
        return jsonify({"status": "ok"})
    s = get_session(phone)
    if s["state"] in (S_WELCOME, S_WAITING_ORDER):
        if is_image:
            send_text(phone, "Por favor, preciso do numero do pedido antes das imagens.")
            s["state"] = S_WAITING_ORDER
        elif text and looks_like_order(text):
            order = extract_order(text)
            s["order"] = order
            pedido_valido = (order in pedidos_confirmados) or not GMAIL_APP_PASSWORD
            if pedido_valido:
                s["state"] = S_WAITING_IMAGES
                send_text(phone, f"Numero do pedido *{order}* confirmado! Agora pode enviar as suas imagens.")
            else:
                send_text(phone, f"Nao encontrei o pedido *{order}* nos nossos registros. Verifique o numero e tente novamente, ou aguarde alguns minutos ate o pagamento ser processado.")
        else:
            if s["state"] == S_WELCOME:
                send_text(phone, "Ola, seja bem vindo!!! Antes de enviar as suas imagens preciso que voce me envie o numero do pedido.")
                s["state"] = S_WAITING_ORDER
            else:
                send_text(phone, "Por favor, envie o numero do seu pedido para continuar.")
    elif s["state"] == S_WAITING_IMAGES:
        if is_image:
            s["images_count"] += 1
            if s["order_confirmed"]:
                _apply_rules(phone, s)
            else:
                send_text(phone, f"Imagem {s['images_count']} recebida! Pode continuar enviando as demais.")
        elif text:
            send_text(phone, "Por favor, envie as imagens do seu pedido.")
    elif s["state"] == S_WAITING_EXTRA:
        resp = text.lower()
        if any(w in resp for w in ["sim", "s", "quero", "yes", "pode", "ok"]):
            extra = s["extra_count"]
            total = extra * ppu(s["product_type"] or "15X21")
            s["extra_value"] = total
            s["state"] = S_WAITING_PIX
            send_text(phone, PIX_MSG.format(n=extra, valor=total))
        elif any(w in resp for w in ["nao", "n", "no"]):
            send_text(phone, "Tudo bem! Por favor, me indique quais imagens deseja descartar.")
            s["state"] = S_WAITING_IMAGES
        else:
            send_text(phone, "Por favor, responda Sim ou Nao.")
    elif s["state"] == S_WAITING_PIX:
        if is_image:
            send_text(phone, MSG_FECHAMENTO)
            reset_session(phone)
        elif text:
            send_text(phone, "Por favor, envie o comprovante do pagamento PIX.")
    return jsonify({"status": "ok"})

def _apply_rules(phone, s):
    count = s["images_count"]
    expected = s["qty_expected"]
    if s["rule"] == "A":
        if count == 1:
            send_text(phone, MSG_FECHAMENTO)
            reset_session(phone)
        else:
            send_text(phone, "Por favor envie apenas a imagem que deseja incluir no pedido.")
    elif s["rule"] == "B":
        if expected is None:
            send_text(phone, f"Imagem {count} recebida!")
            return
        if count == expected:
            send_text(phone, MSG_FECHAMENTO)
            reset_session(phone)
        elif count < expected:
            send_text(phone, f"Ficou faltando {expected - count} imagem(ns).")
        else:
            extra = count - expected
            s["extra_count"] = extra
            s["state"] = S_WAITING_EXTRA
            send_text(phone, f"Voce enviou {extra} imagem(ns) a mais. Voce vai querer comprar as imagens a mais?")

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "online", "ana": "pausada" if is_paused() else "ativa",
                    "sessoes": len(sessions), "pedidos_confirmados": len(pedidos_confirmados),
                    "gmail_ativo": bool(GMAIL_APP_PASSWORD)})

t = threading.Thread(target=gmail_loop, daemon=True)
t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
