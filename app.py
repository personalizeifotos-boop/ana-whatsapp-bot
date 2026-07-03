









import os
import re
import json
import imaplib
import email
import threading
import time
import unicodedata
import urllib.request as _url_req
import gspread
import pytz
from datetime import datetime, timedelta
from flask import Flask, request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

BRASILIA = pytz.timezone("America/Sao_Paulo")
app = Flask(__name__)

# ââ Exibe email da conta de serviÃ§o no log de inicializaÃ§Ã£o ââââââââââââââââââ
try:
    _creds_raw = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if _creds_raw:
        _creds_data = json.loads(_creds_raw)
        print(f"[Setup] Conta de serviÃ§o Google: {_creds_data.get('client_email', 'nÃ£o encontrado')}")
except Exception as _e:
    print(f"[Setup] Erro ao ler credenciais: {_e}")

# ââ Controle da Ana âââââââââââââââââââââââââââââââââââââââââââ
# Defina como True para reativar o envio de mensagens da Ana
ANA_ATIVA = True
_pausa_mensagens = False  # True = mensagens pausadas, imagens continuam normalmente

# DEBUG: armazena últimos payloads para diagnóstico
_ultimos_payloads = []

# ââ ConfiguraÃ§Ãµes ââââââââââââââââââââââââââââââââââââââââââââ
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SPREADSHEET_ID = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"
ZAPI_INSTANCE = "3F353F900771725020A0F6B0730C054E"
ZAPI_TOKEN = "2E4ECDD70099CF7EDCEAF35E"
ZAPI_BASE_URL = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}"

# ââ Mensagens da Ana âââââââââââââââââââââââââââââââââââââââââ
MSG_SAUDACAO = (
    "OlÃ¡, seja bem-vindo Ã  Personalizei! Obrigado pela sua compra. ð\n\n"
    "Antes de enviar qualquer imagem, Ã© de extrema importÃ¢ncia que vocÃª nos envie primeiro "
    "o nÃºmero do pedido. Esse nÃºmero estÃ¡ logo apÃ³s as letras ID: no seu comprovante de compra.\n\n"
    "Por favor, digite ou copie e cole o nÃºmero — nÃ£o envie print, pois nosso sistema "
    "nÃ£o consegue identificar imagens de texto."
)
MSG_SAUDACAO_RETORNO = (
    "OlÃ¡{nome_part}, que Ã³timo ter vocÃª de volta! ð\n\n"
    "Para darmos continuidade, por favor nos envie o nÃºmero do novo pedido "
    "(vem logo apÃ³s 'ID:' no seu comprovante de compra)."
)
MSG_PEDIR_PEDIDO = (
    "Por favor envie o nÃºmero do pedido, esse nÃºmero vem logo depois das letras ID:, "
    "vocÃª pode encontrar esse nÃºmero no seu histÃ³rico de pedidos, "
    "sem esse nÃºmero nÃ£o conseguimos te identificar."
)
MSG_FINALIZAR = (
    "Perfeito, seu pedido jÃ¡ estÃ¡ sendo preparado e serÃ¡ enviado no menor tempo possÃ­vel. "
    "Segue abaixo o link da nossa loja, caso queira conhecer todos os nossos produtos: "
    "https://shopee.com.br/personalizei_fotografias?located_flash_sale=238855072727041"
    "&share_from=isfs&shop=1331254404&stm_medium=referral&stm_source=rw&tab=5"
    "&uls_trackid=55dmrh0c006m"
)
MSG_PIX = (
    "Segue a chave PIX\n"
    "Titular: Rodrigo Vieira Monteiro\n"
    "Chave PIX: 58733941000114\n"
    "ApÃ³s efetuar o pagamento pela chave PIX nos envie o comprovante por favor."
)

# ââ Pasta raiz no Google Drive âââââââââââââââââââââââââââââââ
PEDIDOS_SHOPEE_FOLDER_ID = "1ikovzBRkVLdR8kqTpnSlpy9WyC-dN1IO"

# ââ Tabela de preÃ§os por foto extra âââââââââââââââââââââââââ
PRECOS_EXTRA = {
    "10X15": 1.00,
    "15X21": 1.50,
    "Mini Fotos": 1.00,
    "A4": 3.00,
    "Fotos Retro com ima": 2.50,
    "Mini Fotos com ima": 2.00,
    "Fotos Retro": 1.00,
    "Mini Fotos Retro": 1.00,
    "Mini Fotos Retro com ima": 2.00,
    "Tirinha": 1.00,
    "Cartao de Visita": 1.00,
    "Etiqueta": 1.00,
}

# Nome de exibiÃ§Ã£o (com acentos) para cada tipo — usado no Drive e nas mensagens
NOME_PASTA_TIPO = {
    "10X15":                    "10X15",
    "15X21":                    "15X21",
    "A4":                       "A4",
    "Cartao de Visita":         "Cartão de visita",
    "Adesivos":                 "Adesivos",
    "Fotos Retro":              "Fotos retrô",
    "Fotos Retro com ima":      "Fotos retrô com imã",
    "Mini Fotos":               "Mini fotos",
    "Mini Fotos com ima":       "Mini fotos com imã",
    "Mini Fotos Retro":         "Mini fotos retrô",
    "Mini Fotos Retro com ima": "Mini fotos retrô com imã",
    "Tags":                     "Tags",
    "Tirinha":                  "Tirinha",
}

# Mapeamento: palavra-chave (maiÃºsculo, sem acento) â chave interna do tipo
# Ordem importa: mais especÃ­fico primeiro
MAPEAMENTO_TIPO = [
    ("MINI RETRO COM IMA",  "Mini Fotos Retro com ima"),
    ("MINI RETRO COM IMA",  "Mini Fotos Retro com ima"),
    ("MINI RETRO IMA",      "Mini Fotos Retro com ima"),
    ("MINI IMA RETRO",      "Mini Fotos Retro com ima"),
    ("MINI RETRO",          "Mini Fotos Retro"),
    ("MINI FOTOS COM IMA",  "Mini Fotos com ima"),
    ("MINI FOTO COM IMA",   "Mini Fotos com ima"),
    ("MINI IMA",            "Mini Fotos com ima"),
    ("MINI FOTO",           "Mini Fotos"),
    ("MINI FOTOS",          "Mini Fotos"),
    ("CARTAO DE VISITA",    "Cartao de Visita"),
    ("TIRINHA",             "Tirinha"),
    ("POLAROIDE",           "Fotos Retro"),
    ("RETRO COM IMA",       "Fotos Retro com ima"),
    ("RETRO IMA",           "Fotos Retro com ima"),
    ("IMA RETRO",           "Fotos Retro com ima"),
    ("RETRO",               "Fotos Retro"),
    ("ETIQUETA",            "Adesivos"),
    ("ADESIVO",             "Adesivos"),
    ("ADESIVOS",            "Adesivos"),
    ("15X21",               "15X21"),
    ("15 X 21",             "15X21"),
    ("10X15",               "10X15"),
    ("10 X 15",             "10X15"),
    ("IMA",                 "Fotos Retro com ima"),
    ("A4",                  "A4"),
    ("TAG",                 "Tags"),
    ("TAGS",                "Tags"),
]

# ── Pastas exatas de destino (nomes iguais às pastas em PEDIDOS_SHOPEE) ──────
PASTAS = [
    "Mini fotos retrô com imã",
    "Mini fotos retrô",
    "Mini fotos com imã",
    "Mini fotos",
    "Fotos retrô com imã",
    "Fotos retrô",
    "Cartão de visita",
    "Adesivos",
    "Tirinha",
    "Tags",
    "A4",
    "15X21",
    "10X15",
]

def _norm_txt(t):
    return unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode().upper()

def identificar_pasta(produto):
    """Retorna o nome exato da pasta mais adequada para o produto."""
    t = _norm_txt(produto)
    eh_mini   = "MINI" in t
    tem_retro = any(k in t for k in ["RETRO", "POLAROID", "POLAROIDE"])
    tem_ima   = ("IMA" in t and "IMAGEM" not in t) or "GELADEIRA" in t
    if eh_mini:
        if tem_retro and tem_ima: return "Mini fotos retrô com imã"
        if tem_retro:             return "Mini fotos retrô"
        if tem_ima:               return "Mini fotos com imã"
        return "Mini fotos"
    if tem_retro:
        return "Fotos retrô com imã" if tem_ima else "Fotos retrô"
    if "15X21" in t or "15 X 21" in t: return "15X21"
    if "10X15" in t or "10 X 15" in t: return "10X15"
    if "21X30" in t or "A4" in t:      return "A4"
    if "CARTAO" in t and "VISITA" in t: return "Cartão de visita"
    if "ADESIVO" in t or "ETIQUETA" in t: return "Adesivos"
    if "TIRINHA" in t:                 return "Tirinha"
    if "TAG" in t:                     return "Tags"
    return "10X15"

PEDIDO_REGEX = re.compile(r'\b([A-Z0-9]{10,20})\b')

