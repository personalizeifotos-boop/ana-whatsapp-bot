import os
import re
import json
import imaplib
import email
import threading
import time
import urllib.request as _url_req
import gspread
import pytz
from datetime import datetime
from flask import Flask, request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

BRASILIA = pytz.timezone("America/Sao_Paulo")
app = Flask(__name__)

# ── Configurações ────────────────────────────────────────────
GMAIL_USER         = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SPREADSHEET_ID     = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"
ZAPI_INSTANCE      = "3F353F900771725020A0F6B0730C054E"
ZAPI_TOKEN         = "2E4ECDD70099CF7EDCEAF35E"
ZAPI_BASE_URL      = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}"

# ── Mensagens da Ana ─────────────────────────────────────────
MSG_SAUDACAO = (
    "Olá, seja bem-vindo à Personalizei! Obrigado pela sua compra. 😊\n\n"
    "Antes de enviar qualquer imagem, é de extrema importância que você nos envie primeiro "
    "o número do pedido. Esse número está logo após as letras ID: no seu comprovante de compra.\n\n"
    "Por favor, digite ou copie e cole o número — não envie print, pois nosso sistema "
    "não consegue identificar imagens de texto."
)
MSG_PEDIR_PEDIDO = (
    "Por favor envie o número do pedido, esse número vem logo depois das letras ID:, "
    "você pode encontrar esse número no seu histórico de pedidos, "
    "sem esse número não conseguimos te identificar."
)
MSG_FINALIZAR = (
    "Perfeito, seu pedido já está sendo preparado e será enviado no menor tempo possível. "
    "Segue abaixo o link da nossa loja, caso queira conhecer todos os nossos produtos: "
    "https://shopee.com.br/personalizei_fotografias?located_flash_sale=238855072727041"
    "&share_from=isfs&shop=1331254404&stm_medium=referral&stm_source=rw&tab=5"
    "&uls_trackid=55dmrh0c006m"
)
MSG_PIX = (
    "Segue a chave PIX\n"
    "Titular: Rodrigo Vieira Monteiro\n"
    "Chave PIX: 58733941000114\n"
    "Após efetuar o pagamento pela chave PIX nos envie o comprovante por favor."
)

# ── Tabela de preços por foto extra ─────────────────────────
PRECOS_EXTRA = {
    "10X15":         1.00,
    "15X21":         1.50,
    "Mini foto":     1.00,
    "A4":            3.00,
    "Ima":           2.50,
    "Mini ima":      2.50,
    "Imã":           2.50,
    "Polaroide":     1.00,
    "Tirinha":       1.00,
    "Cartao de Visita": 1.00,
}

MAPEAMENTO_TIPO = [
    ("MINI IMA",         "Mini ima"),
    ("MINI FOTO",        "Mini foto"),
    ("CARTAO DE VISITA", "Cartao de Visita"),
    ("TIRINHA",          "Tirinha"),
    ("POLAROIDE",        "Polaroide"),
    ("ETIQUETA",         "Etiqueta"),
    ("15X21",            "15X21"),
    ("15 X 21",          "15X21"),
    ("10X15",            "10X15"),
    ("10 X 15",          "10X15"),
    ("IMA",              "Ima"),
    ("A4",               "A4"),
    ("TAG",              "Tag"),
]

PEDIDO_REGEX = re.compile(r'\b([A-Z0-9]{10,20})\b')

# ── Estado em memória por telefone ───────────────────────────
# Status possíveis:
#   novo              → primeiro contato, saudação ainda não enviada
#   aguardando_pedido → saudação enviada, esperando número do pedido
#   aguardando_fotos  → pedido vinculado, recebendo fotos
#   aguardando_resposta_extras → perguntou sobre fotos a mais, aguardando sim/não
#   aguardando_descarte        → cliente disse não, aguardando indicar quais descartar
#   aguardando_pagamento       → enviou cobrança PIX, aguardando comprovante
#   concluido                  → fluxo encerrado

