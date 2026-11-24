"""Numeros para la pestaña Estadisticas (calculados con los mails que guarda la base)."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import config, db

SEMANAS = 8


def calcular(conn) -> dict:
    mails = db.mails_desde(conn, db.hace_dias(SEMANAS * 7))
    hoy = datetime.now(timezone.utc).date()
    inicio = hoy - timedelta(days=hoy.weekday())  # lunes de esta semana
    lunes = [inicio - timedelta(weeks=i) for i in range(SEMANAS - 1, -1, -1)]
    por_semana = Counter()
    for m in mails:
        try:
            dia = datetime.fromisoformat(m["received_at"]).date()
        except ValueError:
            continue
        por_semana[(dia - timedelta(days=dia.weekday())).isoformat()] += 1

    por_categoria = Counter(m["category"] or "pendiente" for m in mails)
    remitentes = Counter(m["from_email"] for m in mails)
    nombres = {m["from_email"]: m["from_name"] for m in mails}
    postulaciones = Counter(p["stage"] for p in db.listar_postulaciones(conn))
    return {
        "semanas": [{"desde": d.isoformat(), "mails": por_semana.get(d.isoformat(), 0)} for d in lunes],
        "por_categoria": dict(por_categoria.most_common()),
        "remitentes": [{"email": e, "nombre": nombres[e], "mails": n} for e, n in remitentes.most_common(8)],
        "postulaciones": {etapa: postulaciones.get(etapa, 0) for etapa in config.ETAPAS_POSTULACION},
        "total": len(mails),
        "dias_de_retencion": config.DIAS_DE_RETENCION,
    }