# ââ FAQ baseado em conversas reais com clientes ââââââââââââââ
# Cada entrada: (lista de palavras-chave, resposta)
FAQ_RESPOSTAS = [
    (
        ["como envio", "como mando", "como faÃ§o para enviar", "como enviar", "como mandar",
         "enviar fotos", "mandar fotos", "onde envio", "onde mando"],
        "Ã simples! Ã sÃ³ enviar as fotos diretamente aqui pelo WhatsApp mesmo. ð\n"
        "Mas antes de enviar as fotos, nÃ£o esqueÃ§a de nos passar o nÃºmero do pedido — "
        "ele estÃ¡ logo apÃ³s 'ID:' no seu comprovante de compra da Shopee."
    ),
    (
        ["cancelar", "cancelamento", "desistir", "devolver", "estornar"],
        "VocÃª pode cancelar diretamente pela Shopee, sem problemas! ð "
        "Mas se quiser, pode comprar mais fotos diretamente conosco e aproveitamos o pedido atual para enviar junto — "
        "assim fica mais prÃ¡tico. Ã sÃ³ me dizer quantas fotos vocÃª quer no total!"
    ),
    (
        ["prazo", "quando chega", "quanto tempo", "quanto tempo leva", "quando fica pronto",
         "previsao", "previsão", "dias", "demora muito", "quantos dias",
         "previsao de entrega", "previsão de entrega", "demora para entregar", "demora pra chegar"],
        "Assim que enviarmos o seu pedido, você receberá o código de rastreio por onde poderá "
        "acompanhar o envio. Aconselhamos acompanhar pelo site dos Correios, onde a atualização "
        "é mais rápida do que na Shopee.\n\n"
        "Tenha o seu número de rastreio em mãos e acesse o site dos Correios:\n"
        "https://rastreamento.correios.com.br/app/index.php"
    ),
    (
        ["rastreio", "rastreamento", "codigo", "cÃ³digo", "postado", "enviou", "enviado"],
        "O cÃ³digo de rastreio Ã© enviado pela Shopee assim que seu pedido Ã© postado. "
        "Verifique na aba 'Meus Pedidos' do aplicativo da Shopee. ð"
    ),
    (
        ["frete", "entrega", "correios", "transportadora"],
        "O frete Ã© calculado pela Shopee de acordo com o seu CEP e aparece no momento da compra."
    ),
    (
        ["qualidade", "resolucao", "resoluÃ§Ã£o", "borrada", "pixelada", "nitida", "nÃ­tida",
         "perde qualidade", "perde a qualidade", "perco qualidade", "nao perde", "não perde",
         "as fotos perdem", "por aqui perde"],
        "Não, pode enviar sem problemas! As fotos não perdem qualidade aqui. 😊\n"
        "Para melhores resultados, recomendamos enviar fotos com boa resolução — "
        "evite fotos com zoom excessivo ou tiradas de tela."
    ),
    (
        ["shopee", "loja", "produtos", "catalogo", "catÃ¡logo", "outros produtos"],
        "VocÃª pode conferir todos os nossos produtos na nossa loja da Shopee: "
        "https://shopee.com.br/personalizei_fotografias ð"
    ),
    (
        ["quanto custa", "preÃ§o", "preco", "valor", "tabela", "quanto Ã©", "quanto e",
         "custa", "imÃ£", "ima", "iman", "custo", "cobrado", "cobra", "pago", "paga"],
        "Nossos preÃ§os por foto sÃ£o:\n"
        "• 10x15 cm — R$ 1,00\n"
        "• Mini foto — R$ 1,00\n"
        "• Polaroide — R$ 1,00\n"
        "• 15x21 cm — R$ 1,50\n"
        "• ImÃ£   — R$ 2,50\n"
        "• Mini ImÃ£   — R$ 2,00\n"
        "• A4 (21X30)  — R$ 3,00\n\n"
        "Esses valores sÃ£o cobrados apenas para fotos enviadas alÃ©m da quantidade do seu pedido. ð"
    ),
    (
        ["nao chegou", "não chegou", "fotos nao chegaram", "fotos não chegaram",
         "pedido nao chegou", "pedido não chegou", "ainda nao chegou", "ainda não chegou",
         "minha encomenda nao", "minha encomenda não", "minhas fotos nao chegaram",
         "ainda nao chegaram", "ainda não chegaram", "minhas fotos ainda", "fotos ainda nao chegou", "fotos ainda não chegou"],
        "Você precisa abrir uma reclamação no chat da Shopee pedindo atualização do seu pedido, "
        "pois nós só fazemos a venda e a postagem — toda a logística da entrega é feita pela Shopee junto aos Correios. 😔\n"
        "Nós como vendedores não temos controle nenhum sobre esse processo, sinto muito não poder ajudar!"
    ),
    (
        ["quando sera enviado", "quando será enviado", "quando voces enviam", "quando vocês enviam",
         "quando vao enviar", "quando vão enviar", "quando vai ser enviado", "quando enviam meu pedido",
         "quando voces vao enviar", "quando vocês vão enviar"],
        "Levamos 24h após você nos enviar as fotos para preparar o seu pedido para envio. 📦"
    ),
    (
        ["porta retrato", "porta-retrato", "portaretrato",
         "album", "álbum", "albuns", "álbuns",
         "album de foto", "álbum de foto", "album de fotos", "álbum de fotos",
         "fazem album", "tem album", "fazem álbum", "tem álbum",
         "fazem albuns", "tem albuns", "fazem álbuns", "tem álbuns",
         "voces fazem album", "vocês fazem álbum", "vocês fazem álbuns",
         "fazem porta retrato", "tem porta retrato"],
        "Infelizmente não trabalhamos com esse produto, mas você pode ver todos os nossos produtos no link abaixo:\nhttps://tinyurl.com/mwpwmsr7"
    ),
    (
        ["posso enviar por link", "enviar por link", "mandar por link", "link das fotos",
         "link de fotos", "pelo link", "por link", "enviar pelo link", "fotos por link"],
        "Sim pode, sem problemas, mas eu só consigo ler links do *Google Drive*. 😊\n\nSe as suas fotos estiverem em outro serviço, vou precisar chamar um atendente para te ajudar."
    ),
    (
        ["onde vejo o numero", "onde vejo o número", "onde fica o numero", "onde fica o número",
         "onde esta o numero", "onde está o número", "onde encontro o numero", "onde encontro o número",
         "onde fica o id", "onde vejo o id", "onde esta o id", "onde está o id"],
        "O número do pedido está logo após as letras *ID:* no seu comprovante de compra da Shopee. 📋\n"
        "É um número longo — geralmente começa com 25 ou 26, seguido de vários dígitos."
    ),
    (
        ["me pediram para enviar", "me pediram pra enviar", "me mandaram enviar",
         "fui redirecionado", "me indicaram", "me passaram esse numero", "me pediram para mandar"],
        "Olá, seja bem-vindo à Personalizei! Obrigado pela sua compra. 😊\n\n"
        "Antes de enviar qualquer imagem, é de extrema importância que você nos envie primeiro o número do pedido. "
        "Esse número está logo após as letras *ID:* no seu comprovante de compra.\n\n"
        "Por favor, digite ou copie e cole o número — não envie print, pois nosso sistema não consegue identificar imagens de texto."
    ),
    (
        ["vou enviar as fotos", "vou mandar as fotos", "vou enviar agora",
         "vou mandar agora", "vou te enviar as fotos", "vou te mandar as fotos"],
        "OK, pode enviar! 😊"
    ),
    (
        ["quero comprar mais fotos", "quero comprar fotos a mais", "comprar fotos extras",
         "quero mais fotos", "quero fotos a mais", "comprar fotos a mais", "comprar mais fotos",
         "quero comprar algumas fotos", "comprar algumas fotos", "algumas fotos a mais", "quero algumas fotos"],
        "Sem problemas! Quantas fotos você quer comprar a mais e qual a dimensão? 😊\n"
        "(Ex: 10 fotos 10x15, 5 mini fotos, 3 imãs, etc.)"
    ),
    (
        ["me manda o pix", "manda o pix", "manda seu pix", "me manda seu pix",
         "me manda o seu pix", "manda o seu pix", "me manda o numero do pix", "manda o numero pix",
         "qual o pix", "qual seu pix", "qual o seu pix", "numero do pix", "número do pix",
         "chave pix", "chave do pix", "qual a chave", "qual e o pix", "qual é o pix",
         "qual o numero do pix", "qual é o numero do pix", "qual numero do pix",
         "me passa o pix", "passa o pix", "me passa o numero", "qual o numero pix"],
        "Segue a chave PIX 👇\n\nTitular: Rodrigo Vieira Monteiro\nChave PIX: 58733941000114"
    ),
    (
        ["vou te enviar o pix", "vou enviar o pix", "vou mandar o pix", "vou te mandar o pix",
         "vou fazer o pix", "vou pagar agora", "vou pagar pelo pix", "vou fazer a transferencia",
         "vou fazer a transferência", "vou te mandar o comprovante", "vou enviar o comprovante"],
        "OK 👍"
    ),

]


def calcular_preco(texto):
    """
    Detecta frases como 'quanto daria 37 fotos ima' e retorna
    a resposta com o total calculado. Retorna None se nao detectar.
    """
    def _normaliza(s):
        """Remove acentos para comparacao robusta (ima, ima, etc.)."""
        return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode().upper()

    t = texto.lower().strip()

    # ── Detecta "quero comprar X fotos Y" / "compra X fotos Y" ──
    m_compra = re.search(
        r'(?:quero|quer|compra|comprar|preciso de|queria)\s+(\d+)\s*(?:fotos?\s+)?(\w[\w\s]*)',
        t
    )
    if m_compra:
        qtd_c = int(m_compra.group(1))
        tipo_c = m_compra.group(2).strip()
        tipo_c_norm = unicodedata.normalize('NFD', tipo_c).encode('ascii', 'ignore').decode().upper()
        preco_c = None
        nome_c = None
        if '10X15' in tipo_c_norm or '10 X 15' in tipo_c_norm:
            preco_c, nome_c = 1.00, '10x15 cm'
        elif '15X21' in tipo_c_norm or '15 X 21' in tipo_c_norm:
            preco_c, nome_c = 1.50, '15x21 cm'
        elif any(k in tipo_c_norm for k in ['IMA', 'IMAN']) and 'IMAGEM' not in tipo_c_norm:
            preco_c, nome_c = 2.50, 'Imã'
        elif 'POLAROIDE' in tipo_c_norm or 'POLAROID' in tipo_c_norm:
            preco_c, nome_c = 1.00, 'Polaroide'
        elif 'A4' in tipo_c_norm:
            preco_c, nome_c = 3.00, 'A4'
        elif 'MINI' in tipo_c_norm:
            preco_c, nome_c = 1.00, 'Mini foto'
        if preco_c is not None:
            total_c = qtd_c * preco_c
            total_str_c = f"R$ {total_c:.2f}".replace('.', ',')
            return f"As {qtd_c} fotos {nome_c} custam {total_str_c}. 😊"

    # ── Detecta resposta direta "X fotos TIPO" (ex: "10 fotos 10x15") ──
    m_direto = re.search(r'(\d+)\s+fotos?\s+(.+)', t)
    if m_direto:
        qtd_d = int(m_direto.group(1))
        tipo_d_norm = unicodedata.normalize('NFD', m_direto.group(2).strip()).encode('ascii', 'ignore').decode().upper()
        preco_d = None; nome_d = None
        if '10X15' in tipo_d_norm or '10 X 15' in tipo_d_norm: preco_d, nome_d = 1.00, '10x15 cm'
        elif '15X21' in tipo_d_norm or '15 X 21' in tipo_d_norm: preco_d, nome_d = 1.50, '15x21 cm'
        elif any(k in tipo_d_norm for k in ['IMA', 'IMAN']) and 'IMAGEM' not in tipo_d_norm: preco_d, nome_d = 2.50, 'Imã'
        elif 'POLAROIDE' in tipo_d_norm or 'POLAROID' in tipo_d_norm: preco_d, nome_d = 1.00, 'Polaroide'
        elif 'A4' in tipo_d_norm: preco_d, nome_d = 3.00, 'A4'
        elif 'MINI' in tipo_d_norm: preco_d, nome_d = 1.00, 'Mini foto'
        if preco_d is not None:
            total_d = qtd_d * preco_d
            total_str_d = f"R$ {total_d:.2f}".replace('.', ',')
            return f"As {qtd_d} fotos {nome_d} custam {total_str_d}. 😊"

    m = re.search(
        r'(?:quanto (?:daria|fica|sai|custa|seria|custaria)|valor de|preco de|pre.o de)'
        r'\s+(\d+)\s+(?:fotos?|imas?)?\s*(.*)',
        t
    )
    if not m:
        m2 = re.search(r'(\d+)\s+(?:fotos?\s+)?(\w[\w\s]*)(?:quanto|valor|preco|pre.o)', t)
        if m2:
            quantidade = int(m2.group(1))
            tipo_raw = m2.group(2).strip()
        else:
            return None
    else:
        quantidade = int(m.group(1))
        tipo_raw = (m.group(2) or "").strip()

    # Normalizar para ASCII ANTES de remover stop words (detecta mini foto/mini ima corretamente)
    tipo_norm = _normaliza(tipo_raw)

    preco_unitario = None
    nome_tipo = None

    if any(k in tipo_norm for k in ["MINI IMA", "MINIIMA"]):
        preco_unitario = 2.00
        nome_tipo = "Mini imã"
    elif "MINI FOTO" in tipo_norm or "MINIFOTO" in tipo_norm or tipo_norm.strip() == "MINI":
        preco_unitario = 1.00
        nome_tipo = "Mini foto"
    else:
        # Remove stop words e renormaliza
        for stop in ["de", "foto", "fotos", "imagem", "imagens"]:
            tipo_raw = re.sub(r'\b' + stop + r'\b', '', tipo_raw).strip()
        tipo_norm = _normaliza(tipo_raw)

        if any(k in tipo_norm for k in ["IMA", "IMAN"]) and "IMAGEM" not in tipo_norm:
            preco_unitario = 2.50
            nome_tipo = "Imã"
        elif "POLAROIDE" in tipo_norm or "POLAROID" in tipo_norm:
            preco_unitario = 1.00
            nome_tipo = "Polaroide"
        elif "15X21" in tipo_norm or "15 X 21" in tipo_norm or "15X 21" in tipo_norm:
            preco_unitario = 1.50
            nome_tipo = "15x21 cm"
        elif "10X15" in tipo_norm or "10 X 15" in tipo_norm or "10X 15" in tipo_norm:
            preco_unitario = 1.00
            nome_tipo = "10x15 cm"
        elif "A4" in tipo_norm or "21X30" in tipo_norm or "21 X 30" in tipo_norm:
            preco_unitario = 3.00
            nome_tipo = "A4"
        else:
            return None

    if preco_unitario is None:
        return None

    total = quantidade * preco_unitario
    total_str = f"R$ {total:.2f}".replace(".", ",")
    unitario_str = f"R$ {preco_unitario:.2f}".replace(".", ",")

    return (
        f"{quantidade} fotos {nome_tipo} ficam {total_str}. 😊\n"
        f"(cada {nome_tipo} custa {unitario_str} — cobrado apenas para fotos além da quantidade do pedido)"
    )

