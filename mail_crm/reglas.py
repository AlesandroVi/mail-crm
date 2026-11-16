"""Reglas por remitente: lo que vos decidis manda sobre la IA.

Uso:
  python -m mail_crm.reglas                          -> lista las reglas
  python -m mail_crm.reglas agregar PATRON ACCION [nota]
  python -m mail_crm.reglas borrar PATRON

PATRON es un mail exacto (promo@tienda.com) o un dominio (tienda.com).
ACCION es una de: papelera, archivar, dejar, destacar
"""
import sys

from . import db

ACCIONES_DE_REGLA = ["papelera", "archivar", "dejar", "destacar"]


def main(args: list[str]) -> None:
    conn = db.conectar()
    if not args:
        reglas = db.listar_reglas(conn)
        if not reglas:
            print("No hay reglas todavia.")
        for r in reglas:
            print(f"  {r['pattern']:<40} -> {r['action']:<9} {r['note'] or ''}")
    elif args[0] == "agregar" and len(args) >= 3:
        patron, accion, nota = args[1], args[2], " ".join(args[3:])
        if accion not in ACCIONES_DE_REGLA:
            raise SystemExit(f"Accion invalida. Usa una de: {', '.join(ACCIONES_DE_REGLA)}")
        db.guardar_regla(conn, patron, accion, nota)
        print(f"Regla guardada: {patron.lower()} -> {accion}")
    elif args[0] == "borrar" and len(args) == 2:
        print("Regla borrada." if db.borrar_regla(conn, args[1]) else "No existia esa regla.")
    else:
        print(__doc__)
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
