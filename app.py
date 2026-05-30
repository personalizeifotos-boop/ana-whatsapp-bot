import os
import re
import json
import logging
import requests
from flask import Flask, request, jsonify

try:
        import gspread
        from google.oauth2.service_account import Credentials
        SHEETS_AVAILABLE = True
except ImportError:
        SHEETS_AVAILABLE = False

INSTANCE_ID  = "3F353F900771725020A0F6B0730C054E"
TOKEN        = "2E4ECDD70099CF7EDCEAF35E"
CLIENT_TOKEN = "Fd7f15657ef534ae09757eefa5368120cS"
ZAPI_BASE    = f"https://api.z-api.io/instances/{INSTANCE_ID}/token/{TOKEN}"
ZAPI_HEADERS = {"Client-Token": CLIENT_TOKEN, "Content-Type": "application/json"}

SHEET_ID     = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"
SHEET_NAME   = "Pedidos"
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDENTIALS_JSON")

PIX_KEY  = "58733941000114"
PIX_NOME = "Rodrigo Vieira Monteiro"

S_WELCOME        = "welcome"
S_WAITING_ORDER  = "waiting_order"
S_WAITING_IMAGES = "waiting_images"
S_WAITING_PIX    = "waiting_pix"

user_states = {}
user_data   = {}
bot_paused  = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


def clean_phone(phone: str) -> str:
        return re.sub(r'@.*$', '', str(phone)).strip()


def send_text(phone: str, text: str):
        phone = clean_phone(phone)
        url = f"{ZAPI_BASE}/send-text"
        payload = {"phone": phone, "message": text}
        try:
                    r = requests.post(url, json=payload, headers=ZAPI_HEADERS, timeout=15)
                    logger.info(f"send_text -> {phone} | {r.status_code} | {r.text[:200]}")
                    return r
except Exception as e:
        logger.error(f"send_text ERRO: {e}")


def get_google_sheet():
        if not SHEETS_AVAILABLE or not GOOGLE_CREDS:
                    return None
                try:
                            creds_dict = json.loads(GOOGLE_CREDS)
                            scopes = ["https://www.googleapis.com/auth/spreadsheets",
                                      "https://www.googleapis.com/auth/drive.readonly"]
                            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
                            gc = gspread.authorize(creds)
                            return gc.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
except Exception as e:
        logger.error(f"Sheets ERRO: {e}")
        return None


def pedido_valido(numero_pedido: str) -> bool:
        ws = get_google_sheet()
    if ws is None:
                logger.warning("Planilha indisponivel - aceitando pedido.")
                return True
            try:
                        col = ws.col_values(1)
                        pedidos = [str(p).strip().upper() for p in col[1:]]
                        ok = numero_pedido.upper() in pedidos
                        logger.info(f"Pedido {numero_pedido}: {'OK' if ok else 'NAO ENCONTRADO'}")
                        return ok
except Exception as e:
        logger.error(f"Erro verificar pedido: {e}")
        return True


def is_order_number(text: str) -> bool:
        return bool(re.match(r'^[A-Za-z0-9]{6,25}$', text.strip()))


def process_message(phone: str, message: str, msg_type: str = "text"):
        global bot_paused
    phone = clean_phone(phone)
    message = (message or "").strip()

    if message.startswith("/"):
                if message == "/pausar-ana":
                                bot_paused = True
                                send_text(phone, "Ana pausada.")
    elif message == "/retomar-ana":
            bot_paused = False
            send_text(phone, "Ana reativada.")
elif message == "/status-ana":
            send_text(phone, f"Status: {'Pausada' if bot_paused else 'Ativa'}")
elif message.startswith("/pedido "):
            partes = message.split(" ", 3)
            if len(partes) >= 4:
                                p_phone, p_prod, p_qtd = clean_phone(partes[1]), partes[2], partes[3]
                                user_states[p_phone] = S_WAITING_IMAGES
                                user_data[p_phone] = {"produto": p_prod, "quantidade": int(p_qtd), "imagens": []}
                                send_text(p_phone, f"Pedido confirmado! Produto: {p_prod} ({p_qtd} un).\nEnvie suas imagens agora.")
                                send_text(phone, f"Pedido de {p_phone} configurado.")
                        return

    if bot_paused:
                return

    state = user_states.get(phone, S_WELCOME)

    if state == S_WELCOME:
                send_text(phone, "Ola, seja bem vindo!!!\nAntes de enviar as suas imagens preciso que voce me envie o numero do pedido.")
                user_states[phone] = S_WAITING_ORDER

elif state == S_WAITING_ORDER:
        if is_order_number(message):
                        numero = message.upper()
                        if pedido_valido(numero):
                                            user_data[phone] = {"pedido": numero, "imagens": []}
                                            user_states[phone] = S_WAITING_IMAGES
                                            send_text(phone, f"Pedido {numero} confirmado!\nAgora pode enviar as suas imagens.")
        else:
                send_text(phone, f"Nao encontrei o pedido {numero}.\nVerifique o numero e tente novamente.")
        else:
            send_text(phone, "Por favor, envie apenas o numero do pedido (ex: 26053ORBEPCRN3).")

elif state == S_WAITING_IMAGES:
        if msg_type in ("image", "document"):
                        imgs = user_data.get(phone, {}).get("imagens", [])
                        imgs.append(message)
                        user_data[phone]["imagens"] = imgs
                        qtd = user_data[phone].get("quantidade", 1)
                        if len(imgs) >= qtd:
                                            user_states[phone] = S_WELCOME
                                            send_text(phone, "Imagens recebidas! Obrigado.\nSeu pedido esta sendo processado.")
        else:
                send_text(phone, f"Imagem recebida! Faltam {qtd - len(imgs)} imagem(ns).")
        else:
            send_text(phone, "Por favor, envie as imagens do seu pedido.")

elif state == S_WAITING_PIX:
        if msg_type in ("image", "document"):
                        user_states[phone] = S_WAITING_IMAGES
                        send_text(phone, "Comprovante recebido! Aguarde confirmacao.")
else:
            send_text(phone, f"Envie o comprovante do PIX.\nChave: {PIX_KEY}\nTitular: {PIX_NOME}")

else:
        user_states[phone] = S_WELCOME
        process_message(phone, message, msg_type)


@app.route("/webhook", methods=["POST"])
def webhook():
        try:
                    data = request.get_json(force=True, silent=True) or {}
                    logger.info(f"WEBHOOK: {json.dumps(data)[:500]}")
                    phone = clean_phone(data.get("phone") or data.get("from") or data.get("sender") or "")
                    if data.get("fromMe") or data.get("fromApi") or not phone:
                                    return jsonify({"status": "ignored"}), 200
                                msg_type = data.get("type", "text").lower()
        text = ""
        if "text" in data:
                        text = data["text"].get("message", "") if isinstance(data["text"], dict) else str(data["text"])
        elif "message" in data:
            text = data["message"] if isinstance(data["message"], str) else ""
        process_message(phone, text, msg_type)
        return jsonify({"status": "ok"}), 200
except Exception as e:
        logger.error(f"WEBHOOK ERRO: {e}", exc_info=True)
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.route("/", methods=["GET"])
def health():
        return jsonify({"status": "Ana Bot online", "paused": bot_paused}), 200


if __name__ == "__main__":
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
