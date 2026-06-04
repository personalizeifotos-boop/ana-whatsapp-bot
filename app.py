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

app = Flask(__name__)

GMAIL_USER         = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SPREADSHEET_ID     = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"

# Mapeamento em memoria: telefone -> numero_pedido
telefone_pedido = {}

# ── Google Sheets ────────────────────────────────────────────

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


def buscar_pedido_por_telefone(phone):
    """Busca na aba Pedidos se o telefone ja esta vinculado a algum pedido."""
    try:
        ws = get_sheet("Pedidos")
        if ws is None:
            return ""
        linhas = ws.get_all_values()
        phone_norm = re.sub(r'\D', '', phone)
        suf = phone_norm[-11:] if len(phone_norm) >= 11 else phone_norm
        for linha in linhas[1:]:
            if len(linha) >= 8 and linha[7].strip():
                tel = re.sub(r'\D', '', linha[7].strip())
                tel_suf = tel[-11:] if len(tel) >= 11 else tel
                if suf == tel_suf:
                    return linha[0].strip()
    except Exception as e:
        print(f"[Webhook] Erro ao buscar telefone na planilha: {e}")
    return ""


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
        data = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        ws.append_row([numero_pedido, data, produto, quantidade,
                       sku, cliente, prazo, telefone, status, obs])
        print(f"[Sheets] Pedido {numero_pedido} salvo.")
    except Exception as e:
        print(f"[Sheets] Erro ao salvar pedido: {e}")
        raise


def preencher_pedido_retroativo(phone, numero_pedido):
    """Preenche coluna E (Pedido) em todas as linhas da aba Imagens
    que tenham o mesmo telefone e coluna E vazia."""
    try:
        ws = get_sheet("Imagens")
        if ws is None:
            return
        linhas = ws.get_all_values()
        phone_norm = re.sub(r'\D', '', phone)
        suf = phone_norm[-11:] if len(phone_norm) >= 11 else phone_norm
        updates = []
        for i, linha in enumerate(linhas[1:], start=2):  # linha 2 em diante (pula cabecalho)
            tel = re.sub(r'\D', '', linha[0].strip()) if linha else ""
            tel_suf = tel[-11:] if len(tel) >= 11 else tel
            pedido_atual = linha[4].strip() if len(linha) >= 5 else ""
            if suf == tel_suf and not pedido_atual:
                updates.append({'range': f'E{i}', 'values': [[numero_pedido]]})
        if updates:
            ws.batch_update(updates)
            print(f"[Imagens] {len(updates)} imagens retroativas vinculadas ao pedido {numero_pedido} ({phone})")
    except Exception as e:
        print(f"[Imagens] Erro ao preencher retroativo: {e}")


def salvar_imagem_pendente(phone, image_url, pedido=""):
    try:
        ws = get_sheet("Imagens", ["Telefone", "URL", "Data", "Status", "Pedido"])
        if ws is None:
            return
        data = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        ws.append_row([phone, image_url, data, "pendente", pedido])
        print(f"[Imagens] Registrada imagem de {phone} (pedido: {pedido or 'nao vinculado'})")
    except Exception as e:
        print(f"[Imagens] Erro ao registrar: {e}")


# ── Extracao do numero de pedido Shopee ─────────────────────────

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


