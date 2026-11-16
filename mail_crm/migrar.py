"""Copia tus datos del archivo local (crm.db) a la base Postgres de la nube.

Uso:  python -m mail_crm.migrar

Necesita DATABASE_URL en .env. Se corre una sola vez: si la base de la nube ya
tiene datos, no hace nada (para no duplicar).
"""
from . import config, db

# En orden: una tabla solo se copia despues de las tablas a las que apunta.
TABLAS = {
    "contacts": ["id", "email", "name", "first_seen", "last_seen", "email_count"],
    "emails": ["id", "gmail_id", "thread_id", "contact_id", "subject", "snippet", "body",
               "received_at", "labels", "is_unread"],
    "decisions": ["id", "email_id", "category", "action", "reason", "model", "dry_run", "applied", "created_at"],
    "rules": ["id", "pattern", "action", "note", "created_at"],
}


def migrar() -> None:
    if not config.DATABASE_URL:
        raise SystemExit("Falta DATABASE_URL en .env (la direccion de tu base en Supabase).")
    if not config.DB_PATH.exists():
        raise SystemExit(f"No encuentro {config.DB_PATH.name}: no hay datos locales para copiar.")

    local = db.conectar(config.DB_PATH)
    nube = db.conectar()  # con DATABASE_URL definido, esto es Postgres
    try:
        ya_hay = sum(nube.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in TABLAS)
        if ya_hay:
            raise SystemExit("La base de la nube ya tiene datos. No copio nada para no duplicar.")

        with nube:  # una sola transaccion: si algo falla, no queda nada a medias
            for tabla, columnas in TABLAS.items():
                filas = local.execute(f"SELECT {', '.join(columnas)} FROM {tabla} ORDER BY id").fetchall()
                marcas = ", ".join("?" * len(columnas))
                for f in filas:
                    nube.execute(f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES ({marcas})", tuple(f))
                # Los ids se copiaron tal cual: avisarle al contador desde donde seguir.
                nube.execute(
                    f"SELECT setval(pg_get_serial_sequence('{tabla}', 'id'), "
                    f"(SELECT COALESCE(MAX(id), 0) + 1 FROM {tabla}), false)"
                )
                print(f"  {tabla:<10} {len(filas)} filas copiadas")
        print("\nListo: tus datos ya estan en la nube.")
    finally:
        local.close()
        nube.close()


if __name__ == "__main__":
    migrar()
