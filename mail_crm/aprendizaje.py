"""Sugerencias de reglas a partir de lo que haces en el panel.

Tres señales, siempre por remitente:
  - Corregiste a la IA: cambiaste a mano su decision 2 o mas veces, y elegiste lo mismo.
  - Confias en la IA: aplicaste 3 o mas veces la misma accion y no la deshiciste.
    Una regla ahorra cuota de IA y siempre decide igual.
  - La IA se equivoca: deshiciste 2 o mas acciones suyas -> sugerencia de "dejar".
Nunca se crea una regla sola: solo se sugiere y vos decidis.
"""
import json
from collections import Counter, defaultdict

from . import db
from .reglas import ACCIONES_DE_REGLA

MIN_CORRECCIONES, MIN_CONFIANZA, MIN_DESHECHAS = 2, 3, 2


def _ignoradas(conn) -> set[str]:
    try:
        return set(json.loads(db.leer_ajuste(conn, "sugerencias_ignoradas", "[]")))
    except ValueError:
        return set()


def ignorar(conn, patron: str) -> None:
    lista = _ignoradas(conn) | {patron.strip().lower()}
    db.guardar_ajuste(conn, "sugerencias_ignoradas", json.dumps(sorted(lista)[-500:]))


def sugerencias(conn) -> list[dict]:
    reglas = db.listar_reglas(conn)
    ignoradas = _ignoradas(conn)
    por_mail = defaultdict(list)
    for d in db.historial_de_decisiones(conn):
        por_mail[d["email_id"]].append(d)

    correcciones, confianza, deshechas = defaultdict(Counter), defaultdict(Counter), Counter()
    for decisiones in por_mail.values():
        remitente = decisiones[-1]["email"]
        ultima = decisiones[-1]
        hubo_ia = any(d["model"] not in ("regla", "manual") for d in decisiones[:-1])
        if ultima["model"] == "manual" and hubo_ia:
            correcciones[remitente][ultima["action"]] += 1
        elif ultima["model"] not in ("regla", "manual") and ultima["applied"] and not ultima["undone"]:
            confianza[remitente][ultima["action"]] += 1
        deshechas[remitente] += sum(1 for d in decisiones if d["undone"] and d["model"] not in ("regla", "manual"))

    salida, vistos = [], set()

    def agregar(remitente, accion, motivo):
        if remitente in vistos or remitente in ignoradas or accion not in ACCIONES_DE_REGLA:
            return
        if db.buscar_regla(reglas, remitente) is not None:
            return
        vistos.add(remitente)
        salida.append({"pattern": remitente, "action": accion, "motivo": motivo})

    for remitente, cuenta in correcciones.items():
        accion, n = cuenta.most_common(1)[0]
        if n >= MIN_CORRECCIONES:
            agregar(remitente, accion, f"Cambiaste {n} veces lo que decidió la IA con sus mails.")
    for remitente, n in deshechas.items():
        if n >= MIN_DESHECHAS:
            agregar(remitente, "dejar", f"Deshiciste {n} acciones de la IA sobre sus mails.")
    for remitente, cuenta in confianza.items():
        accion, n = cuenta.most_common(1)[0]
        if n >= MIN_CONFIANZA:
            agregar(remitente, accion, f"Aplicaste {n} veces la misma acción: con una regla ya no gasta IA.")
    return salida
