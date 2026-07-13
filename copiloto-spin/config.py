"""Carga de configuracion desde el archivo .env (sin dependencias externas)."""

from pathlib import Path

_ENV = Path(__file__).parent / ".env"


def _cargar_env() -> dict:
    valores = {}
    if _ENV.exists():
        for linea in _ENV.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, _, valor = linea.partition("=")
                valores[clave.strip()] = valor.strip()
    return valores


_env = _cargar_env()

DEEPGRAM_API_KEY = _env.get("DEEPGRAM_API_KEY", "")
ANTHROPIC_API_KEY = _env.get("ANTHROPIC_API_KEY", "")
# Workers AI (tier Economico del copiloto). Opcionales: si no hay token fijo
# aqui, copiloto.py usa el de la sesion de wrangler y lo refresca solo.
CLOUDFLARE_API_TOKEN = _env.get("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = _env.get("CLOUDFLARE_ACCOUNT_ID", "")

if not DEEPGRAM_API_KEY:
    raise SystemExit("Falta DEEPGRAM_API_KEY en el archivo .env")