estado_clientes = {}   # phone → dict de estado
timers_ativos   = {}   # phone → threading.Timer
telefone_pedido = {}   # legado: phone → pedido


def get_estado(phone):
    if phone not in estado_clientes:
        estado_clientes[phone] = {
            "status":            "novo",
            "pedido":            "",
            "produto":           "",
            "sku":               "",
            "limite_fotos":      0,
            "fotos_recebidas":   0,
            "imgs_antes_pedido": 0,
            "fotos_extras":      0,
            "valor_extra":       0.0,
        }
    return estado_clientes[phone]


def identificar_tipo(produto, sku):
    texto = (produto + " " + sku).upper()
    for orig, sub in [("Ã","A"),("Â","A"),("Á","A"),("À","A"),("É","E"),
                      ("Ê","E"),("Í","I"),("Ó","O"),("Ô","O"),("Õ","O"),
                      ("Ú","U"),("Ç","C")]:
        texto = texto.replace(orig, sub)
    for chave, tipo in MAPEAMENTO_TIPO:
        if chave in texto:
            return tipo
    return "10X15"


def extrair_limite_fotos(sku):
    m = re.search(r'(\d+)\s*fotos?', sku, re.IGNORECASE)
    return int(m.group(1)) if m else 0


# ── Z-API: envio de mensagens ─────────────────────────────────
def enviar_mensagem(phone, mensagem):
    phone_num = re.sub(r'\D', '', phone)
    url = f"{ZAPI_BASE_URL}/send-text"
    payload = json.dumps({"phone": phone_num, "message": mensagem}).encode()
    req = _url_req.Request(url, data=payload,
                           headers={
                               "Content-Type": "application/json",
                               "Client-Token": "Fd7f15657ef534ae09757eefa5368120cS"
                           })
    try:
        with _url_req.urlopen(req, timeout=15):
            print(f"[Z-API] ✓ → {phone_num}: {mensagem[:80]}...")
            return True
    except Exception as e:
        print(f"[Z-API] ✗ Erro para {phone_num}: {e}")
        return False


# ── Google Drive ──────────────────────────────────────────────
def _upload_imagem_drive(image_url, phone):
    try:
        req = _url_req.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with _url_req.urlopen(req, timeout=15) as resp:
            image_bytes = resp.read()
        if len(image_bytes) < 500:
            print(f"[Drive] Imagem muito pequena ({len(image_bytes)}B) — URL expirada?")
            return image_url
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            return image_url
        creds = Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        service = build("drive", "v3", credentials=creds)
        timestamp   = datetime.now(BRASILIA).strftime("%Y%m%d_%H%M%S")
        phone_clean = re.sub(r'\D', '', phone)
        filename    = f"foto_{phone_clean}_{timestamp}.jpg"
        media     = MediaInMemoryUpload(image_bytes, mimetype="image/jpeg")
        file_obj  = service.files().create(
            body={"name": filename}, media_body=media, fields="id"
        ).execute()
        file_id = file_obj.get("id")
        service.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"}
        ).execute()
        drive_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        print(f"[Drive] ✓ {filename} ({len(image_bytes)//1024}KB)")
        return drive_url
    except Exception as e:
        print(f"[Drive] Erro: {e}")
        return image_url


# ── Google Sheets ─────────────────────────────────────────────
def _gc():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        return None
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        json.loads(creds_json), scopes=scopes
    )
    return gspread.authorize(creds)


def get_sheet(nome="Pedidos", colunas=None):
    gc = _gc()
    if gc is None:
        return None
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(nome)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=nome, rows=1000,
                              cols=len(colunas or []) + 2)
        if colunas:
            ws.append_row(colunas)
        return ws


