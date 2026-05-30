from flask import Flask, request, jsonify
import requests
import os
import re

app = Flask(__name__)

INSTANCE_ID = "3F353F900771725020A0F6B0730C054E"
TOKEN = "2E4ECDD70099CF7EDCEAF35E"
ZAPI_BASE = f"https://api.z-api.io/instances/{INSThANCE_ID}/token/{TOKEN}"
CLIENT_TOKEN = "Fd7f15657ef534ae09757eefa5368120cS"

S_WELCOME="welcome"
S_WAITING_ORDER="waiting_order"
S_WAITING_IMAGES="waiting_images"
S_WAITING_EXTRA="waiting_extra_confirm"
S_WAITING_PIX="waiting_pix"

PRICES={"10X15":1.00,"15X21":1.50,"POLAROIDE":1.00,"A4":3.00,"IMA":2.50,"TAG":1.00,"ADESIVO":1.00,"CARTAO DE VISITA":1.00}
KEYWORDS_RULE_A=["tag","cartao de visita","adesivo"]
PRODUCT_FOLDERS=["10X15","15X21","A4","Mini foto","tirinhas","ima","mini ima","adesivo","tag","cartao de visita"]
LOJA_LINK="https://shopee.com.br/personalizei_fotografias?shop=1331254404"
MSG_FECHAMENTO="Perfeito, seu pedido ja esta sendo preparado. Segue o link da loja:\n"+LOJA_LINK
PIX_MSG="O valor das {n} fotos a mais e de R$ {valor:.2f}.\n\nSegue a chave PIX\nTitular: Rodrigo Vieira Monteiro\nChave PIX: 58733941000114\n\nApos efetuar o pagamento nos envie o comprovante."

sessions={}
def new_session():
    return {"state":S_WELCOME,"order":None,"images_count":0,"rule":None,"product_type":None,"qty_expected":None,"extra_count":0,"extra_value":0.0,"order_confirmed":False}
def get_session(phone):
    if phone not in sessions: sessions[phone]=new_session()
    return sessions[phone]
def reset_session(phone): sessions[phone]=new_session()
def send_text(phone,msg):
    try:
        r=requests.post(f"{ZAPI_BASE}/send-text",json={"phone":phone,"message":msg},headers={"Client-Token":CLIENT_TOKEN},timeout=15)
        return r.json()
    except Exception as e:
        print(f"Erro:{e}"); return {}
def is_paused(): return os.path.exists("/tmp/ana_paused")
def looks_like_order(t): return bool(re.search(r"\d{8,}",t.replace(" ","")))
def extract_order(t):
    m=re.search(r"\d{8,}",t.replace(" ","")); return m.group() if m else t.strip()
def is_rule_a(d): return any(kw in d.lower() for kw in KEYWORDS_RULE_A)
def detect_pt(d):
    for p in PRODUCT_FOLDERS:
        if p.lower() in d.lower(): return p
    return None
def ppu(pt):
    for k,v in PRICES.items():
        if k in pt.upper(): return v
    return 1.00
def extract_qty(d):
    m=re.search(r"\b(\d{1,3})\b",d); return int(m.group(1)) if m else None

