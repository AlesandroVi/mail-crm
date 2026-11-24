"""Sincroniza Gmail -> base de datos.

Uso:  python -m mail_crm.sync
"""
from . import config, db, gmail_client


def sincronizar(interactivo: bool = True) -> dict:
    """Trae los mails nuevos y los guarda. Devuelve cuantos reviso y cuantos eran nuevos.

    Con interactivo=False (servidor) nunca abre el navegador para iniciar sesion.
    """
    servicio = gmail_client.obtener_servicio(interactivo=interactivo)
    conn = db.conectar()
    try:
        ids = gmail_client.listar_ids(servicio, config.GMAIL_QUERY, config.MAX_MAILS_POR_SYNC)
        nuevos = 0
        for gmail_id in ids:
            if db.mail_ya_guardado(conn, gmail_id):
                continue  # no volvemos a descargar lo que ya tenemos
            mail = gmail_client.traer_mail(servicio, gmail_id)
            if db.guardar_mail(conn, mail):
                nuevos += 1
                if config.DETALLE_EN_CONSOLA:
                    print(f"  + {mail['from_email']:<40} {mail['subject'][:60]}")

        print(f"\n{len(ids)} mails revisados, {nuevos} nuevos guardados.")
        if interactivo:
            print("\nContactos que mas te escriben:")
            for c in db.top_contactos(conn):
                print(f"  {c['email_count']:>3}  {c['email']}  ({c['name'] or 'sin nombre'})")
        return {"revisados": len(ids), "nuevos": nuevos}
    finally:
        conn.close()


if __name__ == "__main__":
    sincronizar()
