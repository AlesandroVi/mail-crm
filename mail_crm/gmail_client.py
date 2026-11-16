"""Conexion con Gmail: inicio de sesion (OAuth) y lectura de mails."""
import base64
import html
import json
import re
from datetime import datetime, timezone
from email.utils import parseaddr

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from . import config


class FaltaAutorizacion(Exception):
    """Hay que iniciar sesion en Google (primera vez, permiso nuevo o sesion vencida)."""


def _credenciales_guardadas():
    """Lee la sesion de Google guardada, si tiene los permisos que la app necesita hoy.

    En tu PC esta en token.json; en un servidor, en la variable GMAIL_TOKEN_JSON.
    """
    try:
        if config.GMAIL_TOKEN_JSON:
            datos = json.loads(config.GMAIL_TOKEN_JSON)
        elif config.TOKEN_PATH.exists():
            datos = json.loads(config.TOKEN_PATH.read_text(encoding="utf-8"))
        else:
            return None
    except (ValueError, OSError):
        return None
    if not isinstance(datos, dict) or not set(config.GMAIL_SCOPES) <= set(datos.get("scopes", [])):
        return None  # el token es de cuando la app pedia otros permisos
    return Credentials.from_authorized_user_info(datos, config.GMAIL_SCOPES)


def obtener_servicio(interactivo: bool = True):
    """Devuelve el cliente de la API de Gmail, iniciando sesion si hace falta.

    La primera vez abre el navegador para que autorices la app. Despues
    reutiliza token.json y no vuelve a preguntar.
    Con interactivo=False nunca abre el navegador: si hace falta iniciar
    sesion, lanza FaltaAutorizacion (lo usa el panel web).
    """
    creds = _credenciales_guardadas()

    if creds and not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None  # la sesion vencio (en modo prueba dura 7 dias)

    if not creds or not creds.valid:
        if not interactivo:
            raise FaltaAutorizacion()
        if not config.CREDENTIALS_PATH.exists():
            raise SystemExit(
                f"Falta {config.CREDENTIALS_PATH.name}. Segui los pasos del README "
                "para descargarlo de Google Cloud y ponelo en la carpeta del proyecto."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(config.CREDENTIALS_PATH), config.GMAIL_SCOPES
        )
        creds = flow.run_local_server(port=0)

    if not config.GMAIL_TOKEN_JSON:  # en un servidor no hay donde guardarlo (ni hace falta)
        config.TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def listar_ids(servicio, query: str, maximo: int) -> list[str]:
    """IDs de los mails que coinciden con la busqueda (los mas nuevos primero)."""
    ids: list[str] = []
    page_token = None
    while len(ids) < maximo:
        resp = (
            servicio.users()
            .messages()
            .list(userId="me", q=query, maxResults=min(100, maximo - len(ids)), pageToken=page_token)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def traer_mail(servicio, gmail_id: str) -> dict:
    """Descarga un mail completo y lo convierte al formato que usa la base."""
    crudo = servicio.users().messages().get(userId="me", id=gmail_id, format="full").execute()
    return parsear_mensaje(crudo)


def parsear_mensaje(crudo: dict) -> dict:
    """Convierte la respuesta de la API de Gmail en un dict simple."""
    payload = crudo.get("payload", {})
    cabeceras = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    nombre, direccion = parseaddr(cabeceras.get("from", ""))
    etiquetas = crudo.get("labelIds", [])
    fecha = datetime.fromtimestamp(int(crudo.get("internalDate", 0)) / 1000, tz=timezone.utc)

    return {
        "gmail_id": crudo["id"],
        "thread_id": crudo.get("threadId"),
        "from_email": direccion or "desconocido",
        "from_name": nombre,
        "subject": cabeceras.get("subject", "(sin asunto)"),
        "snippet": html.unescape(crudo.get("snippet", "")),
        "body": _extraer_cuerpo(payload)[: config.MAX_CUERPO],
        "received_at": fecha.isoformat(timespec="seconds"),
        "labels": etiquetas,
        "is_unread": "UNREAD" in etiquetas,
    }


def _decodificar(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _buscar_parte(payload: dict, mime: str) -> str:
    """Busca recursivamente la primera parte del mail con ese tipo MIME."""
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _decodificar(payload["body"]["data"])
    for parte in payload.get("parts", []) or []:
        texto = _buscar_parte(parte, mime)
        if texto:
            return texto
    return ""


def _extraer_cuerpo(payload: dict) -> str:
    """Texto del mail: prefiere texto plano; si solo hay HTML, le quita las etiquetas."""
    texto = _buscar_parte(payload, "text/plain")
    if texto:
        return texto.strip()
    crudo_html = _buscar_parte(payload, "text/html")
    if not crudo_html:
        return ""
    sin_bloques = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", crudo_html)
    sin_tags = re.sub(r"(?s)<[^>]+>", " ", sin_bloques)
    return re.sub(r"\s+", " ", html.unescape(sin_tags)).strip()
