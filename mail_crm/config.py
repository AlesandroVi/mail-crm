"""Configuracion central del proyecto."""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Los asuntos de los mails traen emojis y simbolos que algunas consolas de Windows
# no saben mostrar. Con esto se reemplazan por "?" en vez de romper el programa.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(errors="replace")

# Archivos de Google (ver README para saber como conseguirlos)
CREDENTIALS_PATH = RAIZ / "credentials.json"  # lo descargas de Google Cloud
TOKEN_PATH = RAIZ / "token.json"              # se crea solo al iniciar sesion

# Base de datos local
DB_PATH = RAIZ / "crm.db"

# Permiso que le pedimos a Gmail: "modify" permite leer, mover a la papelera,
# archivar y destacar. NO permite borrar mails de forma definitiva.
# Si cambias esto, la app te pide iniciar sesion de nuevo.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Que mails traer en cada sincronizacion (misma sintaxis que el buscador de Gmail)
GMAIL_QUERY = "in:inbox newer_than:14d"
MAX_MAILS_POR_SYNC = 50


def _cargar_env() -> None:
    """Lee el archivo .env (lineas CLAVE=valor) y lo pasa a variables de entorno."""
    ruta = RAIZ / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()

# --- Nube ---
# Si existe, la app usa esa base Postgres en vez del archivo local crm.db.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# En un servidor no hay archivo token.json: su contenido va en esta variable.
GMAIL_TOKEN_JSON = os.environ.get("GMAIL_TOKEN_JSON", "")
# Clave que usa la tarea programada para pedirle al servidor que revise el mail.
TAREA_TOKEN = os.environ.get("TAREA_TOKEN", "")

# --- Panel web ---
# Con 127.0.0.1 el panel solo se ve desde esta PC. En un servidor se usa 0.0.0.0
# (visible desde internet) y ahi la contraseña es obligatoria.
PANEL_HOST = os.environ.get("PANEL_HOST", "127.0.0.1")
PANEL_PUERTO = int(os.environ.get("PORT", "8000"))
# Se crean con: python -m mail_crm.clave   (nunca se guarda la contraseña en si)
PANEL_CLAVE_HASH = os.environ.get("PANEL_CLAVE_HASH", "")
PANEL_SECRETO = os.environ.get("PANEL_SECRETO", "")

# --- IA (Gemini) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Si este modelo no existe o no tiene cuota gratis, corre
# `python -m mail_crm.clasificador` para ver los disponibles y cambialo en .env
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
# Si el modelo principal esta saturado o sin cuota, se prueban estos en orden.
GEMINI_MODELOS_RESPALDO = ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite", "gemini-flash-latest"]
# Segundos de espera antes de cada reintento cuando Gemini esta saturado.
ESPERAS_REINTENTO = [4, 12]

# La capa gratuita permite pocos pedidos por dia, asi que mandamos varios
# mails juntos en cada pedido.
MAILS_POR_PEDIDO = 10
MAX_MAILS_POR_CLASIFICACION = 50
CUERPO_PARA_IA = 1_500  # caracteres del cuerpo que ve la IA

# La IA solo elige la CATEGORIA. La accion la decide esta tabla, no la IA:
# asi un mail malicioso no puede "convencerla" de hacer algo raro.
ACCION_POR_CATEGORIA = {
    "spam": "papelera",
    "promocion": "archivar",
    "notificacion": "archivar",
    "atencion": "dejar",
    "personal": "dejar",
    "importante": "destacar",
    "requiere_respuesta": "borrador",
}
# Lo que la IA sabe de vos para decidir que te importa. Editalo a gusto.
PERFIL_USUARIO = (
    "El usuario esta buscando trabajo como desarrollador de software: las alertas y "
    "ofertas de empleo concretas le interesan y van como 'importante'. Las sugerencias "
    "de contactos y la actividad de redes sociales no le interesan: son 'notificacion'."
)

# --- Borradores de respuesta ---
# La IA redacta el texto; la app lo guarda en Gmail como borrador y NUNCA lo envia.
CUERPO_PARA_BORRADOR = 4_000  # caracteres del mail recibido que ve la IA al redactar
MAX_BORRADOR = 10_000         # largo maximo del borrador (caracteres)
MAX_INDICACIONES = 500        # largo maximo de lo que le pedis a la IA
# Nombre con el que se firman los borradores. Se puede cambiar en .env
FIRMA_BORRADOR = os.environ.get("FIRMA_BORRADOR", "Alesandro")

# Si la IA no esta bastante segura, no se hace nada con ese mail.
CONFIANZA_MINIMA = 0.8

# --- Datos guardados ---
# Del cuerpo de cada mail se guarda solo lo que la IA llega a leer: guardar mas
# solo agrandaria lo que se expone si alguien entrara a la base.
MAX_CUERPO = max(CUERPO_PARA_IA, CUERPO_PARA_BORRADOR)
# Los mails (y sus decisiones y borradores) se borran de la base pasados estos dias.
# Los contactos, sus notas y tus reglas se conservan siempre.
DIAS_DE_RETENCION = int(os.environ.get("DIAS_DE_RETENCION", "60"))

# En tu PC la consola muestra remitente y asunto de cada mail. En el servidor no:
# sus registros los guarda el hosting y no hace falta que tengan datos personales.
DETALLE_EN_CONSOLA = PANEL_HOST == "127.0.0.1"

# --- Hora local (Argentina no cambia de horario en verano) ---
HUSO_HORARIO = -3
# A partir de esta hora, la primera actualizacion del dia arma el resumen diario.
HORA_RESUMEN = 8

# --- Seguimientos ---
# Mails que mandaste y que vigila el panel para avisarte si no te contestan.
GMAIL_QUERY_ENVIADOS = "in:sent newer_than:14d"
DIAS_SIN_RESPUESTA = 3  # pasados estos dias sin respuesta, aparece en Seguimientos

# --- Postulaciones ---
ETAPAS_POSTULACION = {
    "sugerida": "Sugerida por la IA",
    "interesa": "Me interesa",
    "postulado": "Me postulé",
    "entrevista": "Entrevista",
    "oferta": "Oferta",
    "descartado": "Descartado",
}

# --- Etiquetas en Gmail (si las activas en Ajustes) ---
# Cada mail clasificado por la IA recibe la etiqueta de su categoria.
ETIQUETA_POR_CATEGORIA = {
    "spam": "CRM/Spam",
    "promocion": "CRM/Promociones",
    "notificacion": "CRM/Notificaciones",
    "atencion": "CRM/Atención",
    "personal": "CRM/Personal",
    "importante": "CRM/Importante",
    "requiere_respuesta": "CRM/Para responder",
}

# --- Avisos al celular ---
# Despues de cada actualizacion llega un aviso si aparecieron mails de estas categorias.
CATEGORIAS_CON_AVISO = {"requiere_respuesta": "para responder", "atencion": "que tenés que ver"}
# Identifica al que manda los avisos ante Google, Apple y Mozilla (Render la define sola).
URL_PANEL = os.environ.get("RENDER_EXTERNAL_URL", "https://localhost")