def verificar_faq(texto_lower):
    """Verifica se o texto corresponde a alguma pergunta do FAQ. Retorna resposta ou None."""
    for palavras, resposta in FAQ_RESPOSTAS:
        if any(p in texto_lower for p in palavras):
            return resposta
    return None

def tentar_extrair_nome(texto):
    """
    Tenta extrair nome prÃ³prio de uma mensagem de texto.
    PadrÃµes: 'Meu nome Ã© X', 'Me chamo X', ou mensagem que parece sÃ³ um nome (2-5 palavras).
    """
    t = texto.strip()
    # "Meu nome Ã© X" / "Me chamo X" / "Sou a/o X"
    m = re.match(
        r'(?:meu nome [eÃ©]|me chamo|sou (?:a |o )?)\s*(.{4,50})',
        t, re.IGNORECASE
    )
    if m:
        nome = m.group(1).strip().rstrip('.,!?')
        if re.match(r'^[A-Za-zÃ-Ãº\s]+$', nome):
            return nome.title()
    # Mensagem que parece ser sÃ³ um nome (2-5 palavras, apenas letras)
    if re.match(r'^[A-Za-zÃ-Ãº\s]{5,60}$', t):
        partes = t.split()
        if 2 <= len(partes) <= 5 and all(len(p) >= 2 for p in partes):
            return t.title()
    return None

# ââ Estado em memÃ³ria por telefone âââââââââââââââââââââââââââ
estado_clientes = {}   # phone â dict de estado
timers_ativos = {}     # phone â threading.Timer
telefone_pedido = {}   # legado: phone â pedido

def get_estado(phone):
    if phone not in estado_clientes:
        estado_clientes[phone] = {
            "status": "novo",
            "pedido": "",
            "produto": "",
            "sku": "",
            "limite_fotos": 0,
            "fotos_recebidas": 0,
            "imgs_antes_pedido": 0,
            "fotos_extras": 0,
            "valor_extra": 0.0,
            "nome_cliente": "",        # nome extraÃ­do das mensagens
            # Multi-produto
            "multi_produto": False,
            "produtos": [],
            "produto_ativo_idx": -1,
            "expecting_pix": False,    # próxima imagem é comprovante PIX
        }
    return estado_clientes[phone]

def identificar_tipo(produto, sku):
    texto = (produto + " " + sku).upper()
    # Remove acentos de forma robusta (unicodedata resolve problema de encoding)
    texto = ''.join(
        c for c in unicodedata.normalize('NFKD', texto)
        if unicodedata.category(c) != 'Mn'
    )
    for chave, tipo in MAPEAMENTO_TIPO:
        if chave in texto:
            return tipo
    return "10X15"

def extrair_limite_fotos(sku):
    m = re.search(r'(\d+)\s*fotos?', sku, re.IGNORECASE)
    if not m:
        m = re.search(r'^(\d+)', sku.strip())  # fallback: '6 Mini fotos' → 6
    return int(m.group(1)) if m else 0

def parse_sku_produtos(sku):
    if '+' not in sku:
        return []
    partes = [p.strip() for p in sku.split('+')]
    resultado = []
    for parte in partes:
        m = re.search(r'(\d{1,3})\s*fotos?', parte, re.IGNORECASE)
        if m:
            limite = int(m.group(1))
            tipo = identificar_tipo('', parte)
            resultado.append({
                "tipo": tipo,
                "limite": limite,
                "recebidas": 0,
                "concluido": False,
                "sku_parte": parte,
            })
    return resultado

def extrair_sku_multiproduto(produto_str, corpo):
    texto = (produto_str + " " + corpo).upper()
    matches = list(re.finditer(r'(\d{1,3})\s+FOTOS?', texto))
    if len(matches) < 2:
        return ""
    partes = []
    for m in matches:
        qtd = int(m.group(1))
        start = max(0, m.end())
        contexto = texto[start: start + 60]
        tipo = identificar_tipo('', contexto)
        parte = f"{qtd} fotos {tipo}"
        if parte not in partes:
            partes.append(parte)
    return " + ".join(partes) if len(partes) > 1 else ""

def msg_orientacao_multiproduto(produtos):
    linhas = "\n".join(f"• {p['limite']} fotos {p['tipo']}" for p in produtos)
    return (
        f"Identificamos que seu pedido possui {len(produtos)} produtos:\n"
        f"{linhas}\n\n"
        "Para organizarmos tudo certinho, envie as fotos de cada produto "
        "separadamente, indicando a dimensÃ£o antes ou depois de cada lote. "
        "Exemplo: escreva '10X15' e envie as fotos, depois escreva '15X21' "
        "e envie as demais. ð"
    )

def _detectar_tipo_na_mensagem(texto):
    t = texto.upper()
    for orig, sub in [("Ã","A"),("Ã","A"),("Ã","A"),("Ã","A"),("Ã","E"),
                      ("Ã","E"),("Ã","I"),("Ã","O"),("Ã","O"),("Ã","O"),
                      ("Ã","U"),("Ã","C")]:
        t = t.replace(orig, sub)
    for chave, tipo in MAPEAMENTO_TIPO:
        if chave in t:
            return tipo
    return None

# ââ Z-API: envio de mensagens âââââââââââââââââââââââââââââââââ
def _fix_encoding(texto):
    """Corrige strings UTF-8 armazenadas como Latin-1 (double-encoding)."""
    resultado = []
    segmento = []
    for char in texto:
        if ord(char) <= 255:
            segmento.append(char)
        else:
            if segmento:
                seg = ''.join(segmento)
                try:
                    seg = seg.encode('latin-1').decode('utf-8')
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                resultado.append(seg)
                segmento = []
            resultado.append(char)
    if segmento:
        seg = ''.join(segmento)
        try:
            seg = seg.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        resultado.append(seg)
    return ''.join(resultado)

def enviar_mensagem(phone, mensagem):
    mensagem = _fix_encoding(mensagem)
    if not ANA_ATIVA:
        print(f"[Ana DESATIVADA] Mensagem bloqueada para {phone}: {mensagem[:80]}")
        return False
    if _pausa_mensagens:
        print(f"[Ana PAUSADA] Mensagem bloqueada para {phone}: {mensagem[:80]}")
        return False
    phone_num = re.sub(r'\D', '', phone)
    url = f"{ZAPI_BASE_URL}/send-text"
    payload = json.dumps({"phone": phone_num, "message": mensagem}, ensure_ascii=False).encode("utf-8")
    req = _url_req.Request(url, data=payload,
                           headers={
                               "Content-Type": "application/json",
                               "Client-Token": "Fd7f15657ef534ae09757eefa5368120cS"
                           })
    try:
        with _url_req.urlopen(req, timeout=15):
            print(f"[Z-API] â â {phone_num}: {mensagem[:80]}...")
            return True
    except Exception as e:
        print(f"[Z-API] â Erro para {phone_num}: {e}")
        return False

# ââ Google Drive ââââââââââââââââââââââââââââââââââââââââââââââ
_drive_folder_cache = {}  # (nome, parent_id) â folder_id

def _drive_service():
    """Retorna servico Drive via OAuth2 do usuario (conta personalizei.fotos@gmail.com)."""
    from google.oauth2.credentials import Credentials as _OAuthCreds
    from google.auth.transport.requests import Request as _GRequest
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        print("[Drive] Credenciais OAuth2 nao configuradas.")
        return None
    try:
        creds = _OAuthCreds(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        creds.refresh(_GRequest())
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"[Drive] Erro OAuth2: {e}")
        return None
def get_or_create_drive_folder(service, nome, parent_id):
    """Retorna o ID de uma pasta, criando se nÃ£o existir. Usa cache."""
    cache_key = (nome, parent_id)
    if cache_key in _drive_folder_cache:
        return _drive_folder_cache[cache_key]
    try:
        q = (f"name='{nome}' and mimeType='application/vnd.google-apps.folder' "
             f"and '{parent_id}' in parents and trashed=false")
        res = service.files().list(q=q, fields="files(id,name)").execute()
        files = res.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            meta = {
                "name": nome,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            }
            folder = service.files().create(body=meta, fields="id").execute()
            folder_id = folder["id"]
            print(f"[Drive] Pasta criada: {nome} ({folder_id})")
        _drive_folder_cache[cache_key] = folder_id
        return folder_id
    except Exception as e:
        print(f"[Drive] Erro ao criar/buscar pasta '{nome}': {e}")
        return parent_id  # fallback: salva na pasta pai

def _upload_imagem_drive(image_url, phone, pedido="", tipo="", subpasta=""):
    try:
        req = _url_req.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with _url_req.urlopen(req, timeout=15) as resp:
            image_bytes = resp.read()
        if len(image_bytes) < 500:
            print(f"[Drive] Imagem muito pequena ({len(image_bytes)}B) — URL expirada?")
            return image_url

        service = _drive_service()
        if not service:
            return image_url

        # ââ Hierarquia: PEDIDOS_SHOPEE / tipo / pedido ââââââââââââââââ
        nome_pasta_tipo = NOME_PASTA_TIPO.get(tipo, tipo or "Sem Categoria")
        folder_tipo = get_or_create_drive_folder(
            service, nome_pasta_tipo, PEDIDOS_SHOPEE_FOLDER_ID
        )
        if pedido:
            folder_pedido = get_or_create_drive_folder(service, pedido, folder_tipo)
        else:
            folder_pedido = folder_tipo
        if subpasta:
            folder_pedido = get_or_create_drive_folder(service, subpasta, folder_pedido)

        timestamp = datetime.now(BRASILIA).strftime("%Y%m%d_%H%M%S")
        phone_clean = re.sub(r'\D', '', phone)
        filename = f"foto_{phone_clean}_{timestamp}.jpg"
        media = MediaInMemoryUpload(image_bytes, mimetype="image/jpeg")
        file_obj = service.files().create(
            body={"name": filename, "parents": [folder_pedido]},
            media_body=media, fields="id"
        ).execute()
        file_id = file_obj.get("id")
        service.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"}
        ).execute()
        drive_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        print(f"[Drive] â {filename} â {nome_pasta_tipo}/{pedido or '-'} ({len(image_bytes)//1024}KB)")
        drive_url = f"https://drive.google.com/file/d/{file_id}/view"
        return drive_url
    except Exception as e:
        print(f"[Drive] Erro: {e}")
        return image_url

def extrair_id_drive(texto):
    padroes = [
        r'drive\.google\.com/drive(?:/u/\d+)?/folders/([a-zA-Z0-9_-]+)',
        r'drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
        r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
        r'drive\.google\.com/folderview\?id=([a-zA-Z0-9_-]+)',
    ]
    for padrao in padroes:
        m = re.search(padrao, texto)
        if m:
            return m.group(1)
    return None

def listar_imagens_pasta_drive(folder_id):
    try:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            return []
        creds = Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = build("drive", "v3", credentials=creds)
        query = (
            f"'{folder_id}' in parents "
            "and trashed = false "
            "and (mimeType contains 'image/')"
        )
        results = service.files().list(
            q=query,
            fields="files(id, name, mimeType)",
            pageSize=200
        ).execute()
        arquivos = results.get("files", [])
        print(f"[Drive] Pasta {folder_id}: {len(arquivos)} imagens encontradas")
        return arquivos
    except Exception as e:
        print(f"[Drive] Erro ao listar pasta {folder_id}: {e}")
        return []

NTFY_TOPIC = "personalizei-atendente-rodrigo"

def _notificar_atendente_desktop(phone, body, estado):
    """Notifica Rodrigo via ntfy.sh quando Ana nao sabe responder (cooldown 10min por cliente)."""
    agora = time.time()
    if agora - _alertas_atendente.get(phone, 0) < 600:
        return
    _alertas_atendente[phone] = agora

    pedido = estado.get("pedido", "") if estado else ""
    nome   = estado.get("nome_cliente", "") if estado else ""

    linhas = ["*Cliente precisa de atendimento!*", f"Telefone: {phone}"]
    if nome:
        linhas.append(f"Nome: {nome}")
    if pedido:
        linhas.append(f"Pedido: {pedido}")
    linhas.append(f"Mensagem: {body[:150]}")
    mensagem = "\n".join(linhas)

    try:
        req = _url_req.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=mensagem.encode("utf-8"),
            headers={
                "Title": "Atendimento necessario - Ana Bot",
                "Priority": "urgent",
                "Tags": "rotating_light"
            },
            method="POST"
        )
        _url_req.urlopen(req, timeout=5)
        print(f"[Ana] Atendente notificado para {phone}")
    except Exception as e:
        print(f"[Ana] Erro ao notificar atendente: {e}")