# ── Thread IMAP – monitora Gmail ──────────────────────────

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
                corpo   = extrair_corpo_email(msg)

                m_subj = re.search(r'pedido\s+([A-Z0-9]{10,20})', assunto, re.IGNORECASE)
                numero = (m_subj.group(1).upper() if m_subj
                          else (extrair_numero_pedido(assunto) or extrair_numero_pedido(corpo)))

                if numero and numero.upper() not in pedidos_na_planilha:
                    produto    = ""
                    quantidade = ""
                    sku        = ""
                    cliente    = ""
                    prazo      = ""

                    m_prod = re.search(
                        r'ID do pedido:\s*#?' + re.escape(numero) + r'[\s\S]{0,50}?([A-Za-z\xC0-\xFF][^\n\t]{10,})',
                        corpo, re.IGNORECASE
                    )
                    if m_prod:
                        produto = m_prod.group(1).strip().rstrip('.')

                    m_qtd = re.search(r'Quantidade\s+(\d+)', corpo)
                    if m_qtd:
                        quantidade = m_qtd.group(1).strip()

                    m_sku = re.search(r'Varia[\xE7\xE3o]{2,4}[:\s]+([^\n\t<]{3,60})', corpo, re.IGNORECASE)
                    if not m_sku:
                        m_sku = re.search(r'SKU[:\s]+([^\n\t<]{3,60})', corpo, re.IGNORECASE)
                    if m_sku:
                        sku = re.sub(r'^\d+[-\s]+', '', m_sku.group(1).strip())

                    if not sku:
                        m_kit = re.search(r'(KIT\s+(?:AT[E\xC9]\s+)?\d+\s+FOTOS?)', corpo, re.IGNORECASE)
                        if m_kit:
                            sku = m_kit.group(1).strip().upper()

                    m_num = re.search(r'(\d+)\s*FOTO', sku.upper())
                    if m_num:
                        sku = m_num.group(1) + ' fotos'

                    mc = re.search(r'Envie o pedido para ([^\.\n,]+)', corpo)
                    if mc:
                        cliente = mc.group(1).strip()

                    mp = re.search(r'(At[e\xE9] \d+ de \w+)', corpo, re.IGNORECASE)
                    if mp:
                        prazo = mp.group(1).strip()

                    salvar_pedido(
                        numero_pedido=numero, produto=produto,
                        quantidade=quantidade, sku=sku,
                        cliente=cliente, prazo=prazo,
                        status="Pagamento confirmado",
                        obs=f"Detectado via Gmail em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}"
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


# ── Webhook WhatsApp (Z-API) ──────────────────────────────

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    try:
        data = request.get_json(force=True, silent=True) or {}

        # Log completo
        print(f"[Webhook] PAYLOAD: {json.dumps(data)[:800]}")

        # Evolution API usa fromMe em ingles mas outros campos em portugues
        if data.get("fromMe", False):
            return "ok", 200

        phone = (data.get("phone", "")
                 .replace("@s.whatsapp.net", "")
                 .replace("@c.us", ""))

        # Evolution API: "tipo" (PT) ou "type" (EN)
        msg_type = data.get("type") or data.get("tipo") or ""

        # Evolution API: "text" e "imagem" sao dicionarios aninhados
        def extrair_texto(d):
            v = d.get("body") or d.get("text") or d.get("texto") or ""
            if isinstance(v, dict):
                return v.get("message") or v.get("body") or v.get("text") or ""
            return str(v) if v else ""

        def extrair_image_url_aninhado(d):
            for chave in ("imagem", "image"):
                v = d.get(chave)
                if isinstance(v, dict):
                    return v.get("imageUrl") or v.get("url") or v.get("mediaUrl") or ""
                if isinstance(v, str) and v.startswith("http"):
                    return v
            return d.get("imageUrl") or d.get("mediaUrl") or ""

        body = extrair_texto(data)

        # Detecta imagem — Evolution API usa "imagem" (PT), Z-API usa "image" (EN)
        tem_imagem = (
            msg_type in ("image", "imagem")
            or "image" in data
            or "imagem" in data
            or (isinstance(body, str) and body.startswith("http") and any(ext in body.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]))
        )

        # URL da imagem: tenta todos os campos possiveis
        image_url = ""
        if tem_imagem:
            if body and body.startswith("http"):
                image_url = body
            else:
                image_url = extrair_image_url_aninhado(data)

        print(f"[Webhook] phone={phone} tipo={msg_type} tem_imagem={tem_imagem} image_url={image_url[:80] if image_url else ''} body={str(body)[:60]}")

        if tem_imagem and image_url:
            pedido_vinculado = telefone_pedido.get(phone, "")
            if not pedido_vinculado:
                # Fallback: busca na planilha se o telefone ja esta vinculado
                pedido_vinculado = buscar_pedido_por_telefone(phone)
                if pedido_vinculado:
                    telefone_pedido[phone] = pedido_vinculado
                    print(f"[Webhook] Pedido {pedido_vinculado} encontrado na planilha para {phone}")
            salvar_imagem_pendente(phone, image_url, pedido_vinculado)

        elif body and not body.startswith("http"):
            numero = extrair_numero_pedido(body)
            if numero and pedido_existe(numero):
                telefone_pedido[phone] = numero
                atualizar_telefone_na_planilha(numero, phone)
                print(f"[Webhook] Pedido {numero} vinculado ao telefone {phone}")
                preencher_pedido_retroativo(phone, numero)

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