def buscar_pedido_na_planilha(numero):
    try:
        ws = get_sheet("Pedidos")
        if ws is None:
            return None
        for linha in ws.get_all_values()[1:]:
            if linha and linha[0].strip().upper() == numero.upper():
                return {
                    "pedido":  linha[0].strip(),
                    "produto": linha[2].strip() if len(linha) > 2 else "",
                    "sku":     linha[4].strip() if len(linha) > 4 else "",
                    "cliente": linha[5].strip() if len(linha) > 5 else "",
                }
    except Exception as e:
        print(f"[Sheets] Erro ao buscar pedido: {e}")
    return None


def pedido_existe(numero):
    try:
        ws = get_sheet("Pedidos")
        if ws is None:
            return False
        return numero.upper() in [
            v.strip().upper() for v in ws.col_values(1) if v.strip()
        ]
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
        data = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
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
        data = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        ws.append_row([phone, image_url, data, "pendente", pedido])
        print(f"[Imagens] Registrada: {phone} (pedido: {pedido or 'nao vinculado'})")
    except Exception as e:
        print(f"[Imagens] Erro ao registrar: {e}")


def preencher_pedido_retroativo(phone, numero_pedido):
    """Preenche coluna E (Pedido) nas linhas sem pedido do telefone. Retorna contagem."""
    try:
        ws = get_sheet("Imagens")
        if ws is None:
            return 0
        linhas = ws.get_all_values()
        suf = re.sub(r'\D', '', phone)
        suf = suf[-11:] if len(suf) >= 11 else suf
        updates = []
        for i, linha in enumerate(linhas[1:], start=2):
            tel = re.sub(r'\D', '', linha[0].strip()) if linha else ""
            tel_suf = tel[-11:] if len(tel) >= 11 else tel
            pedido_col = linha[4].strip() if len(linha) >= 5 else ""
            if suf == tel_suf and not pedido_col:
                updates.append({'range': f'E{i}', 'values': [[numero_pedido]]})
        if updates:
            ws.batch_update(updates)
            print(f"[Imagens] {len(updates)} imagens → pedido {numero_pedido}")
        return len(updates)
    except Exception as e:
        print(f"[Imagens] Erro retroativo: {e}")
        return 0


# ── Timers ────────────────────────────────────────────────────
def cancelar_timer(phone):
    t = timers_ativos.get(phone)
    if t:
        t.cancel()
        timers_ativos.pop(phone, None)


def iniciar_timer(phone, segundos, callback):
    cancelar_timer(phone)
    t = threading.Timer(segundos, callback)
    t.daemon = True
    t.start()
    timers_ativos[phone] = t


# ── Lógica de conversa ────────────────────────────────────────
def pedir_numero_pedido_timer(phone):
    """Callback do timer de 30s: pede número do pedido se ainda não recebido."""
    estado = get_estado(phone)
    if not estado["pedido"]:
        enviar_mensagem(phone, MSG_PEDIR_PEDIDO)
        print(f"[Ana] Timer 30s: pediu número do pedido para {phone}")


def verificar_inatividade_fotos(phone):
    """Callback do timer de 10min: avalia fotos se ainda aguardando."""
    estado = get_estado(phone)
    if estado["status"] != "aguardando_fotos":
        return
    limite    = estado["limite_fotos"]
    recebidas = estado["fotos_recebidas"]
    if limite > 0 and recebidas < limite:
        faltam = limite - recebidas
        enviar_mensagem(phone, f"Ficou faltando {faltam} imagem(ns).")
        print(f"[Ana] Timer 10min: faltando {faltam} fotos para {phone}")


