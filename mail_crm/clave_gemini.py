"""Cambia la clave de Gemini sin editar archivos a mano.

Uso:  python -m mail_crm.clave_gemini

Te pide la clave nueva, comprueba contra Gemini que funcione, la guarda en .env
y la deja en el portapapeles para que la pegues en Render.
"""
import subprocess

from . import clasificador, config
from .clave import guardar_en_env, leer_clave


def main() -> None:
    print("Pega la clave NUEVA de Gemini (clic derecho para pegar; vas a ver asteriscos) y Enter.\n")
    nueva = leer_clave("Clave nueva: ").strip()
    if len(nueva) < 20:
        raise SystemExit("Eso es muy corto para ser una clave de Gemini. No se cambio nada.")
    if nueva == config.GEMINI_API_KEY:
        raise SystemExit("Esa es la misma clave que ya tenias. Crea una nueva en AI Studio.")

    print("\nProbando la clave contra Gemini...")
    anterior, config.GEMINI_API_KEY = config.GEMINI_API_KEY, nueva
    try:
        modelos = clasificador.listar_modelos()
    except clasificador.ErrorIA as e:
        config.GEMINI_API_KEY = anterior
        raise SystemExit(f"La clave no funciono: {str(e)[:200]}\nNo se cambio nada.")

    guardar_en_env({"GEMINI_API_KEY": nueva})
    print(f"Funciona ({len(modelos)} modelos disponibles). Quedo guardada en .env.")
    try:
        subprocess.run("clip", input=nueva, text=True, check=True, shell=True)
        print("\nTambien quedo copiada en el portapapeles: ahora pegala en Render")
        print("(servicio mail-crm -> Environment -> GEMINI_API_KEY -> Ctrl+A, Ctrl+V -> Save).")
    except (OSError, subprocess.CalledProcessError):
        print("\nNo pude copiarla al portapapeles: copiala de nuevo desde AI Studio para pegarla en Render.")


if __name__ == "__main__":
    main()