def processar_pasta_drive(phone, folder_id):
    try:
        arquivos = listar_imagens_pasta_drive(folder_id)
        if not arquivos:
            enviar_mensagem(
                phone,
                "â NÃ£o consegui acessar as fotos do link enviado. "
                "Verifique se o link estÃ¡ compartilhado como 'Qualquer pessoa com o link' e tente novamente, "
                "ou envie as fotos diretamente pelo WhatsApp."
            )
            return
        qtd = len(arquivos)
        enviar_mensagem(phone, f"ð Encontrei {qtd} foto(s) no link. Baixando e processando, aguarde...")
        print(f"[Drive] Processando {qtd} imagens do Drive para {phone}")
        for arq in arquivos:
            file_id = arq["id"]
            image_url = f"https://drive.google.com/uc?id={file_id}&export=download"
            processar_imagem_recebida(phone, image_url)
            time.sleep(0.5)
        enviar_mensagem(phone, f"✅ Pronto! Recebi {qtd} foto(s) do seu link com sucesso! 😊")
        print(f"[Drive] {qtd} imagens do Drive processadas para {phone}")
    except Exception as e:
        print(f"[Drive] Erro ao processar pasta para {phone}: {e}")
        enviar_mensagem(
            phone,
            "â Ocorreu um erro ao baixar as fotos do link. "
            "Por favor, envie as fotos diretamente pelo WhatsApp."
        )

# ââ Google Sheets âââââââââââââââââââââââââââââââââââââââââââââ
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
                    "pedido": linha[0].strip(),
                    "produto": linha[2].strip() if len(linha) > 2 else "",
                    "sku": linha[4].strip() if len(linha) > 4 else "",
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

def salvar_imagem_pendente(phone, image_url, pedido="", tipo="", status="pendente"):
    try:
        ws = get_sheet("Imagens", ["Telefone", "URL", "Data", "Status", "Pedido", "Tipo"])
        if ws is None:
            return
        data = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        # Fix 1: Dedup — ignora URL jÃ¡ registrada (Z-API duplica eventos)
        suf = re.sub(r'\D', '', phone)
        suf = suf[-11:] if len(suf) >= 11 else suf
        for linha in ws.get_all_values()[1:]:
            tel = re.sub(r'\D', '', linha[0].strip()) if linha else ""
            tel_suf = tel[-11:] if len(tel) >= 11 else tel
            if tel_suf == suf and len(linha) > 1 and linha[1].strip() == image_url:
                print(f"[Imagens] URL duplicada ignorada: {phone}")
                return
        ws.append_row([phone, image_url, data, status, pedido, tipo])
        print(f"[Imagens] Registrada: {phone} (pedido: {pedido or 'nao vinculado'}, tipo: {tipo or '-'})")
    except Exception as e:
        print(f"[Imagens] Erro ao registrar: {e}")

def _forcar_conclusao_descarte(phone):
    """Conclui pedido em aguardando_descarte mesmo sem webhook de delecao.
    Usa confirmar_fotos_pedido para marcar so as primeiras N fotos como pendente."""
    estado = get_estado(phone)
    if estado["status"] != "aguardando_descarte":
        return  # ja concluido via webhook normal
    limite = estado["limite_fotos"]
    tipo = identificar_tipo(estado.get("produto", ""), estado.get("sku", ""))
    enviar_mensagem(phone, f"Perfeito, {limite} fotos {tipo} \u2705")
    enviar_mensagem(phone, MSG_FINALIZAR)
    estado["status"] = "concluido"
    cancelar_timer(phone)
    threading.Thread(
        target=confirmar_fotos_pedido,
        args=(phone, estado.get("pedido", ""), limite),
        daemon=True
    ).start()
    print(f"[Ana] Conclusao forcada descarte: {phone} ({limite} fotos)")

def confirmar_fotos_pedido(phone, pedido, limite):
    """Confirma as primeiras {limite} fotos 'aguardando' do phone -> 'pendente'.
    Excesso marcado como 'descartada'. Chamado em background apos contagem correta."""
    try:
        ws = get_sheet("Imagens")
        if ws is None:
            return
        suf = re.sub(r'\D', '', phone)
        suf = suf[-11:] if len(suf) >= 11 else suf
        linhas = ws.get_all_values()
        confirmadas = 0
        updates = []
        for i, linha in enumerate(linhas[1:], start=2):
            if len(linha) < 4:
                continue
            tel = re.sub(r'\D', '', linha[0].strip())
            tel_suf = tel[-11:] if len(tel) >= 11 else tel
            if tel_suf != suf:
                continue
            if linha[3].strip().lower() != "aguardando":
                continue
            if confirmadas < limite:
                updates.append({"range": f"D{i}", "values": [["pendente"]]})
                confirmadas += 1
            else:
                updates.append({"range": f"D{i}", "values": [["descartada"]]})
        if updates:
            ws.batch_update(updates)
            print(f"[Imagens] {confirmadas} fotos confirmadas p/ {phone} (pedido {pedido})")
        else:
            print(f"[Imagens] Nenhuma foto aguardando para {phone}")
    except Exception as e:
        print(f"[Imagens] Erro confirmar_fotos_pedido: {e}")

def preencher_pedido_retroativo(phone, numero_pedido):
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
            print(f"[Imagens] {len(updates)} imagens â pedido {numero_pedido}")
        return len(updates)
    except Exception as e:
        print(f"[Imagens] Erro retroativo: {e}")
        return 0


def contar_imagens_pedido(numero_pedido):
    """Retorna quantas imagens jÃ¡ foram recebidas para um dado nÃºmero de pedido."""
    try:
        ws = get_sheet("Imagens")
        if ws is None:
            return 0
        linhas = ws.get_all_values()
        count = 0
        for linha in linhas[1:]:
            pedido_col = linha[4].strip() if len(linha) >= 5 else ""
            if pedido_col == numero_pedido:
                count += 1
        return count
    except Exception as e:
        print(f"[Imagens] Erro ao contar imagens: {e}")
        return 0

# ââ MemÃ³ria de clientes (aba Clientes no Sheets) âââââââââââââ
def _suf(phone):
    s = re.sub(r'\D', '', phone)
    return s[-11:] if len(s) >= 11 else s

def carregar_cliente(phone):
    """
    Carrega histÃ³rico do cliente.
    Retorna dict {nome, primeiro_contato, ultimo_pedido, total_pedidos, pedidos} ou None se novo.
    """
    try:
        ws = get_sheet("Clientes", ["Telefone","Nome","Primeiro_Contato","Ultimo_Pedido","Total_Pedidos","Pedidos"])
        if ws is None:
            return None
        suf = _suf(phone)
        for linha in ws.get_all_values()[1:]:
            if not linha:
                continue
            tel_suf = _suf(linha[0])
            if tel_suf == suf:
                return {
                    "nome": linha[1].strip() if len(linha) > 1 else "",
                    "primeiro_contato": linha[2].strip() if len(linha) > 2 else "",
                    "ultimo_pedido": linha[3].strip() if len(linha) > 3 else "",
                    "total_pedidos": int(linha[4]) if len(linha) > 4 and linha[4].strip().isdigit() else 0,
                    "pedidos": linha[5].strip() if len(linha) > 5 else "",
                }
        return None
    except Exception as e:
        print(f"[Clientes] Erro ao carregar {phone}: {e}")
        return None

def salvar_ou_atualizar_cliente(phone, nome="", pedido=""):
    """Cria ou atualiza registro do cliente. Chamado no primeiro contato e ao vincular pedido."""
    try:
        ws = get_sheet("Clientes", ["Telefone","Nome","Primeiro_Contato","Ultimo_Pedido","Total_Pedidos","Pedidos"])
        if ws is None:
            return
        data = datetime.now(BRASILIA).strftime("%d/%m/%Y")
        suf = _suf(phone)
        linhas = ws.get_all_values()
        for i, linha in enumerate(linhas[1:], start=2):
            if not linha:
                continue
            if _suf(linha[0]) == suf:
                # Cliente existente — atualiza campos
                updates = []
                nome_atual = linha[1].strip() if len(linha) > 1 else ""
                total = int(linha[4].strip()) if len(linha) > 4 and linha[4].strip().isdigit() else 0
                historico = linha[5].strip() if len(linha) > 5 else ""
                if nome and not nome_atual:
                    updates.append({'range': f'B{i}', 'values': [[nome]]})
                if pedido:
                    updates.append({'range': f'D{i}', 'values': [[pedido]]})
                    updates.append({'range': f'E{i}', 'values': [[total + 1]]})
                    novo_hist = f"{historico}, {pedido}" if historico else pedido
                    updates.append({'range': f'F{i}', 'values': [[novo_hist]]})
                if updates:
                    ws.batch_update(updates)
                    print(f"[Clientes] Atualizado: {phone} pedido={pedido} nome={nome}")
                return
        # Novo cliente
        total_inicial = 1 if pedido else 0
        ws.append_row([phone, nome, data, pedido, total_inicial, pedido])
        print(f"[Clientes] Novo cliente: {phone} nome={nome}")
    except Exception as e:
        print(f"[Clientes] Erro ao salvar {phone}: {e}")

# ââ Timers ââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

# ââ LÃ³gica de conversa ââââââââââââââââââââââââââââââââââââââââ
def pedir_numero_pedido_timer(phone):
    estado = get_estado(phone)
    if not estado["pedido"]:
        enviar_mensagem(
            phone,
            "Por favor, precisamos do n\u00famero do pedido, "
            "sem ele, n\u00e3o conseguimos identificar a sua compra."
        )
        print(f"[Ana] Timer 30s: pediu n\u00famero do pedido para {phone}")

def verificar_inatividade_fotos(phone):
    estado = get_estado(phone)
    if estado["status"] != "aguardando_fotos":
        return
    limite = estado["limite_fotos"]
    recebidas = estado["fotos_recebidas"]
    # ── Pedido sem limite definido → avalia conclusão ────────────────────
    if limite == 0:
        avaliar_conclusao(phone)
        return
    # ââ CenÃ¡rio de cÃ³pias: 1 foto recebida para pedido com mÃºltiplas ââââââ
    if limite > 1 and recebidas == 1:
        enviar_mensagem(phone, f"Recebi 1 foto! ð¸ SerÃ£o {limite} cÃ³pias dessa mesma foto?")
        estado["status"] = "aguardando_confirmacao_copias"
        print(f"[Ana] {phone}: 1 foto — perguntando {limite} cÃ³pias")
        return
    if limite > 0 and recebidas < limite:
        faltam = limite - recebidas
        enviar_mensagem(
            phone,
            f"Recebemos {recebidas} foto(s), mas seu pedido Ã© de {limite}. \U0001f60a\n"
            f"Faltam {faltam} foto(s) para completar seu pedido!"
        )
        print(f"[Ana] Timer 10s: faltando {faltam} fotos para {phone}")

def _pedir_dimensao_timer(phone):
    estado = get_estado(phone)
    if not estado.get("multi_produto") or estado.get("produto_ativo_idx", -1) >= 0:
        return
    if estado["imgs_antes_pedido"] > 0:
        tipos_pendentes = ", ".join(
            p["tipo"] for p in estado["produtos"] if not p["concluido"]
        )
        enviar_mensagem(
            phone,
            f"Recebemos suas fotos! Por favor, nos informe a dimensÃ£o delas "
            f"({tipos_pendentes})."
        )