@app.route("/webhook",methods=["POST"])
def webhook():
    data=request.json or {}
    if data.get("fromMe"): return jsonify({"status":"ok"})
    phone=data.get("phone","")
    if not phone: return jsonify({"status":"ok"})
    text=(data.get("text") or {}).get("message","").strip()
    is_image=bool(data.get("image") or data.get("document") or data.get("video"))
    if text.startswith("/pausar-ana"):
        open("/tmp/ana_paused","w").close(); return jsonify({"status":"paused"})
    if text.startswith("/retomar-ana"):
        if os.path.exists("/tmp/ana_paused"): os.remove("/tmp/ana_paused")
        return jsonify({"status":"active"})
    if text.startswith("/status-ana"):
        send_text(phone,f"Ana esta {'PAUSADA' if is_paused() else 'ATIVA'}."); return jsonify({"status":"ok"})
    if text.startswith("/pedido"):
        parts=text.split(maxsplit=3)
        if len(parts)>=4:
            s=get_session(parts[1])
            s["rule"]="A" if is_rule_a(parts[2]) else "B"
            s["product_type"]=detect_pt(parts[2]) or parts[2]
            try: s["qty_expected"]=int(parts[3])
            except: s["qty_expected"]=extract_qty(parts[2]) or 1
            s["order_confirmed"]=True
        return jsonify({"status":"ok"})
    if is_paused(): return jsonify({"status":"ok"})
    s=get_session(phone)
    if s["state"] in (S_WELCOME,S_WAITING_ORDER):
        if is_image:
            send_text(phone,"Por favor, preciso do numero do pedido"); s["state"]=S_WAITING_ORDER
        elif text and looks_like_order(text):
            order=extract_order(text); s["order"]=order; s["state"]=S_WAITING_IMAGES
            send_text(phone,f"Numero do pedido *{order}* recebido! Agora pode enviar as suas imagens.")
        else:
            send_text(phone,"Ola seja bem vindo e obrigado pela compra, meu nome e Ana e estou aqui para te ajudar a enviar as suas imagens. Antes de enviar as suas imagens, preciso que voce me envie o numero do seu pedido, e de extrema importancia que voce envie o numero do pedido antes de enviar as imagens!")
            s["state"]=S_WAITING_ORDER
    elif s["state"]==S_WAITING_IMAGES:
        if is_image:
            s["images_count"]+=1
            if s["order_confirmed"]: _apply_rules(phone,s)
            else: send_text(phone,f"Imagem {s['images_count']} recebida! Pode continuar enviando as demais.")
        elif text: send_text(phone,"Por favor, envie as imagens do seu pedido.")
    elif s["state"]==S_WAITING_EXTRA:
        resp=text.lower()
        if any(w in resp for w in ["sim","s","quero","yes","pode","ok"]):
            extra=s["extra_count"]; total=extra*ppu(s["product_type"] or "15X21")
            s["extra_value"]=total; s["state"]=S_WAITING_PIX
            send_text(phone,PIX_MSG.format(n=extra,valor=total))
        elif any(w in resp for w in ["nao","n","no"]):
            send_text(phone,"Tudo bem! Por favor, me indique quais imagens deseja descartar.")
            s["state"]=S_WAITING_IMAGES
        else: send_text(phone,"Por favor, responda Sim ou Nao.")
    elif s["state"]==S_WAITING_PIX:
        if is_image: send_text(phone,MSG_FECHAMENTO); reset_session(phone)
        elif text: send_text(phone,"Por favor, envie o comprovante do pagamento PIX.")
    return jsonify({"status":"ok"})

def _apply_rules(phone,s):
    count=s["images_count"]; expected=s["qty_expected"]
    if s["rule"]=="A":
        if count==1: send_text(phone,MSG_FECHAMENTO); reset_session(phone)
        else: send_text(phone,"Por favor envie apenas a imagem que deseja incluir no pedido.")
    elif s["rule"]=="B":
        if expected is None: send_text(phone,f"Imagem {count} recebida!"); return
        if count==expected: send_text(phone,MSG_FECHAMENTO); reset_session(phone)
        elif count<expected: send_text(phone,f"Ficou faltando {expected-count} imagem(ns).")
        else:
            extra=count-expected; s["extra_count"]=extra; s["state"]=S_WAITING_EXTRA
            send_text(phone,f"Voce enviou {extra} imagem(ns) a mais. Voce vai querer comprar as imagens a mais?")

@app.route("/",methods=["GET"])
def health(): return jsonify({"status":"online","ana":"pausada" if is_paused() else "ativa","sessoes":len(sessions)})

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
