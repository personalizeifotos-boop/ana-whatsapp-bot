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
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2.service_account import Credentials

BRASILIA = pytz.timezone("America/Sao_Paulo")

app = Flask(__name__)

# âââ ConfiguraÃ§Ãµes
TWILIO_WHATSAPP = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SPREADSHEET_ID = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"

PIX_INFO = "Titular: Rodrigo Vieira Monteiro\nChave PIX: 58733941000114"

# âââ Google Sheets
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
        ws = sh.add_worksheet(title="Pedidos", rows=1000, cols=12)
        ws.append_row([
            "NÃºmero do Pedido", "Data", "Produto", "Quantidade",
            "SKU", "Cliente", "Prazo de Entrega",
            "Telefone", "Status", "Obs"
        ])
    return ws

def salvar_pedido(numero_pedido, produto="â", quantidade="â", sku="â",
                  cliente="â", prazo="â", telefone="â",
                  status="Pagamento confirmado", obs=""):
    try:
        ws = get_sheet()
        if ws is None:
            return
        data = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        ws.append_row([numero_pedido, data, produto, quantidade,
                       sku, cliente, prazo, telefone, status, obs])
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
            ws.update_cell(cell.row, 9, novo_status)
            if obs:
                ws.update_cell(cell.row, 10, obs)
    except Exception as e:
        print(f"[Sheets] Erro ao atualizar: {e}")

def pedido_existe(numero_pedido):
    try:
        ws = get_sheet()
        if ws is None:
            return False
        cell = ws.find(numero_pedido)
        return cell is not None
    except Exception:
        return False

def info_pedido(numero_pedido):
    try:
        ws = get_sheet()
        if ws is None:
            return {}
        cell = ws.find(numero_pedido)
        if cell:
            row = ws.row_values(cell.row)
            return {
                "produto": row[2] if len(row) > 2 else "â",
                "quantidade": row[3] if len(row) > 3 else "â",
                "sku": row[4] if len(row) > 4 else "â",
                "cliente": row[5] if len(row) > 5 else "â",
                "row": cell.row,
            }
    except Exception:
        pass
    return {}

def atualizar_telefone(numero_pedido, telefone):
    try:
        ws = get_sheet()
        if ws is None:
            return
        cell = ws.find(numero_pedido)
        if cell:
            ws.update_cell(cell.row, 8, telefone)
    except Exception:
        pass

# âââ ExtraÃ§Ã£o do nÃºmero de pedido Shopee
PEDIDO_REGEX = re.compile(r'\b([A-Z0-9]{10,20})\b')

def extrair_numero_pedido(texto):
    candidatos = PEDIDO_REGEX.findall(texto.upper())
    for c in candidatos:
        tem_letra = any(ch.isalpha() for ch in c)
        tem_digito = any(ch.isdigit() for ch in c)
        if tem_letra and tem_digito:
            return c
    return candidatos[0] if candidatos else None

# âââ Thread IMAP â monitora Gmail a cada 60s
pedidos_processados = set()

def extrair_corpo_email(msg):
    """Extrai texto do email, tentando plain text primeiro, depois HTML."""
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
        # Remove tags HTML para obter texto simples
        corpo = re.sub(r'<[^>]+>', ' ', corpo_html)
        corpo = re.sub(r'&nbsp;', ' ', corpo)
        corpo = re.sub(r'&amp;', '&', corpo)
        corpo = re.sub(r'\s+', ' ', corpo)

    return corpo

def verificar_gmail():
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("[IMAP] Credenciais Gmail nÃ£o configuradas.")
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")
        # CORRIGIDO: FROM e SUBJECT corretos da Shopee Brasil
        _, msgs = mail.search(None, '(FROM "info@mail.shopee.com.br" SUBJECT "Hora de enviar")')
        ids = msgs[0].split()
        novos = 0
        for eid in ids:
            if eid in pedidos_processados:
                continue
            try:
                _, data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                assunto = msg.get("Subject", "")
                corpo = extrair_corpo_email(msg)

                # Extrai nÃºmero direto do assunto: "Hora de enviar o pedido XXXXXX"
                m_subj = re.search(r'pedido\s+([A-Z0-9]{10,20})', assunto, re.IGNORECASE)
                numero = m_subj.group(1).upper() if m_subj else (
                    extrair_numero_pedido(assunto) or extrair_numero_pedido(corpo)
                )

                if numero and not pedido_existe(numero):
                    produto = "â"
                    quantidade = "â"
                    sku = "â"
                    cliente = "â"
                    prazo = "â"

                    # Produto: linha/texto apÃ³s "ID do pedido:" (plain text ou HTML stripped)
                    m_prod = re.search(
                        r'ID do pedido:\s*#?' + re.escape(numero) + r'[\s\S]{0,50}?([A-Za-zÃ-Ãº][^\n\t]{10,})',
                        corpo, re.IGNORECASE
                    )
                    if m_prod:
                        produto = m_prod.group(1).strip().rstrip('.')

                    # Quantidade: nÃºmero apÃ³s "Quantidade"
                    m_qtd = re.search(r'Quantidade\s+(\d+)', corpo)
                    if m_qtd:
                        quantidade = m_qtd.group(1).strip()

                    # Cliente: "Envie o pedido para XXXX"
                    mc = re.search(r'Envie o pedido para ([^\.\n,]+)', corpo)
                    if mc:
                        cliente = mc.group(1).strip()

                    # Prazo de entrega
                    mp = re.search(r'(AtÃ© \d+ de \w+)', corpo)
                    if mp:
                        prazo = mp.group(1).strip()

                    salvar_pedido(
                        numero_pedido=numero, produto=produto,
                        quantidade=quantidade, sku=sku,
                        cliente=cliente, prazo=prazo,
                        status="Pagamento confirmado",
                        obs=f"Detectado via Gmail em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}"
                    )
                    novos += 1
            except Exception as e:
                print(f"[IMAP] Erro ao processar email {eid}: {e}")
            finally:
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