def _verificar_inatividade_multiproduto(phone):
    estado = get_estado(phone)
    if estado["status"] != "aguardando_fotos" or not estado.get("multi_produto"):
        return
    idx = estado.get("produto_ativo_idx", -1)
    if idx < 0:
        return
    p = estado["produtos"][idx]
    faltam = p["limite"] - p["recebidas"]
    if faltam > 0:
        enviar_mensagem(phone, f"Ficou faltando {faltam} imagem(ns) de {p['tipo']}.")
        print(f"[Ana] Multi timer: faltando {faltam} fotos {p['tipo']} para {phone}")

def _processar_imagem_multiproduto(phone):
    estado = get_estado(phone)
    produtos = estado["produtos"]
    idx = estado.get("produto_ativo_idx", -1)

    if idx < 0:
        estado["imgs_antes_pedido"] += 1
        iniciar_timer(phone, 30, lambda: _pedir_dimensao_timer(phone))
        print(f"[Ana] Multi {phone}: imagem sem dimensÃ£o ativa ({estado['imgs_antes_pedido']}Âª)")
        return

    p = produtos[idx]
    p["recebidas"] += 1
    estado["fotos_recebidas"] += 1
    print(f"[Ana] Multi {phone}: {p['tipo']} {p['recebidas']}/{p['limite']}")

    if p["recebidas"] >= p["limite"]:
        p["concluido"] = True
        cancelar_timer(phone)

        if all(pp["concluido"] for pp in produtos):
            avaliar_conclusao(phone)
        else:
            proximo = next((pp for pp in produtos if not pp["concluido"]), None)
            if proximo:
                estado["produto_ativo_idx"] = produtos.index(proximo)
                enviar_mensagem(
                    phone,
                    f"â {p['limite']} fotos {p['tipo']} recebidas! "
                    f"Agora envie as {proximo['limite']} fotos {proximo['tipo']}."
                )
    else:
        iniciar_timer(phone, 600, lambda: _verificar_inatividade_multiproduto(phone))

def reavaliar_apos_delecao(phone):
    """Reavalia contagem 30s apÃ³s cliente deletar uma foto."""
    estado = get_estado(phone)
    status_atual = estado["status"]
    if status_atual not in ("aguardando_fotos", "aguardando_descarte"):
        return
    limite = estado["limite_fotos"]
    recebidas = estado["fotos_recebidas"]
    tipo = identificar_tipo(estado.get("produto", ""), estado.get("sku", ""))

    if limite > 0 and recebidas == limite:
        # Bateu exatamente → concluir
        enviar_mensagem(phone, f"Perfeito, {limite} fotos {tipo} \u2705")
        enviar_mensagem(phone, MSG_FINALIZAR)
        estado["status"] = "concluido"
        cancelar_timer(phone)
        threading.Thread(target=confirmar_fotos_pedido, args=(phone, estado.get("pedido", ""), limite), daemon=True).start()
        print(f"[Ana] Dele\u00e7\u00e3o \u2192 pedido conclu\u00eddo exato: {phone}")

    elif limite > 0 and recebidas > limite:
        extras = recebidas - limite
        if status_atual == "aguardando_descarte":
            # Cliente est\u00e1 deletando fotos — dizer quantas faltam deletar ainda
            enviar_mensagem(
                phone,
                f"Ainda faltam {extras} foto(s) para deletar! Por favor, apague mais {extras} foto(s). \U0001f60a"
            )
            # Aguardar próximo webhook de deleção
        else:
            # Fluxo normal: oferecer comprar extras
            preco = PRECOS_EXTRA.get(tipo, 1.00)
            valor = round(extras * preco, 2)
            estado["fotos_extras"] = extras
            estado["valor_extra"] = valor
            valor_str = f"R$ {valor:.2f}".replace(".", ",")
            unitario_str = f"R$ {preco:.2f}".replace(".", ",")
            enviar_mensagem(
                phone,
                f"Recebemos {recebidas} fotos, mas seu pedido \u00e9 de {limite}. \U0001f60a\n"
                f"Ficaram {extras} foto(s) a mais, que custam {valor_str} no total "
                f"({unitario_str} cada).\n\nDeseja comprar as {extras} foto(s) extras?"
            )
            estado["status"] = "aguardando_resposta_extras"
            cancelar_timer(phone)

    elif limite > 0 and recebidas < limite:
        # Deletou fotos demais — pedir para enviar mais
        faltam = limite - recebidas
        estado["status"] = "aguardando_fotos"
        enviar_mensagem(
            phone,
            f"Ops! Ficamos com {recebidas} foto(s), mas seu pedido \u00e9 de {limite}. \U0001f60a\n"
            f"Por favor, envie mais {faltam} foto(s) para completar seu pedido!"
        )
        iniciar_timer(phone, 600, lambda: verificar_inatividade_fotos(phone))


    elif limite > 0 and recebidas < limite:
        faltam = limite - recebidas
        enviar_mensagem(
            phone,
            f"Atenção! Você deletou mais fotos do que o necessário. "
            f"Seu pedido é de {limite} fotos e recebemos apenas {recebidas}. "
            f"Por favor, envie mais {faltam} foto(s)."
        )
        estado["status"] = "aguardando_fotos"
        iniciar_timer(phone, 600, lambda: verificar_inatividade_fotos(phone))
        print(f"[Ana] Deleção excessiva: {recebidas}/{limite} para {phone}")

def avaliar_conclusao_timer(phone):
    """Chamado 10s apÃ³s Ãºltima foto — confirma se ainda estÃ¡ em aguardando_fotos e conclui."""
    estado = get_estado(phone)
    if estado["status"] == "aguardando_fotos":
        avaliar_conclusao(phone)

def avaliar_conclusao(phone):
    estado = get_estado(phone)
    limite = estado["limite_fotos"]
    recebidas = estado["fotos_recebidas"]
    tipo = identificar_tipo(estado["produto"], estado["sku"])

    if estado.get("multi_produto"):
        produtos = estado["produtos"]
        resumo = " e ".join(f"{p['limite']} fotos {p['tipo']}" for p in produtos)
        enviar_mensagem(phone, f"Perfeito, {resumo}! â")
        enviar_mensagem(phone, MSG_FINALIZAR)
        estado["status"] = "concluido"
        cancelar_timer(phone)
        return

    if limite == 0:
        enviar_mensagem(phone, MSG_FINALIZAR)
        estado["status"] = "concluido"
        return

    if recebidas == limite:
        enviar_mensagem(phone, f"Perfeito, {limite} fotos {tipo} \u2705")
        enviar_mensagem(phone, MSG_FINALIZAR)
        estado["status"] = "concluido"
        cancelar_timer(phone)
        threading.Thread(target=confirmar_fotos_pedido, args=(phone, estado.get("pedido", ""), limite), daemon=True).start()

    elif recebidas > limite:
        extras = recebidas - limite
        estado["fotos_extras"] = extras
        preco = PRECOS_EXTRA.get(tipo, 1.00)
        valor = round(extras * preco, 2)
        estado["valor_extra"] = valor
        valor_str = f"R$ {valor:.2f}".replace(".", ",")
        unitario_str = f"R$ {preco:.2f}".replace(".", ",")
        enviar_mensagem(
            phone,
            f"Recebemos {recebidas} fotos, mas seu pedido Ã© de {limite}. \U0001f60a\n"
            f"Ficaram {extras} foto(s) a mais, que custam {valor_str} no total "
            f"({unitario_str} cada).\n\n"
            f"Deseja comprar as {extras} foto(s) extras?"
        )
        estado["status"] = "aguardando_resposta_extras"
        cancelar_timer(phone)

    elif recebidas < limite:
        pass  # timer de inatividade jÃ¡ rodando

def _vincular_background(phone, numero_pedido, estado, is_multi):
    """OperaÃ§Ãµes pesadas de Sheets em background apÃ³s vincular pedido."""
    try:
        atualizar_telefone_na_planilha(numero_pedido, phone)
        nome_cliente = estado.get("nome_cliente", "")
        salvar_ou_atualizar_cliente(phone, nome=nome_cliente, pedido=numero_pedido)
        fotos_existentes = contar_imagens_pedido(numero_pedido)
        qtd_retro = preencher_pedido_retroativo(phone, numero_pedido)
        imgs_antes = estado.get("imgs_antes_pedido", 0)
        # Sempre limitar ao que foi enviado NESTA sessão (evita contar fotos de sessões anteriores)
        qtd_retro = min(qtd_retro, imgs_antes)
        if qtd_retro > 0:
            print(f"[Ana] {qtd_retro} fotos retroativas para {phone}")
        total = qtd_retro  # fotos_existentes removido: evita contar sessões anteriores
        estado["fotos_recebidas"] = total
        estado["imgs_antes_pedido"] = 0
        if total > 0:
            print(f"[Ana] {phone}: {total} fotos iniciais pedido {numero_pedido} (sheet={fotos_existentes}, retro={qtd_retro})")
        if not is_multi and estado["limite_fotos"] > 0:
            if total >= estado["limite_fotos"]:
                avaliar_conclusao(phone)
            elif total > 0:
                iniciar_timer(phone, 600, lambda: verificar_inatividade_fotos(phone))
    except Exception as e:
        print(f"[Ana] Erro _vincular_background {phone}: {e}")

def vincular_pedido(phone, numero_pedido):
    dados = buscar_pedido_na_planilha(numero_pedido)
    if not dados:
        print(f"[Ana] Pedido {numero_pedido} nÃ£o encontrado na planilha")
        return False

    estado = get_estado(phone)
    produto = dados.get("produto", "")
    sku = dados.get("sku", "")
    tipo = identificar_tipo(produto, sku)
    limite = extrair_limite_fotos(sku)

    # ââ Reset COMPLETO dos contadores ao trocar de pedido ââââââââââââââââââ
    estado["pedido"] = numero_pedido
    estado["produto"] = produto
    estado["sku"] = sku
    estado["limite_fotos"] = limite
    estado["status"] = "aguardando_fotos"
    estado["fotos_recebidas"] = 0      # sempre zera ao vincular novo pedido
    estado["imgs_antes_pedido"] = 0     # zera fotos antes do pedido nesta sessão
    estado["fotos_extras"] = 0
    estado["valor_extra"] = 0.0
    estado["multi_produto"] = False
    estado["produtos"] = []
    estado["produto_ativo_idx"] = -1

    telefone_pedido[phone] = numero_pedido

    # ââ Detecta multi-produto ââââââââââââââââââââââââââââââââââââââââââââââââ
    produtos_parsed = parse_sku_produtos(sku)
    is_multi = len(produtos_parsed) > 1
    if is_multi:
        estado["multi_produto"] = True
        estado["produtos"] = produtos_parsed
        estado["produto_ativo_idx"] = -1
        estado["limite_fotos"] = sum(p["limite"] for p in produtos_parsed)
        print(f"[Ana] Pedido {numero_pedido} multi-produto: {[p['tipo'] for p in produtos_parsed]}")
        enviar_mensagem(
            phone,
            f"Pedido identificado com sucesso! ð\n{msg_orientacao_multiproduto(produtos_parsed)}"
        )
    elif limite > 0:
        enviar_mensagem(phone, f"Pedido identificado com sucesso! ð Agora Ã© sÃ³ enviar suas {limite} fotos para darmos continuidade ao seu pedido.")
    else:
        enviar_mensagem(phone, f"Pedido identificado com sucesso! ð Pode enviar suas fotos para darmos continuidade ao seu pedido.")

    print(f"[Ana] Pedido {numero_pedido} vinculado: limite={estado['limite_fotos']} tipo={tipo}")

    # ââ Sheets em background — resposta jÃ¡ foi enviada acima ââââââââââââââââ
    threading.Thread(
        target=_vincular_background,
        args=(phone, numero_pedido, estado, is_multi),
        daemon=True
    ).start()

    return True
def _salvar_imagem_em_background(phone, image_url, pedido, tipo_img, subpasta=""):
    """Upload no Drive + Sheets em background, sem bloquear o timer."""
    try:
        drive_url = _upload_imagem_drive(image_url, phone, pedido=pedido, tipo=tipo_img, subpasta=subpasta)
        salvar_imagem_pendente(phone, drive_url, pedido, tipo_img, status="aguardando")
    except Exception as e:
        print(f"[Ana] Erro background imagem {phone}: {e}")

