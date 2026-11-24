"""Avisos al celular (Web Push).

Cuando la actualizacion encuentra mails para responder o que tenes que ver,
manda un aviso a cada dispositivo donde los activaste desde el panel.

- El aviso NO dice quien te escribio ni el asunto: solo cuantos mails hay.
  Asi no aparece informacion tuya en la pantalla bloqueada.
- Las claves para firmar los avisos (VAPID) se crean solas la primera vez y se
  guardan en la base; no hay que configurar nada en Render.
- Solo se mandan avisos a los servicios de push de los navegadores (Google,
  Apple, Mozilla, Microsoft), nunca a una direccion cualquiera.
"""
import base64
import json
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from . import config, db

SERVICIOS_DE_PUSH = (
    "fcm.googleapis.com",          # Chrome, Edge en Android, la mayoria de los navegadores
    "android.googleapis.com",
    ".push.services.mozilla.com",  # Firefox
    ".push.apple.com",             # Safari y iPhone (app instalada en la pantalla de inicio)
    ".notify.windows.com",         # Edge en Windows
)
LARGO_MAXIMO = 1000


def endpoint_valido(endpoint: str) -> bool:
    """True si la direccion es de un servicio de push conocido (https, sin usuario ni puerto raro)."""
    if not isinstance(endpoint, str) or len(endpoint) > LARGO_MAXIMO:
        return False
    partes = urlsplit(endpoint)
    host = (partes.hostname or "").lower()
    if partes.scheme != "https" or partes.username or partes.password or partes.port not in (None, 443):
        return False
    return any(host == s if not s.startswith(".") else host.endswith(s) for s in SERVICIOS_DE_PUSH)


def suscripcion_valida(datos) -> dict | None:
    """Devuelve la suscripcion limpia (solo los campos que hacen falta) o None si no sirve."""
    if not isinstance(datos, dict):
        return None
    claves = datos.get("keys")
    if not endpoint_valido(datos.get("endpoint")) or not isinstance(claves, dict):
        return None
    p256dh, auth = claves.get("p256dh"), claves.get("auth")
    if not all(isinstance(x, str) and 0 < len(x) <= 200 for x in (p256dh, auth)):
        return None
    return {"endpoint": datos["endpoint"], "keys": {"p256dh": p256dh, "auth": auth}}


def _claves(conn) -> Vapid:
    """Las claves que firman los avisos. Se crean la primera vez que hacen falta."""
    pem = db.leer_ajuste(conn, "vapid_privada")
    if not pem:
        nueva = Vapid()
        nueva.generate_keys()
        with conn:  # si dos pedidos llegan juntos, gana el primero y el otro usa esa
            conn.execute(
                "INSERT INTO settings (name, value) VALUES (?, ?) ON CONFLICT (name) DO NOTHING",
                ("vapid_privada", nueva.private_pem().decode()),
            )
        pem = db.leer_ajuste(conn, "vapid_privada")
    return Vapid.from_pem(pem.encode())


def clave_publica(conn) -> str:
    """La clave publica que el navegador necesita para suscribirse."""
    crudo = _claves(conn).public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(crudo).decode().rstrip("=")


def texto_de_aviso(por_categoria: dict[str, int]) -> str | None:
    """Ej.: "2 mails para responder y 1 que tenés que ver". None si no hay nada para avisar."""
    partes = []
    for categoria, frase in config.CATEGORIAS_CON_AVISO.items():
        n = por_categoria.get(categoria, 0)
        if n:
            partes.append(f"{n} {'mail' if n == 1 else 'mails'} {frase}")
    if not partes:
        return None
    return " y ".join([", ".join(partes[:-1]), partes[-1]] if len(partes) > 1 else partes)


def enviar(conn, titulo: str, texto: str) -> int:
    """Manda el aviso a todos los dispositivos suscriptos. Devuelve a cuantos llego."""
    suscripciones = db.listar_suscripciones(conn)
    if not suscripciones:
        return 0
    claves = _claves(conn)
    mensaje = json.dumps({"titulo": titulo, "texto": texto}, ensure_ascii=False)
    enviados = 0
    for s in suscripciones:
        if not endpoint_valido(s["endpoint"]):
            db.borrar_suscripcion(conn, s["endpoint"])
            continue
        try:
            webpush(
                json.loads(s["data"]),
                mensaje,
                vapid_private_key=claves,
                vapid_claims={"sub": config.URL_PANEL},  # webpush le agrega datos: va uno nuevo cada vez
                timeout=10,
                ttl=24 * 3600,  # si el celular esta apagado, el aviso espera hasta un dia
            )
            enviados += 1
        except WebPushException as e:
            codigo = getattr(e.response, "status_code", None)
            if codigo in (404, 410):  # ese dispositivo ya no recibe avisos (los desactivaste o desinstalaste)
                db.borrar_suscripcion(conn, s["endpoint"])
            else:
                print(f"[avisos] no se pudo enviar un aviso (error {codigo})")
        except Exception as e:  # sin conexion, etc.: no tiene que frenar la actualizacion
            print(f"[avisos] no se pudo enviar un aviso ({type(e).__name__})")
    return enviados
