"""Etiquetas de Gmail segun la categoria que eligio la IA (si las activas en Ajustes).

Ponerle una etiqueta a un mail no lo mueve ni lo borra: solo lo deja ordenado
tambien en Gmail (por ejemplo, "CRM/Para responder"). Se pueden quitar desde Gmail.
"""
import json

from googleapiclient.errors import HttpError

from . import config, db


def _ids_de_etiquetas(servicio, conn) -> dict[str, str]:
    """Nombre de etiqueta -> id en Gmail. Crea las que falten (una sola vez)."""
    try:
        guardadas = json.loads(db.leer_ajuste(conn, "etiquetas_gmail", "{}"))
    except ValueError:
        guardadas = {}
    faltan = [n for n in config.ETIQUETA_POR_CATEGORIA.values() if n not in guardadas]
    if faltan:
        existentes = {l["name"]: l["id"] for l in servicio.users().labels().list(userId="me").execute().get("labels", [])}
        for nombre in faltan:
            if nombre not in existentes:
                creada = servicio.users().labels().create(
                    userId="me", body={"name": nombre, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
                ).execute()
                existentes[nombre] = creada["id"]
            guardadas[nombre] = existentes[nombre]
        db.guardar_ajuste(conn, "etiquetas_gmail", json.dumps(guardadas))
    return guardadas


def etiquetar(servicio, conn) -> int:
    """Le pone su etiqueta a cada mail clasificado por la IA que todavia no la tiene. Devuelve cuantos."""
    pendientes = db.mails_para_etiquetar(conn, list(config.ETIQUETA_POR_CATEGORIA))
    if not pendientes:
        return 0
    ids = _ids_de_etiquetas(servicio, conn)
    hechos = 0
    for m in pendientes:
        nombre = config.ETIQUETA_POR_CATEGORIA[m["category"]]
        try:
            servicio.users().messages().modify(userId="me", id=m["gmail_id"], body={"addLabelIds": [ids[nombre]]}).execute()
        except HttpError as e:
            if getattr(e.resp, "status", None) == 404:  # el mail ya no existe en Gmail
                db.marcar_etiquetado(conn, m["id"], "-")
                continue
            # Probablemente borraste la etiqueta en Gmail: la proxima vez se vuelve a crear.
            db.guardar_ajuste(conn, "etiquetas_gmail", "{}")
            raise
        db.marcar_etiquetado(conn, m["id"], nombre)
        hechos += 1
    return hechos