def processar_imagem_recebida(phone, image_url):
    estado = get_estado(phone)
    # ── PIX comprovante ──────────────────────────────────────────
    if estado["status"] == "aguardando_pagamento" or estado.get("expecting_pix"):
        enviar_mensagem(phone, "Obrigado! 🙏 PIX recebido com sucesso!")
        estado["status"] = "concluido"
        estado["expecting_pix"] = False
        return

    # ── Troca de fotos: receber fotos originais ────────────────────
    if estado["status"] == "aguardando_foto_troca_original":
        estado.setdefault("fotos_troca_originais", []).append(image_url)
        cancelar_timer(phone)
        def _fechar_troca_original(ph=phone):
            e = get_estado(ph)
            n = len(e.get("fotos_troca_originais", []))
            enviar_mensagem(ph, f"Recebi {n} foto(s). Agora me envie as fotos que você quer imprimir no lugar. 😊")
            e["status"] = "aguardando_foto_troca_nova"
        iniciar_timer(phone, 8, _fechar_troca_original)
        return

    # ── Troca de fotos: receber fotos novas ───────────────────────
    if estado["status"] == "aguardando_foto_troca_nova":
        estado.setdefault("fotos_troca_novas", []).append(image_url)
        pedido = estado.get("pedido", "")
        tipo_img = identificar_tipo(estado.get("produto", ""), estado.get("sku", ""))
        _salvar_imagem_em_background(phone, image_url, pedido, tipo_img, "troca")
        cancelar_timer(phone)
        def _fechar_troca_nova(ph=phone):
            e = get_estado(ph)
            n = len(e.get("fotos_troca_novas", []))
            enviar_mensagem(ph, f"Troca feita com sucesso! ✅ Recebemos {n} foto(s) nova(s) para o seu pedido.")
            e["status"] = "aguardando_fotos"
        iniciar_timer(phone, 8, _fechar_troca_nova)
        return

    if estado["status"] == "concluido":
        # Cliente recorrente: salva foto e pede numero do pedido
        print(f"[Ana] Cliente {phone} recorrente - salvando foto e pedindo novo pedido")
        estado["status"] = "aguardando_pedido"
        estado["pedido"] = ""
        estado["fotos_recebidas"] = 0
        estado["imgs_antes_pedido"] = 1
        drive_url = _upload_imagem_drive(image_url, phone, pedido="", tipo="")
        salvar_imagem_pendente(phone, drive_url, "", "")
        nome_part = f" {estado.get('nome_cliente', '')}".rstrip()
        enviar_mensagem(phone, MSG_SAUDACAO_RETORNO.format(nome_part=nome_part))
        iniciar_timer(phone, 30, lambda: pedir_numero_pedido_timer(phone))
        return
    
    pedido = estado.get("pedido", "")
    tipo_img = ""
    if estado.get("multi_produto"):
        idx = estado.get("produto_ativo_idx", -1)
        if idx >= 0:
            tipo_img = estado["produtos"][idx]["tipo"]
    elif pedido:
        tipo_img = identificar_tipo(estado.get("produto", ""), estado.get("sku", ""))

    # ââ Upload em background — nÃ£o bloqueia o timer ââââââââââââââââââââââââ
    estado["ultima_imagem_url"] = image_url  # guarda para cenÃ¡rio de cÃ³pias
    threading.Thread(
        target=_salvar_imagem_em_background,
        args=(phone, image_url, pedido, tipo_img),
        daemon=True
    ).start()

    # ââ Incrementa contador e reinicia timer IMEDIATAMENTE âââââââââââââââââ
    if pedido:
        if estado.get("multi_produto"):
            _processar_imagem_multiproduto(phone)
            return
        estado["fotos_recebidas"] += 1
        _fid = ev_key.get("id", "") or data.get("id", "")
        if _fid:
            estado.setdefault("fotos_ids", []).append(_fid)
        fotos = estado["fotos_recebidas"]
        limite = estado["limite_fotos"]
        print(f"[Ana] {phone}: {fotos}/{limite} fotos")
        if limite > 0 and fotos >= limite:
            iniciar_timer(phone, 30, lambda: avaliar_conclusao_timer(phone))
        else:
            iniciar_timer(phone, 30, lambda: verificar_inatividade_fotos(phone))
    else:
        estado["imgs_antes_pedido"] += 1
        estado["status"] = "aguardando_pedido"
        iniciar_timer(phone, 30, lambda: pedir_numero_pedido_timer(phone))
        print(f"[Ana] {phone}: imagem sem pedido ({estado['imgs_antes_pedido']}Âª)")
def processar_texto_recebido(phone, body):
    estado = get_estado(phone)
    status = estado["status"]
    body_low = body.lower().strip()

    # ââ Tenta extrair nÃºmero do pedido âââââââââââââââââââââââââââ
    numero = extrair_numero_pedido(body)
    if numero and pedido_existe(numero):
        cancelar_timer(phone)
        vincular_pedido(phone, numero)
        print(f"[Webhook] Pedido {numero} vinculado ao telefone {phone}")
        return


    # ââ Tenta extrair nome do cliente se ainda nÃ£o temos âââââââââ
    if not estado.get("nome_cliente"):
        nome_extraido = tentar_extrair_nome(body)
        if nome_extraido:
            estado["nome_cliente"] = nome_extraido
            salvar_ou_atualizar_cliente(phone, nome=nome_extraido)
            print(f"[Ana] Nome extraÃ­do para {phone}: {nome_extraido}")

    # ââ Multi-produto: detecta rÃ³tulo de dimensÃ£o âââââââââââââââââ
    if estado.get("multi_produto") and status == "aguardando_fotos":
        tipo_det = _detectar_tipo_na_mensagem(body)
        if tipo_det:
            for i, p in enumerate(estado["produtos"]):
                if p["tipo"] == tipo_det and not p["concluido"]:
                    estado["produto_ativo_idx"] = i
                    cancelar_timer(phone)
                    print(f"[Ana] Multi {phone}: dimensÃ£o '{tipo_det}' ativa")
                    buf = estado["imgs_antes_pedido"]
                    if buf > 0:
                        estado["imgs_antes_pedido"] = 0
                        p["recebidas"] += buf
                        estado["fotos_recebidas"] += buf
                        if p["recebidas"] >= p["limite"]:
                            p["concluido"] = True
                            if all(pp["concluido"] for pp in estado["produtos"]):
                                avaliar_conclusao(phone)
                            else:
                                prox = next((pp for pp in estado["produtos"] if not pp["concluido"]), None)
                                if prox:
                                    estado["produto_ativo_idx"] = estado["produtos"].index(prox)
                                    enviar_mensagem(phone, f"â {p['limite']} fotos {p['tipo']} recebidas! Agora envie as {prox['limite']} fotos {prox['tipo']}.")
                        elif buf > 0:
                            iniciar_timer(phone, 600, lambda: _verificar_inatividade_multiproduto(phone))
                    return
            return

    # ââ Resposta sobre fotos extras âââââââââââââââââââââââââââââââ
    if status == "aguardando_confirmacao_copias":
        limite = estado["limite_fotos"]
        tipo = identificar_tipo(estado["produto"], estado["sku"])
        pedido_num = estado.get("pedido", "")
        if any(p in body_low for p in ["sim", "yes", "s", "isso", "correto", "exato", "pode"]):
            subpasta = f"{limite} cÃ³pias"
            img_url = estado.get("ultima_imagem_url", "")
            if img_url:
                threading.Thread(
                    target=_salvar_imagem_em_background,
                    args=(phone, img_url, pedido_num, tipo, subpasta),
                    daemon=True
                ).start()
            enviar_mensagem(phone, f"Perfeito! SerÃ£o {limite} cÃ³pias dessa foto. â")
            enviar_mensagem(phone, MSG_FINALIZAR)
            estado["status"] = "concluido"
            cancelar_timer(phone)
        elif any(p in body_low for p in ["nÃ£o", "nao", "nÃ£", "no", "n", "vou", "outras", "mais"]):
            faltam = limite - estado["fotos_recebidas"]
            estado["status"] = "aguardando_fotos"
            enviar_mensagem(phone, f"Ok! Pode continuar enviando. Faltam {faltam} foto(s)! ð")
            iniciar_timer(phone, 600, lambda: verificar_inatividade_fotos(phone))
        return

    if status == "aguardando_resposta_extras":
        if any(p in body_low for p in ["sim", "yes", "quero"]):
            extras = estado["fotos_extras"]
            valor = estado["valor_extra"]
            tipo = identificar_tipo(estado["produto"], estado["sku"])
            enviar_mensagem(
                phone,
                f"O valor das {extras} foto(s) a mais Ã© de R$ {valor:.2f}.\n{MSG_PIX}"
            )
            estado["status"] = "aguardando_pagamento"

        elif any(p in body_low for p in [
            "nÃ£o", "nao", "nÃ£", "no", "n",
            "vou deletar", "vou apagar", "vou excluir",
            "ja deletei", "jÃ¡ deletei", "ja apaguei", "jÃ¡ apaguei",
            "deletar", "apagar", "excluir", "delete", "apago", "deleto",
            "ok", "tudo bem", "certo", "entendido", "combinado"
        ]):
            extras_del = estado.get('fotos_extras', max(0, estado['fotos_recebidas'] - estado['limite_fotos']))
            enviar_mensagem(
                phone,
                f"Tudo bem, delete {extras_del} foto(s) para que possamos dar continuidade ao seu pedido."
            )
            estado["status"] = "aguardando_descarte"
        return

    # ── Aguardando cliente deletar fotos excedentes ───────────────────
    if status == "aguardando_descarte":
        limite = estado["limite_fotos"]
        recebidas = estado["fotos_recebidas"]
        if recebidas <= limite:
            # Webhook ja chegou e decrementou corretamente — concluir
            avaliar_conclusao(phone)
        else:
            # Webhook pode ter atrasado — aguardar 20s e concluir com as primeiras N fotos
            iniciar_timer(phone, 20, lambda: _forcar_conclusao_descarte(phone))
        return

    # ââ Cliente quer enviar menos fotos do que o pedido ââââââââââ
    if status in ("aguardando_fotos", "aguardando_pedido"):
        m_menos = re.search(r's[oÃ³] (\d+)\s*(?:fotos?)?', body_low)
        if m_menos and any(p in body_low for p in ["sÃ³", "so", "apenas", "somente"]):
            qtd_quero = int(m_menos.group(1))
            limite = estado.get("limite_fotos", 0)
            if limite > 0 and qtd_quero < limite:
                enviar_mensagem(
                    phone,
                    f"Sem problema! Podemos fazer sÃ³ as {qtd_quero} fotos mesmo. ð "
                    f"Pode continuar enviando!"
                )
                estado["limite_fotos"] = qtd_quero
                return

    # ââ Comprovante de pagamento (texto) — ignorado ââââââââââââââ
    if status == "aguardando_pagamento":
        return

    # ââ Detecta link do Google Drive ââââââââ──────────────────────────────────────────────────────────
    if re.search(r'https?://', body):
        drive_id = extrair_id_drive(body)
        if drive_id:
            print(f"[Ana] Link Google Drive detectado de {phone}: {drive_id}")
            enviar_mensagem(
                phone,
                "📁 Recebi o link do Google Drive! Estou baixando suas fotos, aguarde um momento... 📸"
            )
            threading.Thread(
                target=processar_pasta_drive,
                args=(phone, drive_id),
                daemon=True
            ).start()
        else:
            enviar_mensagem(
                phone,
                "Infelizmente só consigo processar links do *Google Drive*. 😅\n\n"
                "Vou chamar um atendente para te ajudar!"
            )
            _notificar_atendente_desktop(phone, f"Cliente enviou link não-Drive: {body}", estado)
        return

    # ââ CÃ¡lculo de preÃ§o: ex. "quanto daria 37 fotos imÃ£" ââââââââ
    # ── Troca de fotos ──────────────────────────────────────────
    if any(p in body_low for p in ["posso trocar", "trocar foto", "trocar as foto",
                                    "quero trocar", "mudar as foto", "substituir foto",
                                    "trocar uma foto", "trocar umas", "troca de foto"]):
        enviar_mensagem(phone, "Sim, claro! 😊 Me envie as fotos que você quer trocar.")
        estado["status"] = "aguardando_foto_troca_original"
        estado["fotos_troca_originais"] = []
        estado["fotos_troca_novas"] = []
        return

    resposta_calc = calcular_preco(body_low)
    if resposta_calc:
        enviar_mensagem(phone, resposta_calc)
        print(f"[Ana] CÃ¡lculo respondido para {phone}: {body[:60]}")
        return

    # ââ FAQ: responde perguntas frequentes ââââââââââââââââââââââââ
    resposta_faq = verificar_faq(body_low)
    if resposta_faq:
        # Sinaliza que próxima imagem pode ser comprovante PIX
        if any(k in body_low for k in ["vou te enviar o pix", "vou enviar o pix",
                                        "vou mandar o pix", "vou te mandar o pix",
                                        "vou pagar", "vou fazer o pix", "comprovante"]):
            estado["expecting_pix"] = True
        enviar_mensagem(phone, resposta_faq)
        print(f"[Ana] FAQ respondido para {phone}: {body[:60]}")
        return

    # -- Saudacoes: responde com a saudacao correta em qualquer status --
    if "bom dia" in body_low or "boa tarde" in body_low or "boa noite" in body_low:
        if "boa tarde" in body_low:
            saudacao = "Boa tarde"
        elif "boa noite" in body_low:
            saudacao = "Boa noite"
        else:
            saudacao = "Bom dia"
        enviar_mensagem(phone, f"{saudacao}! No que posso ajudar? 😊")
        return

        # -- Agradecimentos: responde gentilmente em qualquer status --
    if any(p in body_low for p in [
        "obrigada", "obrigado", "obg", "vlw", "valeu", "grata",
        "muito obrigad", "obrigadinha", "brigadinha", "brigada",
        "thanks", "thank you"
    ]):
        enviar_mensagem(phone, "Nós que agradecemos pela compra, precisando de algo mais, estaremos aqui. 😊")
        return


    print(f"[Ana] Texto nÃ£o reconhecido de {phone}: {body[:60]}")
    # Avisar cliente e notificar atendente
    enviar_mensagem(phone, "NÃ£o entendi sua mensagem. 😅 Deixa eu chamar um atendente para te ajudar!")
    _notificar_atendente_desktop(phone, body, estado)

