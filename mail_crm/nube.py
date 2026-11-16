"""Prepara las variables que hay que cargar en el servidor (Render).

Uso:  python -m mail_crm.nube

Crea el archivo nube.env con todo lo que el servidor necesita, listo para pegar
en Render (Environment -> Add from .env). Ese archivo tiene secretos: no lo
compartas ni lo subas a GitHub (ya esta en .gitignore). Borralo cuando termines.
"""
import secrets

from . import config
from .clave import guardar_en_env

SALIDA = config.RAIZ / "nube.env"


def main() -> None:
    faltan = []
    if not config.GEMINI_API_KEY:
        faltan.append("GEMINI_API_KEY en .env")
    if not (config.PANEL_CLAVE_HASH and config.PANEL_SECRETO):
        faltan.append("la contraseña del panel (corre: python -m mail_crm.clave)")
    if not config.DATABASE_URL:
        faltan.append("DATABASE_URL en .env (la direccion de tu base en Supabase)")
    if not config.TOKEN_PATH.exists():
        faltan.append("token.json (corre: python -m mail_crm.sync)")
    if faltan:
        raise SystemExit("Antes de seguir falta:\n  - " + "\n  - ".join(faltan))

    tarea_token = config.TAREA_TOKEN
    if not tarea_token:
        tarea_token = secrets.token_urlsafe(32)
        guardar_en_env({"TAREA_TOKEN": tarea_token})

    variables = {
        "PANEL_HOST": "0.0.0.0",
        "DATABASE_URL": config.DATABASE_URL,
        "GEMINI_API_KEY": config.GEMINI_API_KEY,
        "GEMINI_MODEL": config.GEMINI_MODEL,
        "PANEL_CLAVE_HASH": config.PANEL_CLAVE_HASH,
        "PANEL_SECRETO": config.PANEL_SECRETO,
        "TAREA_TOKEN": tarea_token,
        "GMAIL_TOKEN_JSON": config.TOKEN_PATH.read_text(encoding="utf-8").strip(),
    }
    SALIDA.write_text("".join(f"{k}={v}\n" for k, v in variables.items()), encoding="utf-8")
    print(f"Listo: se creo {SALIDA.name} con {len(variables)} variables.")
    print("Abrilo, copia todo y pegalo en Render (Environment -> Add from .env).")
    print("Tiene secretos: no lo compartas, y borralo cuando termines.")


if __name__ == "__main__":
    main()