# âââ Estado das conversas
conversas = {}

def montar_info_produto(dados):
    sku = dados.get("sku", "")
    produto = dados.get("produto", "")
    quantidade = dados.get("quantidade", "")
    qtd_match = re.search(r'(\d+)\s*FOTO', sku.upper()) if sku else None
    qtd = qtd_match.group(1) if qtd_match else quantidade
    if qtd and qtd != "â" and produto and produto != "â":
        return f"\nSÃ£o {qtd} fotos\n{produto}"
    elif produto and produto != "â":
        return f"\n{produto}"
    return ""

def responder_ana(telefone, mensagem, tem_midia=False):
    msg_lower = mensagem.strip().lower()
    msg_original = mensagem.strip()

    estado = conversas.get(telefone, {"etapa": "inicio"})
    etapa = estado.get("etapa", "inicio")
    ultima_msg = estado.get("ultima_msg", "")

    # ââ Google Drive ââââââââââââââââââââââââââââââââââââââ
    if "drive.google.com" in msg_lower:
        resposta = (
            "Obrigada pelo link! ð\n"
            "Vou solicitar acesso no Drive no nome de *Ana Maria*.\n"
            "Assim que tiver acesso, confirmo as fotos."
        )
        estado["ultima_msg"] = resposta
        conversas[telefone] = estado
        return resposta

    # ââ PreÃ§o / pagamento ââââââââââââââââââââââââââââââââââââââââ
    palavras_preco = ["preÃ§o", "preco", "quanto", "custa", "valor", "pagar",
                      "pagamento", "pix", "transferÃªncia", "transferencia"]
    if any(p in msg_lower for p in palavras_preco):
        resposta = (
            "Nossos preÃ§os:\n\n"
            "ð· *Foto 10x15* â R$1,00/unidade\n"
            "ð· *Foto 15x21* â R$1,50/unidade\n"
            "ð§² *Foto ImÃ£ Geladeira* â R$2,50/unidade\n"
            "ðï¸ *Foto Polaroide* â R$1,50/unidade\n\n"
            f"Para pagamentos:\n{PIX_INFO}"
        )
        estado["ultima_msg"] = resposta
        conversas[telefone] = estado
        return resposta

    # ââ Cancelamento âââââââââââââââââââââââââââââââââââââââââââââ
    if "cancel" in msg_lower and any(p in msg_lower for p in ["comprar", "maior", "mais", "pacote"]):
        resposta = (
            "Sim, pode cancelar direto no app da Shopee! ð\n\n"
            "Mas se preferir, pode comprar a diferenÃ§a diretamente com a gente â "
            "aproveitamos esse pedido e enviamos tudo junto.\n\n"
            "ð· Foto 10x15: R$1,00/unidade\n"
            "ð· Foto 15x21: R$1,50/unidade\n"
            "ð§² Foto ImÃ£: R$2,50/unidade\n\n"
            "Quer continuar com a gente? Me diz quantas fotos quer no total! ð"
        )
        estado["ultima_msg"] = resposta
        conversas[telefone] = estado
        return resposta

    numero = extrair_numero_pedido(msg_original)

    if etapa == "inicio":
        if numero:
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                produto_info = montar_info_produto(dados)
                estado = {"etapa": "aguardando_imagens", "pedido": numero}
                resposta = (
                    f"OlÃ¡! ð Encontrei seu pedido *{numero}*."
                    f"{produto_info}\n\n"
                    "Me envie as fotos para prosseguirmos. Pode mandar todas de uma vez! ð¸"
                )
            else:
                resposta = (
                    f"Oi! O pedido *{numero}* ainda nÃ£o apareceu no nosso sistema.\n"
                    "Aguarde alguns minutos apÃ³s a confirmaÃ§Ã£o do pagamento e tente novamente. "
                    "Se o problema persistir, me avise! ð"
                )
        elif tem_midia:
            estado = {"etapa": "aguardando_pedido", "fotos_recebidas": 1}
            resposta = (
                "Obrigada pela foto! ð¸\n"
                "Para vincular ao seu pedido, me informe o *nÃºmero do pedido* da Shopee.\n"
                "VocÃª encontra no app em *Meus Pedidos*."
            )
        else:
            resposta = (
                "OlÃ¡! Sou a Ana da *Personalizei Fotos* ð¸\n\n"
                "Obrigada pela sua compra! Para comeÃ§ar, me informe o "
                "*nÃºmero do seu pedido* da Shopee.\n"
                "VocÃª encontra no app em *Meus Pedidos*."
            )

    elif etapa == "aguardando_pedido":
        if numero:
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                atualizar_status(numero, "Imagens recebidas",
                                 obs=f"Fotos recebidas em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}")
                estado = {"etapa": "imagens_recebidas", "pedido": numero}
                resposta = (
                    f"Perfeito! Pedido *{numero}* vinculado Ã s fotos. â\n"
                    "Nossa equipe jÃ¡ foi notificada e vai iniciar a produÃ§Ã£o em breve.\n"
                    "Prazo mÃ©dio de entrega: *3 a 5 dias Ãºteis*. Obrigada! ð"
                )
            else:
                resposta = (
                    f"O pedido *{numero}* ainda nÃ£o estÃ¡ no sistema.\n"
                    "Aguarde alguns minutos e tente novamente."
                )
        elif tem_midia:
            fotos = estado.get("fotos_recebidas", 0) + 1
            estado["fotos_recebidas"] = fotos
            resposta = None if "nÃºmero do pedido" in ultima_msg else (
                "Por favor, me informe o *nÃºmero do pedido* para continuar."
            )
        else:
            resposta = None if "nÃºmero do pedido" in ultima_msg else (
                "Por favor, me informe o *nÃºmero do pedido* da Shopee para continuar."
            )

    elif etapa == "aguardando_imagens":
        pedido = estado.get("pedido", "")
        if numero and numero != pedido:
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                produto_info = montar_info_produto(dados)
                estado = {"etapa": "aguardando_imagens", "pedido": numero}
                resposta = (
                    f"Ok! Mudando para o pedido *{numero}*."
                    f"{produto_info}\n\n"
                    "Me envie as fotos! ð·"
                )
            else:
                resposta = f"O pedido *{numero}* nÃ£o foi encontrado. Verifique o nÃºmero e tente novamente."
        elif tem_midia:
            fotos = estado.get("fotos_recebidas", 0) + 1
            estado["fotos_recebidas"] = fotos
            atualizar_status(pedido, "Imagens recebidas",
                             obs=f"Imagem recebida em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}")
            if fotos == 1:
                estado["etapa"] = "imagens_recebidas"
                resposta = (
                    "Imagens recebidas com sucesso â\n"
                    "Nossa equipe jÃ¡ foi notificada e vai iniciar a produÃ§Ã£o em breve.\n"
                    "Prazo mÃ©dio de entrega: *3 a 5 dias Ãºteis*. Obrigada! ð"
                )
            else:
                resposta = None
        else:
            resposta = None

    elif etapa == "imagens_recebidas":
        pedido = estado.get("pedido", "")
        if tem_midia:
            fotos = estado.get("fotos_recebidas", 0) + 1
            estado["fotos_recebidas"] = fotos
            atualizar_status(pedido, "Imagens recebidas",
                             obs=f"Imagem adicional em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}")
            resposta = None
        elif numero and numero != pedido:
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                produto_info = montar_info_produto(dados)
                estado = {"etapa": "aguardando_imagens", "pedido": numero}
                resposta = (
                    f"OlÃ¡! Abrindo atendimento para o pedido *{numero}*."
                    f"{produto_info}\n\n"
                    "Me envie as fotos! ð¸"
                )
            else:
                resposta = f"O pedido *{numero}* nÃ£o foi encontrado."
        else:
            resposta = "Suas fotos estÃ£o em produÃ§Ã£o! ð Se precisar de algo, estou aqui."

    else:
        estado = {"etapa": "inicio"}
        resposta = (
            "OlÃ¡! Para um novo atendimento, me informe o *nÃºmero do seu pedido* da Shopee. ð"
        )

    if resposta:
        estado["ultima_msg"] = resposta
    conversas[telefone] = estado
    return resposta

# âââ Webhook WhatsApp âââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    telefone = request.form.get("From", "")
    mensagem = request.form.get("Body", "") or ""
    tem_midia = bool(request.form.get("MediaUrl0", ""))

    resposta_texto = responder_ana(telefone, mensagem, tem_midia=tem_midia)

    resp = MessagingResponse()
    if resposta_texto:
        resp.message(resposta_texto)
    return str(resp)

@app.route("/", methods=["GET"])
def health():
    return "Ana Bot OK", 200

# Inicia thread IMAP no nÃ­vel do mÃ³dulo â funciona com gunicorn E flask dev
_imap_thread = threading.Thread(target=thread_gmail, daemon=True)
_imap_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
