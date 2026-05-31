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

# ─── Configurações
TWILIO_WHATSAPP = os.environ.get("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SPREADSHEET_ID = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"

PIX_INFO = "Titular: Rodrigo Vieira Monteiro\nChave PIX: 58733941000114"


# ─── Google Sheets
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
            "Número do Pedido", "Data", "Produto", "Quantidade",
            "SKU", "Cliente", "Prazo de Entrega",
            "Telefone", "Status", "Obs"
        ])
    return ws


def salvar_pedido(numero_pedido, produto="—", quantidade="—", sku="—",
                  cliente="—", prazo="—", telefone="—",
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
    """Retorna dict com dados do pedido da planilha."""
    try:
        ws = get_sheet()
        if ws is None:
            return {}
        cell = ws.find(numero_pedido)
        if cell:
            row = ws.row_values(cell.row)
            return {
                "produto": row[2] if len(row) > 2 else "—",
                "quantidade": row[3] if len(row) > 3 else "—",
                "sku": row[4] if len(row) > 4 else "—",
                "cliente": row[5] if len(row) > 5 else "—",
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


# ─── Extração do número de pedido Shopee
PEDIDO_REGEX = re.compile(r'\b([A-Z0-9]{10,20})\b')


def extrair_numero_pedido(texto):
    candidatos = PEDIDO_REGEX.findall(texto.upper())
    for c in candidatos:
        tem_letra = any(ch.isalpha() for ch in c)
        tem_digito = any(ch.isdigit() for ch in c)
        if tem_letra and tem_digito:
            return c
    return candidatos[0] if candidatos else None


# ─── Thread IMAP — monitora Gmail a cada 60s
pedidos_processados = set()


def verificar_gmail():
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("[IMAP] Credenciais Gmail não configuradas.")
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")
        _, msgs = mail.search(None, '(FROM "noreply@shopee.com.br" SUBJECT "Pagamento")')
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
                produto = "—"
                quantidade = "—"
                sku = "—"
                cliente = "—"
                prazo = "—"
                m = re.search(
                    r'ID do pedido:.*?\n\n(.+?)\n\nQuantidade\n\nSKU\n\n(\d+)\n\n([^\n]+)',
                    corpo, re.DOTALL
                )
                if m:
                    produto = m.group(1).strip()
                    quantidade = m.group(2).strip()
                    sku = m.group(3).strip()
                mc = re.search(r'Envie o pedido para ([^\.\n]+)', corpo)
                if mc:
                    cliente = mc.group(1).strip()
                mp = re.search(r'(Até \d+ de \w+)', corpo)
                if mp:
                    prazo = mp.group(1).strip()

                salvar_pedido(
                    numero_pedido=numero, produto=produto,
                    quantidade=quantidade, sku=sku,
                    cliente=cliente, prazo=prazo,
                    status="Pagamento confirmado",
                    obs=f"Detectado via Gmail em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}"
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


# ─── Estado das conversas
# Estrutura: { telefone: { "etapa": ..., "pedido": ..., "ultima_msg": ... } }
conversas = {}

# Etapas:
# "inicio"                → aguardando número do pedido
# "aguardando_imagens"    → pedido encontrado, esperando fotos
# "aguardando_pedido"     → fotos chegaram antes do número, esperando número
# "imagens_recebidas"     → tudo pronto, em produção


def montar_info_produto(dados):
    """Monta texto com qtd de fotos e descrição do produto a partir do SKU."""
    sku = dados.get("sku", "")
    produto = dados.get("produto", "")
    qtd_match = re.search(r'(\d+)\s*FOTO', sku.upper())
    if qtd_match and produto and produto != "—":
        qtd = qtd_match.group(1)
        return f"\nSão {qtd} fotos\n{produto}"
    elif produto and produto != "—":
        return f"\n{produto}"
    return ""


def responder_ana(telefone, mensagem, tem_midia=False):
    msg_lower = mensagem.strip().lower()
    msg_original = mensagem.strip()

    estado = conversas.get(telefone, {"etapa": "inicio"})
    etapa = estado.get("etapa", "inicio")
    ultima_msg = estado.get("ultima_msg", "")

    # ── Detecção de Google Drive ──────────────────────────────────────
    if "drive.google.com" in msg_lower:
        resposta = (
            "Obrigada pelo link! 😊\n"
            "Vou solicitar acesso no Drive no nome de *Ana Maria*.\n"
            "Assim que tiver acesso, confirmo as fotos."
        )
        estado["ultima_msg"] = resposta
        conversas[telefone] = estado
        return resposta

    # ── Perguntas sobre preço / pagamento ────────────────────────────
    palavras_preco = ["preço", "preco", "quanto", "custa", "valor", "pagar",
                      "pagamento", "pix", "transferência", "transferencia"]
    if any(p in msg_lower for p in palavras_preco):
        resposta = (
            "Nossos preços:\n\n"
            "📷 *Foto 10x15* — R$1,00/unidade\n"
            "📷 *Foto 15x21* — R$1,50/unidade\n"
            "🧲 *Foto Imã Geladeira* — R$2,50/unidade\n"
            "🎞️ *Foto Polaroide* — R$1,50/unidade\n\n"
            f"Para pagamentos:\n{PIX_INFO}"
        )
        estado["ultima_msg"] = resposta
        conversas[telefone] = estado
        return resposta

    # ── Cancelamento e compra direta ─────────────────────────────────
    if "cancel" in msg_lower and any(p in msg_lower for p in ["comprar", "maior", "mais", "pacote"]):
        resposta = (
            "Sim, pode cancelar direto no app da Shopee! 😊\n\n"
            "Mas se preferir, pode comprar a diferença diretamente com a gente — "
            "aproveitamos esse pedido e enviamos tudo junto.\n\n"
            "📷 Foto 10x15: R$1,00/unidade\n"
            "📷 Foto 15x21: R$1,50/unidade\n"
            "🧲 Foto Imã: R$2,50/unidade\n\n"
            "Quer continuar com a gente? Me diz quantas fotos quer no total! 😊"
        )
        estado["ultima_msg"] = resposta
        conversas[telefone] = estado
        return resposta

    # ── Extrai número de pedido da mensagem ──────────────────────────
    numero = extrair_numero_pedido(msg_original)

    # ══════════════════════════════════════════════════════════════════
    # ETAPA: INICIO
    # ══════════════════════════════════════════════════════════════════
    if etapa == "inicio":
        if numero:
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                produto_info = montar_info_produto(dados)
                estado = {"etapa": "aguardando_imagens", "pedido": numero}
                resposta = (
                    f"Olá! 😊 Encontrei seu pedido *{numero}*."
                    f"{produto_info}\n\n"
                    "Me envie as fotos para prosseguirmos. Pode mandar todas de uma vez! 📸"
                )
            else:
                resposta = (
                    f"Oi! O pedido *{numero}* ainda não apareceu no nosso sistema.\n"
                    "Aguarde alguns minutos após a confirmação do pagamento e tente novamente. "
                    "Se o problema persistir, me avise! 😊"
                )
        elif tem_midia:
            # Foto chegou antes do número do pedido
            estado = {"etapa": "aguardando_pedido", "fotos_recebidas": 1}
            resposta = (
                "Obrigada pela foto! 📸\n"
                "Para vincular ao seu pedido, me informe o *número do pedido* da Shopee.\n"
                "Você encontra no app em *Meus Pedidos*."
            )
        else:
            # Saudação ou mensagem inicial
            resposta = (
                "Olá! Sou a Ana da *Personalizei Fotos* 📸\n\n"
                "Obrigada pela sua compra! Para começar, me informe o "
                "*número do seu pedido* da Shopee.\n"
                "Você encontra no app em *Meus Pedidos*."
            )

    # ══════════════════════════════════════════════════════════════════
    # ETAPA: AGUARDANDO NÚMERO (fotos chegaram antes do pedido)
    # ══════════════════════════════════════════════════════════════════
    elif etapa == "aguardando_pedido":
        if numero:
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                atualizar_status(numero, "Imagens recebidas",
                                 obs=f"Fotos recebidas em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}")
                estado = {"etapa": "imagens_recebidas", "pedido": numero}
                resposta = (
                    f"Perfeito! Pedido *{numero}* vinculado às fotos. ✅\n"
                    "Nossa equipe já foi notificada e vai iniciar a produção em breve.\n"
                    "Prazo médio de entrega: *3 a 5 dias úteis*. Obrigada! 💜"
                )
            else:
                resposta = (
                    f"O pedido *{numero}* ainda não está no sistema.\n"
                    "Aguarde alguns minutos e tente novamente."
                )
        elif tem_midia:
            # Mais fotos chegando, só conta silenciosamente
            fotos = estado.get("fotos_recebidas", 0) + 1
            estado["fotos_recebidas"] = fotos
            # Repete pedido do número só se não foi a última mensagem
            if "número do pedido" not in ultima_msg:
                resposta = "Por favor, me informe o *número do pedido* para continuar."
            else:
                resposta = None  # Já pediu, não repete
        else:
            # Mensagem de texto mas sem número de pedido
            if "número do pedido" not in ultima_msg:
                resposta = "Por favor, me informe o *número do pedido* da Shopee para continuar."
            else:
                resposta = None

    # ══════════════════════════════════════════════════════════════════
    # ETAPA: AGUARDANDO IMAGENS
    # ══════════════════════════════════════════════════════════════════
    elif etapa == "aguardando_imagens":
        pedido = estado.get("pedido", "")

        if numero and numero != pedido:
            # Cliente informou número de outro pedido
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                produto_info = montar_info_produto(dados)
                estado = {"etapa": "aguardando_imagens", "pedido": numero}
                resposta = (
                    f"Ok! Mudando para o pedido *{numero}*."
                    f"{produto_info}\n\n"
                    "Me envie as fotos! 📷"
                )
            else:
                resposta = f"O pedido *{numero}* não foi encontrado. Verifique o número e tente novamente."
        elif tem_midia:
            # Primeira foto recebida — confirma e passa para próxima etapa
            fotos = estado.get("fotos_recebidas", 0) + 1
            estado["fotos_recebidas"] = fotos
            atualizar_status(pedido, "Imagens recebidas",
                             obs=f"Imagem recebida em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}")
            if fotos == 1:
                # Só confirma na primeira foto, não em cada uma
                estado["etapa"] = "imagens_recebidas"
                resposta = (
                    "Imagens recebidas com sucesso! ✅\n"
                    "Nossa equipe já foi notificada e vai iniciar a produção em breve.\n"
                    "Prazo médio de entrega: *3 a 5 dias úteis*. Obrigada! 💜"
                )
            else:
                resposta = None  # Fotos adicionais chegam silenciosamente
        else:
            # Texto enquanto espera fotos — apenas mantém estado
            resposta = None

    # ══════════════════════════════════════════════════════════════════
    # ETAPA: IMAGENS RECEBIDAS (em produção)
    # ══════════════════════════════════════════════════════════════════
    elif etapa == "imagens_recebidas":
        pedido = estado.get("pedido", "")

        if tem_midia:
            # Fotos adicionais — aceita silenciosamente
            fotos = estado.get("fotos_recebidas", 0) + 1
            estado["fotos_recebidas"] = fotos
            atualizar_status(pedido, "Imagens recebidas",
                             obs=f"Imagem adicional em {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}")
            resposta = None
        elif numero and numero != pedido:
            # Novo pedido
            if pedido_existe(numero):
                dados = info_pedido(numero)
                atualizar_telefone(numero, telefone)
                produto_info = montar_info_produto(dados)
                estado = {"etapa": "aguardando_imagens", "pedido": numero}
                resposta = (
                    f"Olá! Abrindo atendimento para o pedido *{numero}*."
                    f"{produto_info}\n\n"
                    "Me envie as fotos! 📸"
                )
            else:
                resposta = f"O pedido *{numero}* não foi encontrado."
        else:
            resposta = "Suas fotos estão em produção! 💜 Se precisar de algo, estou aqui."

    else:
        estado = {"etapa": "inicio"}
        resposta = (
            "Olá! Para um novo atendimento, me informe o *número do seu pedido* da Shopee. 😊"
        )

    if resposta:
        estado["ultima_msg"] = resposta
    conversas[telefone] = estado
    return resposta


# ─── Webhook WhatsApp ─────────────────────────────────────────────────────────
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


if __name__ == "__main__":
    t = threading.Thread(target=thread_gmail, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