def avaliar_conclusao(phone):
    """Avalia se faltou, sobrou ou foi exato. Dispara mensagens adequadas."""
    estado    = get_estado(phone)
    limite    = estado["limite_fotos"]
    recebidas = estado["fotos_recebidas"]
    tipo      = identificar_tipo(estado["produto"], estado["sku"])

    if limite == 0:
        enviar_mensagem(phone, MSG_FINALIZAR)
        estado["status"] = "concluido"
        return

    if recebidas == limite:
        enviar_mensagem(phone, f"Perfeito, {limite} fotos {tipo}")
        enviar_mensagem(phone, MSG_FINALIZAR)
        estado["status"] = "concluido"
        cancelar_timer(phone)

    elif recebidas > limite:
        extras = recebidas - limite
        estado["fotos_extras"] = extras
        preco  = PRECOS_EXTRA.get(tipo, 1.00)
        valor  = round(extras * preco, 2)
        estado["valor_extra"] = valor
        enviar_mensagem(
            phone,
            f"Você enviou {extras} imagem(ns) a mais. "
            "Você vai querer comprar as imagens a mais?"
        )
        estado["status"] = "aguardando_resposta_extras"
        cancelar_timer(phone)

    elif recebidas < limite:
        # Ainda aguardando — timer de inatividade já rodando, não faz nada agora
        pass


def vincular_pedido(phone, numero_pedido):
    """Vincula pedido ao telefone, atualiza planilha e envia confirmação."""
    dados = buscar_pedido_na_planilha(numero_pedido)
    if not dados:
        print(f"[Ana] Pedido {numero_pedido} não encontrado na planilha")
        return False

    estado = get_estado(phone)
    produto = dados.get("produto", "")
    sku     = dados.get("sku", "")
    tipo    = identificar_tipo(produto, sku)
    limite  = extrair_limite_fotos(sku)

    estado["pedido"]       = numero_pedido
    estado["produto"]      = produto
    estado["sku"]          = sku
    estado["limite_fotos"] = limite
    estado["status"]       = "aguardando_fotos"

    telefone_pedido[phone] = numero_pedido
    atualizar_telefone_na_planilha(numero_pedido, phone)

    # Vincula retroativamente as imagens que chegaram antes do pedido
    qtd_retro = preencher_pedido_retroativo(phone, numero_pedido)
    if qtd_retro > 0:
        estado["fotos_recebidas"] = qtd_retro
        print(f"[Ana] {qtd_retro} fotos retroativas para {phone}")

    # Confirmação para o cliente
    if limite > 0:
        enviar_mensagem(phone, f"Pedido identificado com sucesso! 😊 Agora é só enviar suas {limite} fotos para darmos continuidade ao seu pedido.")
    else:
        enviar_mensagem(phone, f"Pedido identificado com sucesso! 😊 Pode enviar suas fotos para darmos continuidade ao seu pedido.")

    print(f"[Ana] Pedido {numero_pedido} vinculado: limite={limite} tipo={tipo}")

    # Avalia se as fotos retroativas já completaram ou ultrapassaram o limite
    if limite > 0 and qtd_retro >= limite:
        avaliar_conclusao(phone)
    elif qtd_retro > 0 and qtd_retro < limite:
        iniciar_timer(phone, 600, lambda: verificar_inatividade_fotos(phone))

    return True


def processar_imagem_recebida(phone, image_url):
    """Processa imagem recebida: salva no Drive/Sheets e conta para o SKU."""
    estado = get_estado(phone)

    # Ignora fotos de pedidos já concluídos
    if estado["status"] == "concluido":
        print(f"[Ana] Pedido concluído — imagem de {phone} ignorada")
        return

    # Upload para Drive (URL permanente)
    drive_url = _upload_imagem_drive(image_url, phone)
    pedido    = estado.get("pedido", "")
    salvar_imagem_pendente(phone, drive_url, pedido)

    if pedido:
        # Pedido vinculado — conta a foto
        estado["fotos_recebidas"] += 1
        fotos  = estado["fotos_recebidas"]
        limite = estado["limite_fotos"]
        print(f"[Ana] {phone}: {fotos}/{limite} fotos")

        if limite > 0 and fotos >= limite:
            cancelar_timer(phone)
            avaliar_conclusao(phone)
        else:
            # Reinicia timer de inatividade de 10 minutos
            iniciar_timer(phone, 600, lambda: verificar_inatividade_fotos(phone))
    else:
        # Sem pedido ainda — incrementa contador e (re)inicia timer de 30s
        estado["imgs_antes_pedido"] += 1
        estado["status"] = "aguardando_pedido"
        iniciar_timer(phone, 30, lambda: pedir_numero_pedido_timer(phone))
        print(f"[Ana] {phone}: imagem sem pedido ({estado['imgs_antes_pedido']}ª)")


