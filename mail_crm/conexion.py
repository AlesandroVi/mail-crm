"""Guarda en .env la conexion a tu base de Supabase, sin editar archivos a mano.

Uso:  python -m mail_crm.conexion

Te pide la cadena de conexion (la de "Session pooler") y la contraseña de la
base, arma la direccion correcta (los simbolos raros de la contraseña se
codifican solos), prueba que conecte y recien ahi la guarda en .env.
"""
import re
from urllib.parse import quote

from . import db
from .clave import guardar_en_env, leer_clave

# postgresql://USUARIO:CONTRASEÑA@SERVIDOR:PUERTO/BASE   (la contraseña puede traer cualquier cosa)
FORMA = re.compile(r"^postgres(?:ql)?://([^:/@\s]+):(.*)@([^@/\s]+/[^@\s]+)$")


def armar_url(cadena: str, clave: str) -> str:
    """Reemplaza la contraseña de la cadena por `clave`, codificada para que sea valida."""
    partes = FORMA.match(cadena.strip().strip('"').strip("'"))
    if not partes:
        raise ValueError("Esa no parece la cadena de Supabase. Tiene que empezar con postgresql://")
    usuario, _, resto = partes.groups()
    return f"postgresql://{usuario}:{quote(clave, safe='')}@{resto}"


def main() -> None:
    print("Paso 1: pega la cadena de conexion de Supabase (Connect -> Direct -> Session pooler).")
    print("        Para pegar en esta ventana: clic derecho. Despues Enter.\n")
    cadena = input("Cadena: ")
    print("\nPaso 2: pega o escribi la contraseña de la base (vas a ver un * por cada letra).\n")
    clave = leer_clave("Contraseña de la base: ").strip()
    if not clave:
        raise SystemExit("No escribiste ninguna contraseña.")
    try:
        url = armar_url(cadena, clave)
    except ValueError as e:
        raise SystemExit(str(e))

    print("\nProbando la conexion...")
    try:
        conn = db.ConexionPostgres(url)
        conn.execute("SELECT 1")
        conn.close()
    except Exception as e:  # cualquier fallo de red o de contraseña
        motivo = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
        raise SystemExit(
            f"No se pudo conectar: {motivo}\n"
            "Revisa que la contraseña sea la de la base (no la de tu cuenta) y que la cadena sea "
            "la de Session pooler. No se guardo nada."
        )

    guardar_en_env({"DATABASE_URL": url})
    print("Conexion correcta. Quedo guardada en .env.")


if __name__ == "__main__":
    main()
