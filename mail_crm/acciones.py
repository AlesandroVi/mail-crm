"""Aplica (y deshace) en Gmail las decisiones guardadas.

Todas las acciones son reversibles:
  papelera -> mueve a la papelera (Gmail la guarda 30 dias). Deshacer: la saca de ahi.
  archivar -> quita el mail de la bandeja de entrada.        Deshacer: lo devuelve.
  destacar -> le pone la estrella.                           Deshacer: se la saca.
Las demas acciones (dejar, ninguna, borrador) no cambian nada en Gmail.
"""
from googleapiclient.errors import HttpError

from . import db

ACCIONES_CON_EFECTO = ("papelera", "archivar", "destacar")


def _ejecutar(servicio, gmail_id: str, accion: str, deshacer: bool = False) -> None:
    mensajes = servicio.users().messages()
    if accion == "papelera":
        metodo = mensajes.untrash if deshacer else mensajes.trash
        metodo(userId="me", id=gmail_id).execute()
    elif accion == "archivar":
        cuerpo = {"addLabelIds": ["INBOX"]} if deshacer else {"removeLabelIds": ["INBOX"]}
        mensajes.modify(userId="me", id=gmail_id, body=cuerpo).execute()
    elif accion == "destacar":
        cuerpo = {"removeLabelIds": ["STARRED"]} if deshacer else {"addLabelIds": ["STARRED"]}
        mensajes.modify(userId="me", id=gmail_id, body=cuerpo).execute()
    else:
        raise ValueError(f"La accion '{accion}' no cambia nada en Gmail")


def explicar(error: HttpError) -> str:
    codigo = getattr(error.resp, "status", None)
    if codigo == 404:
        return "el mail ya no existe en Gmail"
    if codigo == 403:
        return "Gmail rechazo el permiso"
    return f"error {codigo} de Gmail"


def aplicar_decisiones(conn, servicio, ids: list[int] | None = None) -> dict:
    """Aplica las decisiones pendientes (todas, o solo las de `ids`)."""
    aplicadas, errores = 0, []
    for d in db.decisiones_aplicables(conn, ACCIONES_CON_EFECTO, ids):
        try:
            _ejecutar(servicio, d["gmail_id"], d["action"])
        except HttpError as e:
            errores.append(f"{d['subject'] or '(sin asunto)'}: {explicar(e)}")
            continue
        db.marcar_aplicada(conn, d["id"], True)
        aplicadas += 1
    return {"aplicadas": aplicadas, "errores": errores}


def aplicar_reglas(conn, servicio) -> dict:
    """Aplica solas las decisiones que vienen de TUS reglas (nunca las de la IA).

    Saltea las que deshiciste a mano, para no volver a hacer algo que revertiste.
    """
    ids = [d["id"] for d in db.decisiones_aplicables(conn, ACCIONES_CON_EFECTO)
           if d["model"] == "regla" and not d["undone"]]
    if not ids:
        return {"aplicadas": 0, "errores": []}
    return aplicar_decisiones(conn, servicio, ids)


def deshacer_decision(conn, servicio, decision_id: int) -> None:
    """Revierte en Gmail una decision ya aplicada."""
    d = db.decision_por_id(conn, decision_id)
    if d is None or not d["applied"]:
        raise LookupError("Esa decision no esta aplicada.")
    try:
        _ejecutar(servicio, d["gmail_id"], d["action"], deshacer=True)
    except HttpError as e:
        raise RuntimeError(f"No se pudo deshacer: {explicar(e)}.") from e
    db.marcar_aplicada(conn, decision_id, False)