def processar_texto_recebido(phone, body):
    """Processa mensagem de texto recebida."""
    estado    = get_estado(phone)
    status    = estado["status"]
    body_low  = body.lower().strip()

    # ── Resposta sobre fotos extras ──────────────────────────
    if status == "aguardando_resposta_extras":
        if any(p in body_low for p in ["sim", "yes", "quero", "s"]):
            extras = estado["fotos_extras"]
            valor  = estado["valor_extra"]
            tipo   = identificar_tipo(estado["produto"], estado["sku"])
            enviar_mensagem(
                phone,
                f"O valor das {extras} foto(s) a mais é de R$ {valor:.2f}.\n{MSG_PIX}"
            )
            estado["status"] = "aguardando_pagamento"

        elif any(p in body_low for p in ["não", "nao", "nã", "no", "n"]):
            limite = estado["limite_fotos"]
            enviar_mensagem(
                phone,
                f"Tudo bem! Por favor nos indique quais fotos devem ser descartadas "
                f"para ficarmos com apenas {limite} foto(s)."
            )
            estado["status"] = "aguardando_descarte"
        return

    # ── Comprovante de pagamento (texto) — ignorado ──────────
    if status == "aguardando_pagamento":
        return

    # ── Tenta extrair número do pedido ───────────────────────
    numero = extrair_numero_pedido(body)
    if numero and pedido_existe(numero):
        cancelar_timer(phone)
        vincular_pedido(phone, numero)
        print(f"[Webhook] Pedido {numero} vinculado ao telefone {phone}")
    else:
        print(f"[Ana] Texto não reconhecido de {phone}: {body[:60]}")


# ── Extração do número de pedido ─────────────────────────────
def extrair_numero_pedido(texto):
    candidatos = PEDIDO_REGEX.findall(texto.upper())
    for c in candidatos:
        if any(ch.isalpha() for ch in c) and any(ch.isdigit() for ch in c):
            return c
    return candidatos[0] if candidatos else None


# ── Thread IMAP — monitora Gmail ─────────────────────────────
pedidos_processados = set()


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
                pedidos_na_planilha = set(
                    v.strip().upper() for v in ws.col_values(1) if v.strip()
                )
        except Exception as e:
            print(f"[IMAP] Aviso planilha: {e}")

        novos = 0
        for eid in ids:
            if eid in pedidos_processados:
                continue
            try:
                _, data = mail.fetch(eid, "(RFC822)")
                msg     = email.message_from_bytes(data[0][1])
                assunto = msg.get("Subject", "")
                corpo   = extrair_corpo_email(msg)

                m_subj = re.search(r'pedido\s+([A-Z0-9]{10,20})', assunto, re.IGNORECASE)
                numero = (m_subj.group(1).upper() if m_subj
                          else (extrair_numero_pedido(assunto) or extrair_numero_pedido(corpo)))

                if numero and numero.upper() not in pedidos_na_planilha:
                    produto = quantidade = sku = cliente = prazo = ""

                    m_prod = re.search(
                        r'ID do pedido:\s*#?' + re.escape(numero) +
                        r'[\s\S]{0,50}?([A-Za-zÀ-ú][^\n\t]{10,})',
                        corpo, re.IGNORECASE
                    )
                    if m_prod:
                        produto = m_prod.group(1).strip().rstrip('.')

                    m_qtd = re.search(r'Quantidade\s+(\d+)', corpo)
                    if m_qtd:
                        quantidade = m_qtd.group(1).strip()

                    m_sku = re.search(r'Varia[ção]{2,4}[:\s]+([^\n\t<]{3,60})', corpo, re.IGNORECASE)
                    if not m_sku:
                        m_sku = re.search(r'SKU[:\s]+([^\n\t<]{3,60})', corpo, re.IGNORECASE)
                    if m_sku:
                        sku = re.sub(r'^\d+[-\s]+', '', m_sku.group(1).strip())
                    if not sku:
                        m_kit = re.search(r'(KIT\s+(?:AT[EÉ]\s+)?\d+\s+FOTOS?)', corpo, re.IGNORECASE)
                        if m_kit:
                            sku = m_kit.group(1).strip().upper()
                    m_num = re.search(r'(\d+)\s*FOTO', sku.upper())
                    if m_num:
                        sku = m_num.group(1) + ' fotos'

                    mc = re.search(r'Envie o pedido para ([^\.\n,]+)', corpo)
                    if mc:
                        cliente = mc.group(1).strip()
                    mp = re.search(r'(At[eé] \d+ de \w+)', corpo, re.IGNORECASE)
                    if mp:
                        prazo = mp.group(1).strip()

                    salvar_pedido(
                        numero_pedido=numero, produto=produto,
                        quantidade=quantidade, sku=sku,
                        cliente=cliente, prazo=prazo,
                        status="Pagamento confirmado",
                        obs=f"Gmail {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}"
                    )
                    pedidos_na_planilha.add(numero.upper())
                    novos += 1
                    time.sleep(3)

            except Exception as e:
                print(f"[IMAP] Erro email {eid}: {e}")
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