# ââ ExtraÃ§Ã£o do nÃºmero de pedido âââââââââââââââââââââââââââââ
def extrair_numero_pedido(texto):
    candidatos = PEDIDO_REGEX.findall(texto.upper())
    for c in candidatos:
        if any(ch.isalpha() for ch in c) and any(ch.isdigit() for ch in c):
            return c
    return candidatos[0] if candidatos else None

# ââ Thread IMAP — monitora Gmail âââââââââââââââââââââââââââââ
pedidos_processados = set()
_alertas_atendente = {}  # phone -> timestamp do ultimo alerta (cooldown 10min)

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
        # Busca apenas emails NÃO LIDOS da Shopee dos últimos 7 dias
        desde = (datetime.now(BRASILIA) - timedelta(days=7)).strftime("%d-%b-%Y")
        _, msgs = mail.search(None, f'UNSEEN FROM "info@mail.shopee.com.br" SINCE {desde}')
        ids = msgs[0].split()
        print(f"[IMAP] {len(ids)} emails não lidos da Shopee (últimos 7 dias) encontrados.")

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
                msg = email.message_from_bytes(data[0][1])
                assunto = msg.get("Subject", "")
                print(f"[IMAP] Email encontrado - assunto: {assunto[:80]}")
                # Filtrar apenas emails de pedido (ignorar promoções e outros)
                assunto_low = assunto.lower()
                palavras_pedido = ["hora de enviar", "pedido", "enviar", "preparar", "order"]
                palavras_ignorar = ["devolucao", "devolução", "cancelamento", "cancelado", "reembolso", "disputa", "estorno"]
                if any(p in assunto_low for p in palavras_ignorar):
                    print(f"[IMAP] Ignorando email de devolucao/cancelamento: {assunto[:60]}")
                    pedidos_processados.add(eid)
                    mail.store(eid, '+FLAGS', '\\Seen')
                    continue
                if not any(p in assunto_low for p in palavras_pedido):
                    print(f"[IMAP] Ignorando email não relacionado a pedido: {assunto[:60]}")
                    pedidos_processados.add(eid)
                    continue
                corpo = extrair_corpo_email(msg)

                m_subj = re.search(r'pedido\s+([A-Z0-9]{10,20})', assunto, re.IGNORECASE)
                numero = (m_subj.group(1).upper() if m_subj
                          else (extrair_numero_pedido(assunto) or extrair_numero_pedido(corpo)))

                if not numero:
                    print(f"[IMAP] Número de pedido não encontrado no email: {assunto[:60]}")
                elif numero.upper() in pedidos_na_planilha:
                    print(f"[IMAP] Pedido {numero} já está na planilha, ignorando.")
                if numero and numero.upper() not in pedidos_na_planilha:
                    produto = quantidade = sku = cliente = prazo = ""

                    m_prod = re.search(
                        r'ID do pedido:\s*#?' + re.escape(numero) +
                        r'[\s\S]{0,50}?([A-Za-zÃ-Ãº][^\n\t]{10,})',
                        corpo, re.IGNORECASE
                    )
                    if m_prod:
                        raw_prod = m_prod.group(1).strip().rstrip('.')
                        for marcador in ['Quantidade SKU', 'SKU ', 'ID do pedido', 'Vendedor:', 'Entrega ', 'QUAL É O', 'Abraços']:
                            idx_m = raw_prod.find(marcador)
                            if idx_m > 0:
                                raw_prod = raw_prod[:idx_m].strip()
                        produto = raw_prod

                    m_qtd = re.search(r'Quantidade\s+(\d+)', corpo)
                    if m_qtd:
                        quantidade = m_qtd.group(1).strip()

                    # Extração de SKU e quantidade (lógica definitiva)
                    # Padrão Shopee: "1002 - 20 FOTOS" → 20 fotos
                    qtds_sku = re.findall(r'\d+\s*-\s*(\d+)\s+FOTOS?', corpo, re.IGNORECASE)
                    # Dimensões válidas: apenas tamanhos de foto conhecidos
                    _DIMS_VALIDAS = {"10X15", "15X21"}
                    dims_raw = re.findall(r'(\d{2,3}[xX]\d{2,3})', corpo)
                    dims_unique = []
                    for d in [x.upper() for x in dims_raw]:
                        if d in _DIMS_VALIDAS and (not dims_unique or dims_unique[-1] != d):
                            dims_unique.append(d)

                    if qtds_sku:
                        if len(qtds_sku) == len(dims_unique) and dims_unique:
                            partes = [f"{q} fotos {d}" for q, d in zip(qtds_sku, dims_unique)]
                        elif dims_unique:
                            # Quando qtds != dims: identifica tipo de cada produto pelo contexto no email
                            _sku_iters = list(re.finditer(r'\d+\s*-\s*(\d+)\s+FOTOS?', corpo, re.IGNORECASE))
                            _SFXM = {"Fotos Retro": "retrô", "Fotos Retro com ima": "retrô com imã",
                                     "Mini Fotos": "mini", "Mini Fotos com ima": "mini com imã",
                                     "Mini Fotos Retro": "mini retrô", "Mini Fotos Retro com ima": "mini retrô com imã",
                                     "15X21": "15X21", "A4": "A4", "10X15": "10X15"}
                            if len(_sku_iters) >= 2:
                                partes = []
                                for _mi in _sku_iters:
                                    _qtd = _mi.group(1)
                                    _trecho = corpo[max(0, _mi.start() - 400): _mi.start()]
                                    _tipo = identificar_tipo('', _trecho)
                                    _parte = f"{_qtd} fotos {_SFXM.get(_tipo, _tipo)}"
                                    if _parte not in partes:
                                        partes.append(_parte)
                            else:
                                partes = [f"{q} fotos {dims_unique[0]}" for q in qtds_sku]
                        else:
                            pasta = identificar_pasta(produto)
                            _tipos_dim = {"10X15", "15X21", "A4"}
                            if pasta in _tipos_dim:
                                partes = [f"{q} fotos {pasta}" for q in qtds_sku]
                            else:
                                partes = [f"{q} {pasta}" for q in qtds_sku]
                        sku = ' + '.join(partes)
                        quantidade = qtds_sku[0] if len(qtds_sku) == 1 else ''
                    else:
                        # Fallback: outros padrões
                        m_sku = re.search(r'Varia\w+\s*[:\s]+([^\n\t<]{3,80})', corpo, re.IGNORECASE)
                        if not m_sku:
                            m_sku = re.search(r'SKU[:\s]+([^\n\t<]{3,60})', corpo, re.IGNORECASE)
                        if not m_sku:
                            m_sku = re.search(r'SKU\s+\d+\s+([^\n]{3,60})', corpo, re.IGNORECASE)
                        if m_sku:
                            sku_raw = re.split(r'[\[\(]', m_sku.group(1).strip())[0].strip()
                            sku_raw = re.sub(r'\d{4,}\s*-?\s*', '', sku_raw).strip()
                            sku_raw = re.sub(r'\s+', ' ', sku_raw).strip()
                            sku = sku_raw
                        else:
                            m_kit = re.search(r'(KIT\s+(?:AT[EÉ]\s+)?\d+\s+FOTOS?)', corpo, re.IGNORECASE)
                            if m_kit:
                                m_num = re.search(r'(\d+)\s*FOTO', m_kit.group(1).upper())
                                sku = (m_num.group(1) + ' fotos') if m_num else m_kit.group(1).strip()

                    # ── Cliente: múltiplos padrões de fallback ───────────────
                    mc = re.search(r'Envie\w*\s+o\s+pedido\s+para\s+([^\.\n\r<]{3,60})', corpo, re.IGNORECASE)
                    if not mc:
                        mc = re.search(r'Entregar\s+para[:\s]+([^\.\n\r<]{3,60})', corpo, re.IGNORECASE)
                    if not mc:
                        mc = re.search(r'destinat[aá]rio[:\s]+([^\.\n\r<]{3,60})', corpo, re.IGNORECASE)
                    if not mc:
                        mc = re.search(r'Nome[:\s]+([A-Za-zÀ-ú][^\.\n\r<]{2,50})', corpo, re.IGNORECASE)
                    if mc:
                        cliente = mc.group(1).strip().rstrip('.')
                    # ── Prazo: múltiplos padrões de fallback ─────────────────
                    mp = re.search(r'(At[eé]\s+\d+\s+de\s+\w+)', corpo, re.IGNORECASE)
                    if not mp:
                        mp = re.search(r'(At[eé]\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?)', corpo, re.IGNORECASE)
                    if not mp:
                        mp = re.search(r'prazo\w*[:\s]+(\d+\s+de\s+\w+)', corpo, re.IGNORECASE)
                    if mp:
                        prazo = mp.group(1).strip()

                    if not produto and not sku:
                        print(f"[IMAP] Pedido {numero} sem produto/SKU, ignorando.")
                        pedidos_processados.add(eid)
                        continue
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
                # Marcar email como lido no Gmail para não reprocessar após restart
                try:
                    mail.store(eid, '+FLAGS', '\\Seen')
                except Exception:
                    pass

        print(f"[IMAP] {novos} novos pedidos.")
        mail.logout()
    except Exception as e:
        print(f"[IMAP] Erro: {e}")

def thread_gmail():
    print("[IMAP] Thread Gmail iniciada")
    while True:
        verificar_gmail()
        time.sleep(60)

