"""Seguimientos: mails que mandaste y que todavia no te contestaron.

En cada actualizacion:
  1. Se guardan los mails nuevos de tu carpeta Enviados (solo remitente, asunto y fecha).
  2. Para cada uno que sigue sin respuesta, se mira su conversacion: si alguien
     que no sos vos escribio despues, queda como respondido.
Los que pasan config.DIAS_SIN_RESPUESTA dias sin respuesta aparecen en el panel.
No se lee el cuerpo de ningun mail.
"""
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

from . import config, db, gmail_client

# Direcciones a las que no tiene sentido esperar respuesta.
NO_ESPERAR = ("noreply", "no-reply", "no_reply", "donotreply", "mailer-daemon", "notifications", "notificaciones")


def mi_direccion(servicio, conn) -> str:
    guardada = db.leer_ajuste(conn, "mi_email")
    if guardada:
        return guardada
    direccion = servicio.users().getProfile(userId="me").execute()["emailAddress"].lower()
    db.guardar_ajuste(conn, "mi_email", direccion)
    return direccion


def _cabeceras(mensaje: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in mensaje.get("payload", {}).get("headers", [])}


def _fecha(mensaje: dict) -> str:
    return datetime.fromtimestamp(int(mensaje.get("internalDate", 0)) / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def guardar_enviados(servicio, conn, yo: str) -> int:
    """Guarda los mails enviados que todavia no conocia. Devuelve cuantos guardo."""
    nuevos = 0
    for gmail_id in gmail_client.listar_ids(servicio, config.GMAIL_QUERY_ENVIADOS, config.MAX_MAILS_POR_SYNC):
        if db.seguimiento_guardado(conn, gmail_id):
            continue
        m = servicio.users().messages().get(
            userId="me", id=gmail_id, format="metadata", metadataHeaders=["To", "Subject"]
        ).execute()
        c = _cabeceras(m)
        destinatarios = [(n, d.lower()) for n, d in getaddresses([c.get("to", "")]) if d]
        otros = [(n, d) for n, d in destinatarios if d != yo]
        nombre, direccion = otros[0] if otros else ("", "")
        # Mails a vos mismo o a direcciones automaticas: se guardan como "no espero respuesta".
        sin_espera = not direccion or any(x in direccion.split("@")[0] for x in NO_ESPERAR)
        db.guardar_seguimiento(conn, {
            "gmail_id": gmail_id, "thread_id": m.get("threadId"), "to_email": direccion, "to_name": nombre,
            "subject": " ".join(c.get("subject", "(sin asunto)").split())[:300], "sent_at": _fecha(m),
            "answered": 1 if sin_espera else 0,
        })
        nuevos += 1
    return nuevos


def revisar_respuestas(servicio, conn, yo: str) -> int:
    """Marca como respondidos los mails cuya conversacion tiene un mensaje posterior de otra persona."""
    respondidos = 0
    for s in db.seguimientos_abiertos(conn):
        if not s["thread_id"]:
            continue
        hilo = servicio.users().threads().get(
            userId="me", id=s["thread_id"], format="metadata", metadataHeaders=["From"]
        ).execute()
        for m in hilo.get("messages", []):
            de = parseaddr(_cabeceras(m).get("from", ""))[1].lower()
            if _fecha(m) > s["sent_at"] and de and de != yo:
                db.marcar_respondido(conn, s["id"])
                respondidos += 1
                break
    return respondidos


def actualizar(servicio, conn) -> dict:
    yo = mi_direccion(servicio, conn)
    return {"enviados_nuevos": guardar_enviados(servicio, conn, yo), "respondidos": revisar_respuestas(servicio, conn, yo)}
