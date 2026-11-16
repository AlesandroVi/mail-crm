"""Clasifica los mails guardados que todavia no tienen decision.

MODO SIMULACION: solo anota en la base lo que HARIA con cada mail.
No toca nada en Gmail.

Uso:
  python -m mail_crm.clasificar             -> clasifica los pendientes
  python -m mail_crm.clasificar --rehacer   -> borra las simulaciones y clasifica todo de nuevo

Orden de decision para cada mail:
  1. Si el remitente tiene una regla tuya, se aplica la regla (no se consulta a la IA).
  2. Si no, la IA elige la categoria y config.ACCION_POR_CATEGORIA define la accion.
"""
import sys

from . import clasificador, config, db


def decidir_accion(categoria: str, confianza: float) -> str:
    """Traduce la categoria que eligio la IA a una accion concreta."""
    if confianza < config.CONFIANZA_MINIMA:
        return "ninguna"  # ante la duda, no se toca el mail
    return config.ACCION_POR_CATEGORIA[categoria]


def _mostrar(categoria: str, accion: str, mail: dict, motivo: str) -> None:
    print(f"  {categoria:<19} -> {accion:<9} {mail['from_email'][:30]:<30} {(mail['subject'] or '')[:40]}")
    print(f"  {'':<19}    {motivo}")


def clasificar_pendientes(rehacer: bool = False) -> dict:
    """Clasifica lo pendiente. Devuelve {"clasificados", "pendientes", "aviso"}."""
    conn = db.conectar()
    try:
        return _clasificar(conn, rehacer)
    finally:
        conn.close()


def _clasificar(conn, rehacer: bool) -> dict:
    if rehacer:
        print(f"Se borraron {db.borrar_decisiones_simuladas(conn)} decisiones de simulacion.\n")

    pendientes = [dict(f) for f in db.mails_sin_decision(conn, config.MAX_MAILS_POR_CLASIFICACION)]
    if not pendientes:
        print("No hay mails pendientes de clasificar. Corre primero: python -m mail_crm.sync")
        return {"clasificados": 0, "pendientes": 0, "aviso": None}
    aviso = None

    # 1) Reglas del usuario: no gastan cuota de IA y siempre dan el mismo resultado.
    reglas = db.listar_reglas(conn)
    por_regla = 0
    para_ia = []
    for mail in pendientes:
        regla = db.buscar_regla(reglas, mail["from_email"])
        if regla is None:
            para_ia.append(mail)
            continue
        motivo = f"Regla tuya para {regla['pattern']}"
        db.guardar_decision(conn, mail["id"], "regla", regla["action"], motivo, "regla")
        _mostrar("regla", regla["action"], mail, motivo)
        por_regla += 1

    # 2) El resto lo decide la IA, de a varios mails por pedido.
    por_ia = 0
    for i in range(0, len(para_ia), config.MAILS_POR_PEDIDO):
        lote = para_ia[i : i + config.MAILS_POR_PEDIDO]
        try:
            resultados = clasificador.clasificar_lote(lote)
        except clasificador.CuotaAgotada:
            aviso = "Se agotó la cuota gratuita de Gemini por ahora. Los mails que faltan quedan pendientes."
        except clasificador.NoDisponible:
            aviso = "Gemini está saturado en este momento. Los mails que faltan quedan pendientes."
        except clasificador.ErrorIA as e:
            aviso = f"Error al clasificar: {e}"
        if aviso:
            print(f"\n{aviso}")
            break

        for mail in lote:
            r = resultados.get(mail["id"])
            if r is None:
                continue  # el modelo lo salteo: queda pendiente para la proxima
            accion = decidir_accion(r["categoria"], r["confianza"])
            motivo = f"[{r['confianza']:.0%}] {r['motivo']}"
            db.guardar_decision(conn, mail["id"], r["categoria"], accion, motivo, clasificador.modelo_usado)
            _mostrar(r["categoria"], accion, mail, motivo)
            por_ia += 1

    print(
        f"\n{por_regla + por_ia} de {len(pendientes)} mails clasificados "
        f"({por_regla} por reglas tuyas, {por_ia} por la IA). No se toco Gmail."
    )
    hechos = por_regla + por_ia
    return {"clasificados": hechos, "pendientes": len(pendientes) - hechos, "aviso": aviso}


if __name__ == "__main__":
    clasificar_pendientes(rehacer="--rehacer" in sys.argv[1:])