# ââ Webhook WhatsApp ââââââââââââââââââââââââââââââââââââââââââ
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print(f"[Webhook] PAYLOAD: {json.dumps(data)[:400]}")
        _ultimos_payloads.append({"ts": str(__import__("datetime").datetime.now()), "data": data})
        if len(_ultimos_payloads) > 10:
            _ultimos_payloads.pop(0)

        # ── Suporta Z-API e Evolution API ────────────────────────────
        ev_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        ev_key  = ev_data.get("key") if isinstance(ev_data.get("key"), dict) else {}

        from_me = data.get("fromMe", ev_key.get("fromMe", False))
        if from_me:
            return "ok", 200

        # Extrai phone: Z-API usa data.phone | Evolution API usa data.data.key.remoteJid
        phone = (data.get("phone", "")
                 .replace("@s.whatsapp.net", "")
                 .replace("@c.us", ""))
        if not phone and ev_key:
            rjid = ev_key.get("remoteJid", "")
            if "@g.us" in rjid:
                return "ok", 200  # ignora mensagens de grupos
            phone = rjid.replace("@s.whatsapp.net", "").replace("@c.us", "")
        if not phone:
            return "ok", 200

        # Ignora eventos onde phone é o próprio número da instância (Z-API: connectedPhone)
        connected = re.sub(r'\D', '', str(data.get("connectedPhone", "")))
        if connected and re.sub(r'\D', '', phone)[-11:] == connected[-11:]:
            print(f"[Webhook] Ignorando evento phone proprio ({phone})")
            return "ok", 200

        # msg_type: Z-API usa "type"/"tipo" | Evolution API usa data.data.messageType
        msg_type = data.get("type") or data.get("tipo") or ev_data.get("messageType") or ""
        # DEBUG: log raw payload para diagnostico de documentos
        print(f"[DEBUG] keys={list(data.keys())} msg_type={msg_type!r} mimeType={data.get('mimeType')!r} fileName={data.get('fileName')!r}")

        def extrair_texto(d):
            v = d.get("body") or d.get("text") or d.get("texto") or ""
            if isinstance(v, dict):
                return v.get("message") or v.get("body") or v.get("text") or ""
            # Evolution API: texto em data.data.message.conversation ou extendedTextMessage
            if not v and ev_data:
                ev_msg = ev_data.get("message") or {}
                v = (ev_msg.get("conversation")
                     or (ev_msg.get("extendedTextMessage") or {}).get("text")
                     or "")
            return str(v) if v else ""

        def extrair_image_url(d):
            for chave in ("imagem", "image"):
                v = d.get(chave)
                if isinstance(v, dict):
                    return v.get("imageUrl") or v.get("url") or v.get("mediaUrl") or ""
                if isinstance(v, str) and v.startswith("http"):
                    return v
            # Evolution API: URL em data.data.message.imageMessage.mediaUrl
            if ev_data:
                ev_msg = ev_data.get("message") or {}
                img = ev_msg.get("imageMessage") or {}
                url = img.get("mediaUrl") or img.get("url") or ""
                if url:
                    return url
            return d.get("imageUrl") or d.get("mediaUrl") or ""

        body = extrair_texto(data)

        # ââ Detecta mensagem deletada/revogada pelo cliente âââââââ
        # Z-API pode usar vÃ¡rios campos diferentes para indicar deleÃ§Ã£o
        tipo_lower = str(msg_type).lower()
        notif_lower = str(data.get("notification") or "").lower()
        if notif_lower:
            print(f"[Ana] notification={notif_lower!r} notifParams={data.get('notificationParameters')!r}")
        is_deleted = (
            data.get("isRevoked") is True
            or data.get("revoked") is True
            or data.get("deleted") is True
            or data.get("isDeleted") is True
            or tipo_lower in ("revoked", "deleted", "messagerevoked", "delete",
                              "revokedmessage", "deletedmessage", "revoke")
            or "revok" in tipo_lower
            or "delet" in tipo_lower
            or "revok" in notif_lower
            or "delet" in notif_lower
            or "apag" in notif_lower
        )
        if is_deleted:
            print(f"[Ana] Mensagem deletada detectada de {phone} (type={msg_type})")
            estado = get_estado(phone)
            deleted_id = ev_key.get("id", "") or data.get("id", "")
            fotos_ids = estado.get("fotos_ids", [])
            # Só decrementa se o ID deletado era uma foto contada (evita decrementar por msgs de texto)
            if deleted_id and fotos_ids and deleted_id not in fotos_ids:
                return "ok", 200  # não era foto contada — ignorar
            if estado["status"] in ("aguardando_fotos", "aguardando_descarte") and estado["fotos_recebidas"] > 0:
                estado["fotos_recebidas"] = max(0, estado["fotos_recebidas"] - 1)
                if deleted_id and deleted_id in fotos_ids:
                    fotos_ids.remove(deleted_id)
                print(f"[Ana] Foto deletada: agora {estado['fotos_recebidas']}/{estado['limite_fotos']}")
                iniciar_timer(phone, 30, lambda: reavaliar_apos_delecao(phone))
            return "ok", 200

        # ââ Detecta imagem enviada como documento âââââââââââââââââ
        tem_documento_imagem = False
        # Z-API usa type="ReceivedCallback" para tudo; documento fica em data["document"]
        _doc_zapi = data.get("document") if isinstance(data.get("document"), dict) else None
        if msg_type in ("document", "documentMessage") or _doc_zapi:
            doc = _doc_zapi or {}
            # Evolution API: documento em data.data.message.documentMessage
            if not doc and ev_data:
                doc = (ev_data.get("message") or {}).get("documentMessage") or {}
            # Z-API envia mimeType e fileName no nivel raiz (nao aninhado em "document")
            mime = ((doc.get("mimeType") if isinstance(doc, dict) else None) or data.get("mimeType") or "").lower()
            fname = ((doc.get("fileName") if isinstance(doc, dict) else None) or data.get("fileName") or "").lower()
            tem_documento_imagem = (
                "image/" in mime
                or any(fname.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"])
            )

        # Fallback universal: Z-API pode enviar qualquer type (nao so "document")
        # Se tiver fileName ou mimeType na raiz indicando imagem, trata como imagem-doc
        if not tem_documento_imagem:
            _mime_raiz = (data.get("mimeType") or "").lower()
            _fname_raiz = (data.get("fileName") or "").lower()
            if _mime_raiz or _fname_raiz:
                tem_documento_imagem = (
                    "image/" in _mime_raiz
                    or any(_fname_raiz.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"])
                )

        tem_imagem = (
            msg_type in ("image", "imagem", "imageMessage")
            or bool(data.get("image"))
            or bool(data.get("imagem"))
            or (isinstance(body, str) and body.startswith("http")
                and any(ext in body.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]))
            or tem_documento_imagem
            or bool(ev_data and (ev_data.get("message") or {}).get("imageMessage"))
        )
        image_url = ""
        if tem_imagem:
            if tem_documento_imagem:
                doc = data.get("document") or {}
                if not doc and ev_data:
                    doc = (ev_data.get("message") or {}).get("documentMessage") or {}
                image_url = (doc.get("url") or doc.get("documentUrl") or doc.get("mediaUrl") or doc.get("imageUrl") or "") if isinstance(doc, dict) else ""
                # Z-API: URL do documento pode estar em body
                if not image_url and body and body.startswith("http"):
                    image_url = body
            else:
                image_url = body if (body and body.startswith("http")) else extrair_image_url(data)

        print(f"[Webhook] phone={phone} tipo={msg_type} imagem={tem_imagem} doc_img={tem_documento_imagem} body={str(body)[:60]}")

     # ââ SaudaÃ§Ã£o automÃ¡tica (primeiro contato) ââââââââââââââââââââââââââââ
        estado = get_estado(phone)
        if estado["status"] == "novo":
            # Sem conteÃºdo real (deletado, sistema, etc.) â ignora silenciosamente
            body_vazio = not body or not body.strip()
            if body_vazio and not tem_imagem:
                print(f"[Ana] Webhook sem conteÃºdo de {phone} ignorado (tipo={msg_type})")
                return "ok", 200

            historico = carregar_cliente(phone)

            if historico and historico["total_pedidos"] > 0:
                # Cliente recorrente com pedidos
                nome = historico["nome"]
                if nome:
                    estado["nome_cliente"] = nome

                ultimo = historico.get("ultimo_pedido", "")
                if ultimo:
                    # Restaura pedido silenciosamente (reinÃ­cio do servidor)
                    dados_ped = buscar_pedido_na_planilha(ultimo)
                    if dados_ped:
                        estado["produto"] = dados_ped.get("produto", "")
                        estado["sku"] = dados_ped.get("sku", "")
                        estado["limite_fotos"] = extrair_limite_fotos(dados_ped.get("sku", ""))
                    fotos_ja = contar_imagens_pedido(ultimo)
                    estado["fotos_recebidas"] = fotos_ja
                    estado["pedido"] = ultimo
                    estado["status"] = "aguardando_fotos"
                    print(f"[Ana] ReinÃ­cio: restaurando pedido {ultimo} ({fotos_ja} fotos) para {phone}")
                else:
                    # Cliente com pedidos anteriores mas sem pedido ativo — saudaÃ§Ã£o de retorno
                    nome_part = f", {nome}" if nome else ""
                    saudacao = MSG_SAUDACAO_RETORNO.format(nome_part=nome_part)
                    enviar_mensagem(phone, saudacao)
                    estado["status"] = "aguardando_pedido"
                    print(f"[Ana] Cliente recorrente sem pedido ativo: {phone}")

            elif historico:
                # Ja foi saudado antes (reinicio do servidor) — nao repete saudacao
                estado["status"] = "aguardando_pedido"
                if historico.get("nome"):
                    estado["nome_cliente"] = historico["nome"]
                print(f"[Ana] Cliente ja saudado: {phone} — sem saudacao")

            else:
                # Primeiro contato de verdade
                threading.Thread(
                    target=salvar_ou_atualizar_cliente,
                    args=(phone,),
                    daemon=True
                ).start()
                body_faq = body if (body and not body.startswith("http")) else ""
                faq_inicial = verificar_faq(body_faq.lower().strip()) if body_faq else None
                if faq_inicial:
                    msg_combinada = ("OlÃ¡, seja bem-vindo Ã  Personalizei! Obrigado pela sua compra. ð" "\n\n" + faq_inicial)
                    enviar_mensagem(phone, msg_combinada)
                    estado["status"] = "aguardando_pedido"
                    return "ok", 200
                else:
                    enviar_mensagem(phone, MSG_SAUDACAO)
                estado["status"] = "aguardando_pedido"

        # ââ Processa imagem âââââââââââââââââââââââââââââââââââââââ
        if tem_imagem and image_url:
            processar_imagem_recebida(phone, image_url)

        # ââ Processa texto ââââââââââââââââââââââââââââââââââââââââ
        elif body:
            processar_texto_recebido(phone, body)

        return "ok", 200

    except Exception as e:
        import traceback
        print(f"[Webhook] Erro: {e}")
        traceback.print_exc()
        return "ok", 200

@app.route("/debug-payloads", methods=["GET"])
def debug_payloads():
    return json.dumps(_ultimos_payloads, ensure_ascii=False, default=str), 200, {"Content-Type": "application/json"}

@app.route("/", methods=["GET"])
def health():
    return "Ana Bot OK", 200

@app.route("/pausar", methods=["GET"])
def pausar_mensagens():
    global _pausa_mensagens
    token = request.args.get("token", "")
    if token != "personalizeifotospausar":
        return "Token inválido", 403
    _pausa_mensagens = True
    return "✅ Ana PAUSADA — mensagens bloqueadas, imagens continuam normalmente.", 200

@app.route("/retomar", methods=["GET"])
def retomar_mensagens():
    global _pausa_mensagens
    token = request.args.get("token", "")
    if token != "personalizeifotospausar":
        return "Token inválido", 403
    _pausa_mensagens = False
    return "✅ Ana RETOMADA — mensagens enviando normalmente.", 200

@app.route("/desativar", methods=["GET"])
def desativar_bot():
    global ANA_ATIVA
    token = request.args.get("token", "")
    if token != "personalizeifotospausar":
        return "Token inválido", 403
    ANA_ATIVA = False
    return "🛑 Ana DESATIVADA por completo — nenhuma mensagem ou ação será executada.", 200

@app.route("/ativar", methods=["GET"])
def ativar_bot():
    global ANA_ATIVA
    token = request.args.get("token", "")
    if token != "personalizeifotospausar":
        return "Token inválido", 403
    ANA_ATIVA = True
    return "✅ Ana ATIVADA — funcionando normalmente.", 200

_imap_thread = threading.Thread(target=thread_gmail, daemon=True)
_imap_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
