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


# Configuracoes
GMAIL_USER         = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SPREADSHEET_ID     = "1qbLhiP9g1I9Lp3LemmOw5qoNfW8y6wQyBzafseft6Fc"
ZAPI_INSTANCE      = "3F353F900771725020A0F6B0730C054E"
ZAPI_TOKEN         = "2E4ECDD70099CF7EDCEAF35E"
ZAPI_BASE_URL      = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}"


MSG_SAUDACAO = (
    "Olá, seja bem-vindo à Personalizei! Obrigado pela sua compra. 😊\n\n"
    "Antes de enviar qualquer imagem, é de extrema importância que você nos envie primeiro "
    "o número do pedido. Esse número está logo após as letras ID: no seu comprovante de compra.\n\n"
    "Por favor, digite ou copie e cole o número — não envie print, pois nosso sistema "
    "não consegue identificar imagens de texto."
)
MSG_PEDIR_PEDIDO = (
    "Por favor envie o numero do pedido, esse numero vem logo depois das letras ID:, "