# ── Webhook WhatsApp ──────────────────────────────────────────
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f"[Webhook] PAYLOAD: {json.dumps(data)[:400]}")

        if data.get("fromMe", False):
            return "ok", 200

        phone = (data.get("phone", "")
                 .replace("@s.whatsapp.net", "")
                 .replace("@c.us", ""))
        if not phone:
            return "ok", 200

        msg_type = data.get("type") or data.get("tipo") or ""

        def extrair_texto(d):
            v = d.get("body") or d.get("text") or d.get("texto") or ""
            if isinstance(v, dict):
                return v.get("message") or v.get("body") or v.get("text") or ""
            return str(v) if v else ""

        def extrair_image_url(d):
            for chave in ("imagem", "image"):
                v = d.get(chave)
                if isinstance(v, dict):
                    return v.get("imageUrl") or v.get("url") or v.get("mediaUrl") or ""
                if isinstance(v, str) and v.startswith("http"):
                    return v
            return d.get("imageUrl") or d.get("mediaUrl") or ""

        body       = extrair_texto(data)
        tem_imagem = (
            msg_type in ("image", "imagem")
            or "image" in data
            or "imagem" in data
            or (isinstance(body, str) and body.startswith("http")
                and any(ext in body.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]))
        )
        image_url = ""
        if tem_imagem:
            image_url = body if (body and body.startswith("http")) else extrair_image_url(data)

        print(f"[Webhook] phone={phone} tipo={msg_type} imagem={tem_imagem} body={str(body)[:60]}")

        # ── Saudação automática (primeiro contato) ───────────
        estado = get_estado(phone)
        if estado["status"] == "novo":
            enviar_mensagem(phone, MSG_SAUDACAO)
            estado["status"] = "aguardando_pedido"

        # ── Processa imagem ──────────────────────────────────
        if tem_imagem and image_url:
            processar_imagem_recebida(phone, image_url)

        # ── Processa texto ───────────────────────────────────
        elif body and not body.startswith("http"):
            processar_texto_recebido(phone, body)

        return "ok", 200

    except Exception as e:
        import traceback
        print(f"[Webhook] Erro: {e}")
        traceback.print_exc()
        return "ok", 200


@app.route("/", methods=["GET"])
def health():
    return "Ana Bot OK", 200


_imap_thread = threading.Thread(target=thread_gmail, daemon=True)
_imap_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
