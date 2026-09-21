"""
Copiloto SPIN — asistente de ventas en tiempo real.

Escucha la llamada (voz del prospecto por el audio del sistema + tu voz por
el microfono), transcribe en vivo con Deepgram y cada ~25 segundos le pide
a Claude las siguientes preguntas SPIN, mostradas en una ventana flotante.

El vendedor elige en la ventana como vende (se recuerda entre sesiones):
  - En 2 llamadas: la 1a es indagacion SPIN y la 2a es la de cierre (oferta,
    precio y objeciones); se marca cual toca hoy.
  - En 1 llamada: indaga, presenta, da precio y cierra en la misma llamada.
La informacion del negocio (ofertas, clientes, objeciones, prospecto de hoy) se
carga desde la ventana "Tu negocio", a mano o pegando material para que la IA
lo organice.

Uso:
  python copiloto.py                        # ventana flotante (uso real en llamadas)
  python copiloto.py --cierre               # venta en 2 llamadas, hoy toca la de cierre
  python copiloto.py --una-llamada          # venta completa en una sola llamada
  python copiloto.py --modelo economico     # cerebro Sonnet 5 (mas barato que el Max)
  python copiloto.py --negocio demo-boletas # vender OTRO negocio (carpeta en negocios/)
  python copiloto.py --oferta imperio       # fijar la oferta a vender HOY (o selector "Vender:")
  python copiloto.py --consola 40           # prueba en consola durante N segundos
  python copiloto.py --intervalo 20         # cambia la cadencia del analisis
"""

import argparse
import asyncio
import json
import os
import queue
import re
import subprocess
import threading
import time
import tomllib
import unicodedata
import warnings
from pathlib import Path
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)
import audioop  # noqa: E402

import httpx
import pyaudiowpatch as pyaudio
import websockets
from anthropic import AsyncAnthropic

from config import (
    ANTHROPIC_API_KEY,
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_API_TOKEN,
    DEEPGRAM_API_KEY,
)
from prompt_spin import NEGOCIO_DEFECTO, cargar_prompts, negocios_disponibles

CHUNK_MS = 100
MODELO = "claude-opus-5"
CARPETA_LLAMADAS = Path(__file__).parent / "llamadas"
# Raiz del proyecto (donde vive .claude/skills/auditar-llamada): claude -p debe
# correr desde ahi para que encuentre la skill.
RAIZ_PROYECTO = Path(__file__).parent.parent

# Tiers del analisis EN VIVO. La ventana ofrece dos: max = Opus 5 (el mejor,
# ~$1.50/llamada de 30 min; mismo precio que Opus 4.8) y economico = Sonnet 5
# (~$0.60-0.90; en el A/B del 2026-07-11 saco 7/8 vs 8/8 de Opus, ver
# ab_modelos.md). Los dos corren con la MISMA clave de Anthropic, asi cualquier
# vendedor los usa sin montar nada mas. Fable 5.1 se descarto para el vivo:
# cuesta el doble que Opus y siempre razona antes de responder (mas espera).
# "glm" (GLM-5.2 abierto via Workers AI, ~$0.50) queda solo por flag: exige
# cuenta de Cloudflare con Workers Paid, demasiada friccion para repartirlo.
MODELOS_VIVO = {
    "max": {"id": "claude-opus-5", "etiqueta": "Máx (Opus 5)"},
    "economico": {"id": "claude-sonnet-5", "etiqueta": "Económico (Sonnet 5)"},
    "glm": {"id": None, "etiqueta": "GLM-5.2"},
}
MODELOS_VENTANA = ("economico", "max")

# Workers AI (tier glm): si no hay CLOUDFLARE_API_TOKEN en .env se usa el
# token de la sesion de wrangler, que expira ~1h — se refresca solo corriendo
# wrangler en el proyecto del bot de IG (el unico con wrangler instalado).
CUENTA_CLOUDFLARE = CLOUDFLARE_ACCOUNT_ID or "e935213e2193d286e0a788544a9935cd"
MODELO_ECONOMICO_CF = "@cf/zai-org/glm-5.2"
RUTA_TOKEN_WRANGLER = (
    Path(os.environ.get("APPDATA", "")) / "xdg.config" / ".wrangler" / "config" / "default.toml"
)
DIR_WRANGLER = Path(__file__).parent.parent.parent / "CLI Clodflare Agentes"

forzar_analisis = threading.Event()
solicitar_auditoria = threading.Event()
# Tier del modelo del analisis en vivo; compartido tkinter/asyncio igual que
# modo_analisis (el selector de la ventana lo cambia en caliente).
modelo_vivo = {"valor": "max"}
# Prompts del negocio activo ({"nombre","ofertas","spin","cierre","completa",
# "auditoria"}). Arranca con el ultimo negocio usado (o --negocio); la ventana
# "Tu negocio" lo actualiza EN SITIO al guardar, sin reiniciar.
prompts_activos = cargar_prompts()
# Modo del asesor: "spin" (1a de 2 llamadas, indagacion), "cierre" (2a de 2:
# oferta/precio/objeciones) o "completa" (toda la venta en una llamada). Dict
# compartido entre el hilo de tkinter y el de asyncio; el asesor lo lee en
# cada analisis, se puede cambiar en vivo.
modo_analisis = {"valor": "spin"}

# Lo que el vendedor eligio la ultima vez (como vende, negocio, modelo); la
# ventana lo guarda al cambiarlo y los flags de la linea de comandos mandan.
RUTA_PREFERENCIAS = Path(__file__).parent / "preferencias.json"


def leer_preferencias() -> dict:
    try:
        return json.loads(RUTA_PREFERENCIAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_preferencias(**cambios) -> None:
    try:
        RUTA_PREFERENCIAS.write_text(
            json.dumps({**leer_preferencias(), **cambios}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass  # sin disco no se recuerda la eleccion; la llamada sigue


# Oferta a vender HOY, fijada por el vendedor (selector "Vender:" de la
# ventana o flag --oferta). "" = automatico: el modelo decide con el contexto.
oferta_objetivo = {"valor": ""}


def _bloque_oferta() -> str:
    """Anexo del system prompt cuando el vendedor fijo la oferta del dia."""
    oferta = oferta_objetivo["valor"]
    if not oferta:
        return ""
    return (
        "\n\n# OBJETIVO FIJADO EN VIVO POR EL VENDEDOR (PRIORIDAD ABSOLUTA)\n"
        f"El vendedor fijó en la app la oferta a vender HOY: **{oferta}**. "
        "Esto manda sobre cualquier otro objetivo, incluido el del contexto "
        "de la llamada si dice otra cosa. Orienta dolores, implicaciones, "
        "pitch, manejo de objeciones y cierre hacia ESA oferta. Sugiere "
        "pivotear a otra oferta SOLO si el prospecto claramente no califica "
        "o la pide él mismo — y si eso pasa, dilo explícitamente.\n"
    )


class Transcript:
    """Transcripcion acumulada de la llamada, compartida entre tareas.

    Cada linea se guarda ademas en llamadas/llamada_<fecha>.txt (historico y
    materia prima de la auditoria; el archivo se crea con la primera frase).
    """

    def __init__(self) -> None:
        self.lineas: list[tuple[str, str, str]] = []
        self.archivo = CARPETA_LLAMADAS / f"llamada_{time.strftime('%Y%m%d_%H%M%S')}.txt"

    def agregar(self, quien: str, texto: str) -> None:
        hora = time.strftime("%H:%M:%S")
        self.lineas.append((hora, quien, texto))
        try:
            CARPETA_LLAMADAS.mkdir(exist_ok=True)
            with self.archivo.open("a", encoding="utf-8") as f:
                f.write(f"[{hora}] {quien}: {texto}\n")
        except OSError:
            pass  # si el disco falla, la transcripcion en memoria sigue viva

    def formatear(self) -> str:
        return "\n".join(f"[{h}] {q}: {t}" for h, q, t in self.lineas)


def buscar_dispositivos(p: "pyaudio.PyAudio") -> tuple[dict | None, dict]:
    """Devuelve (microfono, loopback). El microfono puede ser None."""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    salida = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

    loopback = salida if salida.get("isLoopbackDevice") else None
    if loopback is None:
        for dev in p.get_loopback_device_info_generator():
            if salida["name"] in dev["name"]:
                loopback = dev
                break
    if loopback is None:
        raise SystemExit(f"No se encontro loopback para: {salida['name']}")

    try:
        microfono = p.get_default_input_device_info()
        if microfono.get("isLoopbackDevice"):
            microfono = None
    except OSError:
        microfono = None

    return microfono, loopback


async def pipeline_audio(p, dispositivo, etiqueta, transcript, ui) -> None:
    """Captura un dispositivo de audio y agrega sus frases al transcript."""
    rate = int(dispositivo["defaultSampleRate"])
    canales = min(int(dispositivo["maxInputChannels"]), 2)
    frames = int(rate * CHUNK_MS / 1000)
    url = (
        "wss://api.deepgram.com/v1/listen"
        "?model=nova-2&language=es&smart_format=true&interim_results=false"
        f"&encoding=linear16&sample_rate={rate}&channels=1"
    )

    stream = p.open(
        format=pyaudio.paInt16,
        channels=canales,
        rate=rate,
        frames_per_buffer=frames,
        input=True,
        input_device_index=dispositivo["index"],
    )
    loop = asyncio.get_running_loop()
    try:
        async with websockets.connect(
            url, additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        ) as ws:
            ui.put(("estado", f"Escuchando ({etiqueta})"))

            async def emisor():
                while True:
                    datos = await loop.run_in_executor(
                        None, lambda: stream.read(frames, exception_on_overflow=False)
                    )
                    if canales == 2:
                        datos = audioop.tomono(datos, 2, 0.5, 0.5)
                    await ws.send(datos)

            async def receptor():
                async for mensaje in ws:
                    msg = json.loads(mensaje)
                    if msg.get("type") != "Results":
                        continue
                    texto = msg["channel"]["alternatives"][0]["transcript"].strip()
                    if texto and msg.get("is_final"):
                        transcript.agregar(etiqueta, texto)
                        ui.put(("linea", f"{etiqueta}: {texto}"))

            await asyncio.gather(emisor(), receptor())
    finally:
        time.sleep(0.15)  # deja terminar la lectura de audio pendiente en el executor
        try:
            stream.stop_stream()
            stream.close()
        except OSError:
            pass


TAMANO_BLOQUE_CACHE = 3000  # caracteres (~750 tokens) por bloque congelado


def _bloques_cache(texto: str, n: int = TAMANO_BLOQUE_CACHE) -> list[dict]:
    """Parte la transcripcion en bloques para que el cache incremental funcione.

    El cache de Anthropic empareja bloques COMPLETOS: un bloque unico que crece
    en cada analisis jamas se lee del cache (se paga entero cada vez). Cortando
    el texto en trozos fijos de n caracteres, los trozos completos quedan
    identicos entre analisis (el texto solo crece por el final) y se leen del
    cache a ~10% del precio; solo la cola cambiante se paga a precio lleno.
    El breakpoint va en el ultimo trozo completo.
    """
    completos = len(texto) // n
    bloques: list[dict] = [
        {"type": "text", "text": texto[i * n : (i + 1) * n]} for i in range(completos)
    ]
    if bloques:
        bloques[-1]["cache_control"] = {"type": "ephemeral"}
    cola = texto[completos * n :]
    if cola or not bloques:
        bloques.append({"type": "text", "text": cola or texto})
    return bloques


def _token_cloudflare(refrescar: bool = False) -> str:
    """Token para Workers AI: el fijo del .env, o el de la sesion de wrangler.

    El de wrangler expira ~1h: con refrescar=True se corre `wrangler whoami`
    para renovarlo antes de releerlo. Bloqueante: llamar via to_thread.
    """
    if CLOUDFLARE_API_TOKEN:
        return CLOUDFLARE_API_TOKEN
    if refrescar:
        subprocess.run(
            "npx wrangler whoami", shell=True, cwd=DIR_WRANGLER,
            capture_output=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    with open(RUTA_TOKEN_WRANGLER, "rb") as f:
        return tomllib.load(f)["oauth_token"]


async def _analizar_glm(prompt: str, texto: str) -> str:
    """Tier Economico: GLM-5.2 (abierto) en Workers AI, endpoint OpenAI-compat.

    GLM es razonador: pide max_tokens amplio para que el pensamiento (se paga
    pero no se muestra) no se coma la respuesta. Si el token de wrangler
    vencio (401/403), se refresca y se reintenta una vez.
    """
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CUENTA_CLOUDFLARE}"
        "/ai/v1/chat/completions"
    )
    cuerpo = {
        "model": MODELO_ECONOMICO_CF,
        "max_tokens": 3000,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": "Transcripcion de la llamada hasta ahora:\n\n" + texto,
            },
        ],
    }
    async with httpx.AsyncClient(timeout=120.0) as http:
        for intento in (1, 2):
            token = await asyncio.to_thread(_token_cloudflare, intento == 2)
            r = await http.post(
                url, json=cuerpo, headers={"Authorization": f"Bearer {token}"}
            )
            if r.status_code in (401, 403) and intento == 1:
                continue  # token de wrangler vencido: refrescar y reintentar
            if r.status_code == 429:
                raise RuntimeError(
                    "cuota gratis diaria de Workers AI agotada — activa "
                    "Workers Paid ($5/mes) en Cloudflare o usa Premium/Máx"
                )
            r.raise_for_status()
            respuesta = (r.json()["choices"][0]["message"].get("content") or "").strip()
            if not respuesta:
                raise RuntimeError("GLM agoto los tokens razonando y no respondio")
            return respuesta
    raise RuntimeError("GLM sin respuesta tras reintento")


async def analizar_en_vivo(
    client: AsyncAnthropic, tier: str, prompt: str, texto: str
) -> str:
    """Un analisis del transcript con el tier elegido; devuelve la sugerencia."""
    if tier == "glm":
        return await _analizar_glm(prompt, texto)
    # Los dos piensan por defecto y el razonamiento se comeria el max_tokens
    # (devolveria vacio). Sonnet 5 va sin razonamiento (asi se valido en el
    # A/B). Opus 5 va con razonamiento minimo: apagarselo del todo puede colar
    # etiquetas internas en la respuesta, y con esfuerzo "low" tarda lo mismo
    # (~9 s medido con una llamada real).
    if tier == "economico":
        extra = {"thinking": {"type": "disabled"}}
    else:
        extra = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
    respuesta = await client.messages.create(
        model=MODELOS_VIVO[tier]["id"],
        max_tokens=4000,
        system=[
            {
                "type": "text",
                "text": prompt,
                # prompt >4K tokens: el cache esta activo
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": _bloques_cache(
                    "Transcripcion de la llamada hasta ahora:\n\n" + texto
                ),
            }
        ],
        **extra,
    )
    if respuesta.stop_reason == "refusal":
        raise RuntimeError("el modelo declinó responder este tramo de la llamada")
    return next(b.text for b in respuesta.content if b.type == "text")


async def asesor(transcript, ui, intervalo: int) -> None:
    """Cada `intervalo` segundos analiza el transcript con el modelo y modo activos."""
    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    analizadas = 0
    while True:
        forzado = False
        for _ in range(intervalo):
            await asyncio.sleep(1)
            if forzar_analisis.is_set():
                forzar_analisis.clear()
                forzado = True
                break
        if not transcript.lineas:
            continue
        if len(transcript.lineas) == analizadas and not forzado:
            continue  # nada nuevo y nadie forzo (cambiar de modo tambien fuerza)
        analizadas = len(transcript.lineas)

        prompt = prompts_activos[modo_analisis["valor"]] + _bloque_oferta()
        tier = modelo_vivo["valor"]
        etiqueta = MODELOS_VIVO[tier]["etiqueta"]
        ui.put(("estado", f"Analizando con {etiqueta}..."))
        try:
            texto = await analizar_en_vivo(client, tier, prompt, transcript.formatear())
            ui.put(("sugerencia", texto))
            ui.put(("estado", f"Sugerencia de {etiqueta}: {time.strftime('%H:%M:%S')}"))
        except Exception as e:  # noqa: BLE001 — mostrar en UI y dejar rastro
            _log_error(f"Analisis en vivo ({tier}) fallo: {e}")
            ui.put(("estado", f"Error al analizar: {_explicar_error(e)}"))


def _explicar_error(e: Exception) -> str:
    """Traduce los errores tipicos de un usuario nuevo a algo accionable."""
    texto = str(e)
    bajo = texto.lower()
    if "credit balance" in bajo:
        return "tu cuenta de Anthropic no tiene créditos: recarga en console.anthropic.com → Billing"
    if "401" in texto or "authentication" in bajo or "invalid x-api-key" in bajo:
        servicio = "Deepgram" if "websocket" in bajo or "deepgram" in bajo else "Anthropic"
        return f"la clave de {servicio} no es válida: revísala en el archivo .env y vuelve a abrir"
    if "getaddrinfo" in bajo or "connection error" in bajo:
        return f"sin conexión a internet ({texto[:80]})"
    return texto


def _log_error(mensaje: str) -> None:
    """Registro persistente de errores (la barra de estado es efimera)."""
    CARPETA_LLAMADAS.mkdir(exist_ok=True)
    with open(CARPETA_LLAMADAS / "errores.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mensaje}\n")


def ejecutar_auditoria_claude_code(negocio: str = NEGOCIO_DEFECTO) -> Path:
    """Audita con Claude Code (/auditar-llamada): usa el plan Max, no gasta API.

    Lanza `claude -p` desde la raiz del proyecto; la skill toma la ultima
    llamada_*.txt, lee el contexto de negocios/<negocio>/ y escribe
    llamadas/auditoria_<fecha>.md. Si el copiloto se cierra a mitad, el proceso
    hijo sobrevive y el informe se genera igual. Bloqueante: llamar via executor.
    """
    previas = set(CARPETA_LLAMADAS.glob("auditoria_*.md"))
    resultado = subprocess.run(
        ["claude", "-p", f"/auditar-llamada negocio={negocio}",
         "--permission-mode", "bypassPermissions"],
        cwd=RAIZ_PROYECTO,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    nuevas = sorted(set(CARPETA_LLAMADAS.glob("auditoria_*.md")) - previas)
    if not nuevas:
        detalle = (resultado.stderr or resultado.stdout or "sin salida").strip()[-400:]
        raise RuntimeError(
            f"Claude Code no genero el informe (codigo {resultado.returncode}): {detalle}"
        )
    return nuevas[-1]


async def ejecutar_auditoria(client: AsyncAnthropic, texto_llamada: str) -> str:
    """Audita la llamada completa via API (respaldo con costo si falla Claude Code).

    Opus 4.8 con effort "max" + razonamiento adaptativo = la respuesta mas
    inteligente que da el modelo (tarda varios minutos). Streaming obligatorio:
    con max_tokens alto una peticion sin streaming se corta por timeout del SDK.
    """
    async with client.messages.stream(
        model=MODELO,
        max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": "max"},
        system=prompts_activos["auditoria"],
        messages=[
            {
                "role": "user",
                "content": "Transcripción completa de la llamada que terminó. "
                "Haz la auditoría:\n\n" + texto_llamada,
            }
        ],
    ) as stream:
        respuesta = await stream.get_final_message()
    return next(b.text for b in respuesta.content if b.type == "text")


async def auditor(transcript, ui) -> None:
    """Espera el boton 'Auditar llamada' y genera el informe en llamadas/.

    Primero audita con Claude Code (plan Max, $0 de API); si eso falla (CLI
    ausente, sesion no iniciada, timeout) cae a la auditoria via API.
    """
    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    while True:
        await asyncio.sleep(1)
        if not solicitar_auditoria.is_set():
            continue
        solicitar_auditoria.clear()
        if not transcript.lineas:
            ui.put(("estado", "No hay transcripción que auditar todavía"))
            continue

        ui.put(("estado", "Auditando con Claude Code (plan Max)... tarda varios minutos"))
        ruta = None
        try:
            ruta = await asyncio.get_running_loop().run_in_executor(
                None, ejecutar_auditoria_claude_code, prompts_activos["nombre"]
            )
        except Exception as e:  # noqa: BLE001 — dejar rastro y caer a la API
            _log_error(f"Auditoria via Claude Code fallo: {e}")
            ui.put(("estado", "Claude Code falló; auditando vía API (con costo)..."))
            try:
                informe = await ejecutar_auditoria(client, transcript.formatear())
                CARPETA_LLAMADAS.mkdir(exist_ok=True)
                ruta = CARPETA_LLAMADAS / f"auditoria_{time.strftime('%Y%m%d_%H%M%S')}.md"
                ruta.write_text(informe, encoding="utf-8")
            except Exception as e2:  # noqa: BLE001 — mostrar y registrar el error
                _log_error(f"Auditoria via API fallo: {e2}")
                ui.put(("estado", f"Error en la auditoría: {e2}"))
        if ruta is not None:
            ui.put(("estado", f"Auditoría lista: {ruta.name}"))
            try:
                os.startfile(ruta)  # abre el informe con la app por defecto
            except OSError:
                pass
        solicitar_auditoria.clear()  # descarta clics repetidos durante la auditoria


async def nucleo(transcript, ui, intervalo: int) -> None:
    """Arranca las capturas de audio, el asesor SPIN y el auditor post-llamada."""
    with pyaudio.PyAudio() as p:
        microfono, loopback = buscar_dispositivos(p)
        tareas = [pipeline_audio(p, loopback, "Prospecto", transcript, ui)]
        if microfono is not None:
            tareas.append(pipeline_audio(p, microfono, "Tú", transcript, ui))
        else:
            ui.put(("estado", "Sin microfono: solo se transcribe al prospecto"))
        tareas.append(asesor(transcript, ui, intervalo))
        tareas.append(auditor(transcript, ui))
        await asyncio.gather(*tareas)


# ----------------------------- modo consola ------------------------------


async def modo_consola(duracion: int, intervalo: int) -> None:
    transcript = Transcript()
    ui: queue.Queue = queue.Queue()

    async def drenar():
        while True:
            try:
                tipo, contenido = ui.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.2)
                continue
            if tipo == "linea":
                print(f"  {contenido}", flush=True)
            elif tipo == "sugerencia":
                print("\n" + "=" * 52, flush=True)
                print(contenido, flush=True)
                print("=" * 52 + "\n", flush=True)
            else:
                print(f"* {contenido}", flush=True)

    tarea = asyncio.ensure_future(
        asyncio.gather(nucleo(transcript, ui, intervalo), drenar())
    )
    await asyncio.sleep(duracion)
    tarea.cancel()
    try:
        await tarea
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    await asyncio.sleep(0.3)  # margen para que el audio cierre limpio
    print("\nFin de la prueba en consola.")


# ----------------------------- modo ventana ------------------------------


# Paleta y tipos de la ventana. Una sola voz fuerte: el ambar de "lo que dices
# ahora" (como la luz de un teleprompter); todo lo demas se queda callado.
COLORES = {
    "fondo": "#141821",
    "superficie": "#1B2130",
    "elevada": "#2A3246",
    "linea": "#2C3448",
    "texto": "#E9ECF2",
    "tenue": "#8A93A8",
    "apagado": "#5D677D",
    "ambar": "#F3B64C",
    "ambar_fondo": "#2A2518",
    "coral": "#FF8069",
    "coral_fondo": "#2B1D1F",
    "verde": "#5CD6A1",
    "verde_fondo": "#16291F",
    "prospecto": "#8DB8FF",
}
FUENTE_ETIQUETA = ("Bahnschrift SemiCondensed", 10)
FUENTE_FASE = ("Bahnschrift SemiBold SemiConden", 10)
FUENTE_PRINCIPAL = ("Segoe UI Variable Display Semib", 15)
FUENTE_TEXTO = ("Segoe UI Variable Text", 11)
FUENTE_CHICA = ("Segoe UI Variable Text", 9)
FUENTE_NEGRITA = ("Segoe UI Variable Text Semibold", 10)
FUENTE_TITULO = ("Segoe UI Variable Display Semib", 16)
FUENTE_EDITOR = ("Segoe UI Variable Text", 10)

# Fases que muestra la barra de progreso, por modo: filas de (rotulo, prefijo
# con el que se reconoce lo que el modelo pone en FASE ACTUAL / MOMENTO). La
# venta en una llamada recorre las dos mitades: indagar arriba, vender abajo.
_INDAGAR = [("Situación", "situa"), ("Problema", "probl"), ("Implicación", "impli"),
            ("Necesidad", "neces")]
FASES_UI = {
    "spin": [_INDAGAR + [("Cierre", "cierr")]],
    "cierre": [[("Reconexión", "recon"), ("Presentación", "prese"), ("Precio", "preci"),
                ("Objeciones", "objec"), ("Cierre", "cierr")]],
    "completa": [_INDAGAR, [("Oferta", "prese"), ("Precio", "preci"),
                            ("Objeciones", "objec"), ("Cierre", "cierr")]],
}
CAMPOS_SUGERENCIA = (
    "FASE ACTUAL", "MOMENTO", "AVATAR", "DOLORES DETECTADOS", "OBJECION",
    "SENAL DE COMPRA",
)


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def _parsear_sugerencia(texto: str) -> tuple[dict, list[str]]:
    """Separa la respuesta del modelo en campos (FASE, AVATAR...) y preguntas."""
    campos: dict[str, str] = {}
    preguntas: list[str] = []
    for linea in texto.splitlines():
        limpia = linea.replace("*", "").strip()
        if not limpia:
            continue
        numerada = re.match(r"^\d[.)]\s*(.+)$", limpia)
        if numerada:
            preguntas.append(numerada.group(1).strip().strip('"“”'))
            continue
        if ":" in limpia:
            clave, valor = limpia.split(":", 1)
            clave = _sin_tildes(clave.strip()).upper()
            if clave in CAMPOS_SUGERENCIA:
                campos[clave] = valor.strip()
    return campos, preguntas


def _es_vacio(valor: str | None) -> bool:
    """True si el modelo dijo "ninguno", "ninguna aún", "aún no claro"..."""
    if valor is None:
        return True
    v = _sin_tildes(valor.lower()).strip(' ."-')
    return not v or v.startswith(("ninguno", "ninguna", "aun no"))


def modo_ventana(intervalo: int, arrancar_nucleo: bool = True):
    """Ventana flotante. Con arrancar_nucleo=False no escucha audio ni corre
    mainloop: devuelve sus piezas (raiz, ui, ...) para vistas previas."""
    import tkinter as tk

    import ventana_negocio

    C = COLORES
    transcript = Transcript()
    ui: queue.Queue = queue.Queue()

    def correr_nucleo():
        try:
            asyncio.run(nucleo(transcript, ui, intervalo))
        except Exception as e:  # noqa: BLE001 — sin esto la ventana queda en "Iniciando..."
            _log_error(f"La escucha se detuvo: {e!r}")
            ui.put(("estado", f"Error, la escucha se detuvo: {_explicar_error(e)}"))

    if arrancar_nucleo:
        threading.Thread(target=correr_nucleo, daemon=True).start()

    # Nitidez: sin esto Windows estira la ventana como imagen y el texto sale
    # borroso en pantallas con escala (125%, 150%). Luego se escala a mano.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    raiz = tk.Tk()
    escala = raiz.winfo_fpixels("1i") / 96

    def px(n: int) -> int:
        return round(n * escala)

    raiz.title(f"Copiloto SPIN — {prompts_activos['nombre']}")
    raiz.geometry(f"{px(460)}x{px(740)}+{px(40)}+{px(40)}")
    raiz.minsize(px(380), px(560))
    raiz.configure(bg=C["fondo"])
    raiz.attributes("-topmost", True)

    class Segmentado(tk.Frame):
        """Selector de opciones en pastilla (reemplaza los radio buttons)."""

        def __init__(self, padre, opciones, valor, al_cambiar, fuente=FUENTE_ETIQUETA):
            super().__init__(padre, bg=C["linea"], padx=px(1), pady=px(1))
            self.valor, self.al_cambiar, self.botones = valor, al_cambiar, {}
            for i, (texto, v) in enumerate(opciones):
                b = tk.Label(self, text=texto, font=fuente, padx=px(8), pady=px(4), cursor="hand2")
                b.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 1, 0))
                b.bind("<Button-1>", lambda _e, v=v: self.elegir(v))
                b.bind("<Enter>", lambda _e, b=b, v=v: v != self.valor and b.configure(fg=C["texto"]))
                b.bind("<Leave>", lambda _e: self.pintar())
                self.botones[v] = b
            self.pintar()

        def elegir(self, v):
            if v == self.valor:
                return
            self.valor = v
            self.pintar()
            self.al_cambiar(v)

        def pintar(self):
            for v, b in self.botones.items():
                activo = v == self.valor
                b.configure(
                    bg=C["elevada"] if activo else C["superficie"],
                    fg=C["texto"] if activo else C["tenue"],
                )

    def boton(padre, texto, comando, primario=False):
        fondo, frente = (C["ambar"], C["fondo"]) if primario else (C["superficie"], C["texto"])
        hover = "#FFC866" if primario else C["elevada"]
        b = tk.Label(padre, text=texto, bg=fondo, fg=frente, cursor="hand2", pady=px(8),
                     font=FUENTE_NEGRITA)
        b.bind("<Button-1>", lambda _e: comando())
        b.bind("<Enter>", lambda _e: b.configure(bg=hover))
        b.bind("<Leave>", lambda _e: b.configure(bg=fondo))
        return b

    # --- cabecera: estado de la escucha + negocio -----------------------
    cabecera = tk.Frame(raiz, bg=C["fondo"])
    cabecera.pack(fill="x", padx=px(14), pady=(px(12), px(0)))
    punto = tk.Label(cabecera, text="●", bg=C["fondo"], fg=C["ambar"], font=("Segoe UI", 10))
    punto.pack(side="left")
    estado = tk.Label(cabecera, text="Iniciando...", bg=C["fondo"], fg=C["tenue"],
                      font=FUENTE_CHICA, anchor="w", justify="left", wraplength=px(270))
    estado.pack(side="left", fill="x", expand=True, padx=(px(4), px(0)))
    boton_negocio = tk.Label(
        cabecera, bg=C["superficie"], fg=C["texto"], font=FUENTE_ETIQUETA,
        padx=px(10), pady=px(3), cursor="hand2",
    )
    boton_negocio.pack(side="right")
    boton_negocio.bind("<Enter>", lambda _e: boton_negocio.configure(bg=C["elevada"]))
    boton_negocio.bind("<Leave>", lambda _e: boton_negocio.configure(bg=C["superficie"]))

    # --- como vende y que llamada toca hoy -------------------------------
    rotulo_principal = {"spin": "Pregunta ahora", "cierre": "Di esto ahora",
                        "completa": "Di esto ahora"}
    fuente_modo = ("Bahnschrift SemiCondensed", 11)

    def aplicar_modo(valor):
        modo_analisis["valor"] = valor
        pintar_fases(None)
        forzar_analisis.set()  # re-analiza ya con el cerebro del modo nuevo

    def fila_modo(rotulo, opciones, valor, al_cambiar):
        fila = tk.Frame(marco_modo, bg=C["fondo"])
        tk.Label(fila, text=rotulo, width=9, anchor="w", bg=C["fondo"], fg=C["tenue"],
                 font=FUENTE_ETIQUETA).pack(side="left")
        selector = Segmentado(fila, opciones, valor, al_cambiar, fuente=fuente_modo)
        selector.pack(side="left", fill="x", expand=True)
        return fila, selector

    def cambiar_llamadas(cuantas):
        guardar_preferencias(llamadas=cuantas)
        if cuantas == 1:
            fila_hoy.pack_forget()
            aplicar_modo("completa")
        else:
            fila_hoy.pack(fill="x", pady=(px(6), 0))
            aplicar_modo(selector_hoy.valor)

    marco_modo = tk.Frame(raiz, bg=C["fondo"])
    marco_modo.pack(fill="x", padx=px(14), pady=(px(10), px(0)))
    en_una = modo_analisis["valor"] == "completa"
    fila_vendo, _ = fila_modo(
        "Vendo en", (("1 llamada", 1), ("2 llamadas", 2)), 1 if en_una else 2, cambiar_llamadas)
    fila_vendo.pack(fill="x")
    fila_hoy, selector_hoy = fila_modo(
        "Hoy toca", (("1ª: indagar", "spin"), ("2ª: cerrar la venta", "cierre")),
        "spin" if en_una else modo_analisis["valor"], aplicar_modo)
    if not en_una:
        fila_hoy.pack(fill="x", pady=(px(6), 0))

    # --- barra de fases (la secuencia real de la llamada) --------------
    marco_fases = tk.Frame(raiz, bg=C["fondo"])
    marco_fases.pack(fill="x", padx=px(14), pady=(px(12), px(0)))

    def pintar_fases(actual: str | None):
        for w in marco_fases.winfo_children():
            w.destroy()
        filas = FASES_UI[modo_analisis["valor"]]
        clave = _sin_tildes(actual or "").strip().lower()[:5]
        prefijos = [prefijo for fila in filas for _, prefijo in fila]
        idx = prefijos.index(clave) if clave in prefijos else -1
        i = -1
        for n_fila, fila in enumerate(filas):
            marco_fila = tk.Frame(marco_fases, bg=C["fondo"])
            marco_fila.pack(fill="x", pady=(px(8) if n_fila else 0, 0))
            # uniform: todas las celdas del mismo ancho, las filas quedan alineadas
            for col in range(len(fila)):
                marco_fila.columnconfigure(col, weight=1, uniform="fase")
            for col, (fase, _prefijo) in enumerate(fila):
                i += 1
                celda = tk.Frame(marco_fila, bg=C["fondo"])
                celda.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else px(3), 0))
                barra = C["ambar"] if i == idx else (C["tenue"] if i < idx else C["linea"])
                tk.Label(
                    celda, text=fase, bg=C["fondo"], anchor="w",
                    fg=C["texto"] if i == idx else (C["tenue"] if i < idx else C["apagado"]),
                    font=FUENTE_FASE if i == idx else FUENTE_ETIQUETA,
                ).pack(fill="x", pady=(0, px(3)))
                tk.Frame(celda, bg=barra, height=px(3)).pack(fill="x")

    pintar_fases(None)

    # --- tarjeta de la sugerencia --------------------------------------
    # height chico a proposito: la tarjeta se estira con expand=True y asi no
    # le roba el espacio a la transcripcion cuando la ventana es baja.
    t = tk.Text(
        raiz, height=8, bg=C["superficie"], fg=C["texto"], font=FUENTE_TEXTO, wrap="word",
        relief="flat", padx=px(14), pady=px(10), state="disabled", cursor="arrow",
        highlightthickness=0, borderwidth=0, spacing2=px(2),
    )
    t.pack(fill="both", expand=True, padx=px(14), pady=(px(10), px(0)))
    t.tag_configure("etiqueta", font=FUENTE_ETIQUETA, foreground=C["tenue"], spacing1=px(14), spacing3=px(4))
    t.tag_configure("primera", spacing1=px(2))
    t.tag_configure(
        "principal", font=FUENTE_PRINCIPAL, foreground=C["ambar"], background=C["ambar_fondo"],
        lmargin1=px(12), lmargin2=px(12), rmargin=px(12), lmargincolor=C["ambar_fondo"],
        spacing1=px(10), spacing3=px(10), spacing2=px(3),
    )
    t.tag_configure("alterna", foreground=C["texto"], lmargin2=px(22), spacing1=px(5))
    t.tag_configure("num", foreground=C["apagado"])
    t.tag_configure("dolor", foreground=C["texto"], lmargin2=px(18), spacing1=px(3))
    t.tag_configure("vineta", foreground=C["coral"])
    t.tag_configure("dato", foreground=C["texto"])
    t.tag_configure("vacio", foreground=C["apagado"])
    for nombre, frente, fondo in (("obj", C["coral"], C["coral_fondo"]),
                                  ("senal", C["verde"], C["verde_fondo"])):
        comun = dict(background=fondo, lmargin1=px(12), lmargin2=px(12), rmargin=px(12), lmargincolor=fondo)
        t.tag_configure(f"{nombre}_titulo", font=FUENTE_NEGRITA, foreground=frente,
                        spacing1=px(10), **comun)
        t.tag_configure(f"{nombre}_texto", foreground=C["texto"], spacing3=px(10), spacing1=px(2), **comun)
    t.tag_configure("separa", font=("Segoe UI", 4))

    def escribir_sugerencia(bloques: list[tuple[str, tuple | str]]):
        t.configure(state="normal")
        t.delete("1.0", "end")
        for texto, etiquetas in bloques:
            t.insert("end", texto, etiquetas)
        t.configure(state="disabled")
        t.yview_moveto(0)

    def pintar_sugerencia(texto: str):
        campos, preguntas = _parsear_sugerencia(texto)
        if not preguntas:  # el modelo se salio del formato: mostrarlo tal cual
            escribir_sugerencia([(texto, "dato")])
            return
        pintar_fases(campos.get("FASE ACTUAL") or campos.get("MOMENTO"))
        b: list[tuple[str, tuple | str]] = []
        senal = campos.get("SENAL DE COMPRA")
        if not _es_vacio(senal):
            b += [("Señal de compra\n", "senal_titulo"), (senal + "\n", "senal_texto"),
                  ("\n", "separa")]
        b += [(rotulo_principal[modo_analisis["valor"]] + "\n",
               "etiqueta" if b else ("etiqueta", "primera")),
              (preguntas[0] + "\n", "principal")]
        if len(preguntas) > 1:
            b.append(("Otras opciones\n", "etiqueta"))
            for i, p in enumerate(preguntas[1:], start=2):
                b += [(f"{i}.   ", ("alterna", "num")), (p + "\n", "alterna")]
        objecion = campos.get("OBJECION")
        if not _es_vacio(objecion):
            nombre, flecha_obj, respuesta = objecion.partition("→")
            if not flecha_obj:
                nombre, _, respuesta = objecion.partition("->")
            b += [("\n", "separa"), (f"Objeción: {nombre.strip()}\n", "obj_titulo"),
                  ((respuesta.strip() or nombre.strip()) + "\n", "obj_texto")]
        if "DOLORES DETECTADOS" in campos:
            b.append(("Dolores detectados\n", "etiqueta"))
            dolores = campos["DOLORES DETECTADOS"]
            if _es_vacio(dolores):
                b.append(("Ninguno todavía\n", "vacio"))
            else:
                for d in (x.strip() for x in dolores.split("|")):
                    if d:
                        b += [("●  ", ("dolor", "vineta")), (d + "\n", "dolor")]
        if "AVATAR" in campos:
            avatar = campos["AVATAR"]
            b.append(("Avatar\n", "etiqueta"))
            b.append(("Aún no está claro\n", "vacio") if _es_vacio(avatar) else (avatar + "\n", "dato"))
        escribir_sugerencia(b)

    escribir_sugerencia([
        ("Esperando la conversación\n", ("etiqueta", "primera")),
        ("Cuando el prospecto empiece a hablar, aquí aparece la próxima "
         "pregunta. Si la quieres ya, pulsa Analizar ahora o F5.\n", "vacio"),
    ])

    # --- acciones --------------------------------------------------------
    acciones = tk.Frame(raiz, bg=C["fondo"])
    acciones.pack(fill="x", padx=px(14), pady=(px(10), px(0)))
    boton(acciones, "Analizar ahora   F5", forzar_analisis.set, primario=True).pack(
        side="left", fill="x", expand=True)
    boton(acciones, "Auditar al terminar", solicitar_auditoria.set).pack(
        side="left", fill="x", expand=True, padx=(px(8), px(0)))
    raiz.bind("<F5>", lambda _e: forzar_analisis.set())

    # --- ajustes plegables: modelo y oferta ------------------------------
    NOMBRES_MODELO = {"economico": "Económico", "max": "Máx", "glm": "GLM-5.2"}

    def resumen_ajustes() -> str:
        oferta = oferta_objetivo["valor"].capitalize() or "automática"
        return f"Modelo {NOMBRES_MODELO[modelo_vivo['valor']]}, oferta {oferta}"

    fila_ajustes = tk.Frame(raiz, bg=C["fondo"], cursor="hand2")
    fila_ajustes.pack(fill="x", padx=px(14), pady=(px(12), px(0)))
    flecha = tk.Label(fila_ajustes, text="▸  Ajustes", bg=C["fondo"], fg=C["tenue"],
                      font=FUENTE_ETIQUETA, cursor="hand2")
    flecha.pack(side="left")
    resumen = tk.Label(fila_ajustes, text=resumen_ajustes(), bg=C["fondo"], fg=C["apagado"],
                       font=FUENTE_ETIQUETA, cursor="hand2")
    resumen.pack(side="right")

    panel = tk.Frame(raiz, bg=C["fondo"])

    def fila_panel(rotulo, opciones, valor, al_cambiar):
        fila = tk.Frame(panel, bg=C["fondo"])
        fila.pack(fill="x", pady=(px(6), px(0)))
        tk.Label(fila, text=rotulo, width=9, anchor="w", bg=C["fondo"], fg=C["tenue"],
                 font=FUENTE_ETIQUETA).pack(side="left")
        Segmentado(fila, opciones, valor, al_cambiar).pack(side="left", fill="x", expand=True)
        return fila

    def cambiar_modelo(valor):
        # aplica desde el siguiente analisis (no fuerza uno para no gastar de mas)
        modelo_vivo["valor"] = valor
        guardar_preferencias(modelo=valor)
        resumen.configure(text=resumen_ajustes())

    def cambiar_oferta(valor):
        oferta_objetivo["valor"] = valor
        resumen.configure(text=resumen_ajustes())
        forzar_analisis.set()  # que las sugerencias apunten ya a la oferta elegida

    modelos_ui = list(MODELOS_VENTANA)
    if modelo_vivo["valor"] not in modelos_ui:  # llego por flag (--modelo glm)
        modelos_ui.append(modelo_vivo["valor"])
    fila_panel("Modelo", [(NOMBRES_MODELO[k], k) for k in modelos_ui],
               modelo_vivo["valor"], cambiar_modelo)

    fila_vender = {"marco": None}

    def armar_fila_vender():
        """Una opcion por cada oferta del negocio activo (se rearma al cambiarlo)."""
        if fila_vender["marco"] is not None:
            fila_vender["marco"].destroy()
            fila_vender["marco"] = None
        ofertas_ui = prompts_activos.get("ofertas") or []
        if oferta_objetivo["valor"] not in ofertas_ui:
            oferta_objetivo["valor"] = ""
        if len(ofertas_ui) > 1:  # con una sola oferta no hay nada que elegir
            fila_vender["marco"] = fila_panel(
                "Vender", [("Automática", "")] + [(o.capitalize(), o) for o in ofertas_ui],
                oferta_objetivo["valor"], cambiar_oferta)
        resumen.configure(text=resumen_ajustes())

    armar_fila_vender()

    # --- "Tu negocio": lo que el copiloto sabe del que vende -------------
    kit = SimpleNamespace(
        C=C, px=px, Segmentado=Segmentado, boton=boton, FUENTE_TITULO=FUENTE_TITULO,
        FUENTE_NEGRITA=FUENTE_NEGRITA, FUENTE_TEXTO=FUENTE_TEXTO, FUENTE_CHICA=FUENTE_CHICA,
        FUENTE_EDITOR=FUENTE_EDITOR, FUENTE_PESTANA=fuente_modo,
    )
    ventana_abierta = {"win": None}

    def pintar_negocio():
        nombre = ventana_negocio.nombre_visible(prompts_activos["nombre"])
        boton_negocio.configure(text=f"Tu negocio: {nombre}")
        raiz.title(f"Copiloto SPIN — {nombre}")

    def negocio_guardado(nombre):
        # En sitio: el asesor (otro hilo) lee este mismo dict en cada analisis.
        prompts_activos.update(cargar_prompts(nombre))
        guardar_preferencias(negocio=nombre)
        pintar_negocio()
        armar_fila_vender()
        forzar_analisis.set()

    def abrir_negocio(_e=None, empezar_nuevo=False):
        win = ventana_abierta["win"]
        if win is not None and win.winfo_exists():
            win.lift()
            return win
        ventana_abierta["win"] = ventana_negocio.abrir(
            raiz, kit, prompts_activos["nombre"], negocio_guardado, empezar_nuevo)
        return ventana_abierta["win"]

    boton_negocio.bind("<Button-1>", abrir_negocio)
    pintar_negocio()

    marco_trans = tk.Frame(raiz, bg=C["fondo"])

    def alternar_ajustes(_e=None):
        if panel.winfo_ismapped():
            panel.pack_forget()
            flecha.configure(text="▸  Ajustes")
        else:
            panel.pack(fill="x", padx=px(14), before=marco_trans)
            flecha.configure(text="▾  Ajustes")

    for w in (fila_ajustes, flecha, resumen):
        w.bind("<Button-1>", alternar_ajustes)

    # --- transcripcion en vivo -------------------------------------------
    marco_trans.pack(fill="x", padx=px(14), pady=(px(12), px(14)))
    tk.Frame(marco_trans, bg=C["linea"], height=1).pack(fill="x", pady=(px(0), px(8)))
    tk.Label(marco_trans, text="Transcripción", bg=C["fondo"], fg=C["tenue"],
             font=FUENTE_ETIQUETA, anchor="w").pack(fill="x")
    caja_trans = tk.Text(
        marco_trans, height=5, bg=C["fondo"], fg=C["tenue"], font=FUENTE_CHICA,
        wrap="word", relief="flat", padx=px(0), pady=px(4), state="disabled",
        highlightthickness=0, borderwidth=0, cursor="arrow", spacing1=px(3),
    )
    caja_trans.pack(fill="x")
    fuente_quien = ("Segoe UI Variable Text Semibold", 9)
    caja_trans.tag_configure("quien_prospecto", foreground=C["prospecto"], font=fuente_quien)
    caja_trans.tag_configure("quien_tu", foreground=C["apagado"], font=fuente_quien)
    caja_trans.tag_configure("dice_prospecto", foreground=C["texto"])
    caja_trans.tag_configure("dice_tu", foreground=C["tenue"])

    def agregar_linea(linea: str):
        quien, _, dice = linea.partition(": ")
        tipo = "prospecto" if quien.strip().lower().startswith("prospecto") else "tu"
        caja_trans.configure(state="normal")
        caja_trans.insert("end", quien + "   ", f"quien_{tipo}")
        caja_trans.insert("end", dice + "\n", f"dice_{tipo}")
        caja_trans.see("end")
        caja_trans.configure(state="disabled")

    def pintar_estado(texto: str):
        bajo = texto.lower()
        if bajo.startswith("error") or "falló" in bajo or "sin micro" in bajo:
            color = C["coral"]
        elif bajo.startswith(("analizando", "auditando", "iniciando")):
            color = C["ambar"]
        else:
            color = C["verde"]
        punto.configure(fg=color)
        estado.configure(text=texto)

    def refrescar():
        while True:
            try:
                tipo, contenido = ui.get_nowait()
            except queue.Empty:
                break
            if tipo == "linea":
                agregar_linea(contenido)
            elif tipo == "sugerencia":
                pintar_sugerencia(contenido)
            elif tipo == "estado":
                pintar_estado(contenido)
        raiz.after(200, refrescar)

    refrescar()
    if not arrancar_nucleo:
        return SimpleNamespace(raiz=raiz, ui=ui, alternar_ajustes=alternar_ajustes,
                               abrir_negocio=abrir_negocio)
    raiz.mainloop()
    # Ventana cerrada: salir YA. Un apagado normal desmonta PortAudio mientras
    # el hilo de audio aun lee y Windows reporta un crash (access violation).
    # No se pierde nada: la transcripcion se escribe linea a linea y la
    # auditoria por Claude Code corre en un proceso aparte.
    os._exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copiloto SPIN en tiempo real")
    parser.add_argument("--consola", type=int, metavar="SEG",
                        help="modo prueba en consola durante N segundos")
    parser.add_argument("--intervalo", type=int, default=20,
                        help="segundos entre analisis SPIN (defecto: 20)")
    parser.add_argument("--cierre", action="store_true",
                        help="venta en 2 llamadas y hoy toca la de cierre")
    parser.add_argument("--una-llamada", action="store_true",
                        help="toda la venta en una sola llamada (indagar + cerrar)")
    parser.add_argument("--modelo", choices=list(MODELOS_VIVO),
                        help="cerebro del analisis en vivo: max (Opus 5, ~$1.50 "
                             "por llamada; defecto), economico (Sonnet 5, ~$0.60-0.90) "
                             "o glm (GLM-5.2 abierto via Workers AI, ~$0.50)")
    parser.add_argument("--negocio",
                        help="que negocio se vende: carpeta en negocios/ (defecto: "
                             "el ultimo usado; tambien se cambia y se edita en la "
                             "ventana 'Tu negocio')")
    parser.add_argument("--oferta", metavar="NOMBRE",
                        help="oferta a priorizar HOY (ej. 'imperio' o 'agencia'; "
                             "basta parte del nombre; tambien cambiable en la "
                             "ventana con el selector 'Vender:')")
    args = parser.parse_args()

    prefs = leer_preferencias()
    if args.una_llamada:
        modo_analisis["valor"] = "completa"
    elif args.cierre:
        modo_analisis["valor"] = "cierre"
    elif prefs.get("llamadas") == 1:
        modo_analisis["valor"] = "completa"
    modelo = args.modelo or prefs.get("modelo")
    modelo_vivo["valor"] = modelo if modelo in MODELOS_VIVO else "max"
    negocio = args.negocio or prefs.get("negocio") or NEGOCIO_DEFECTO
    if not args.negocio and negocio not in negocios_disponibles():
        negocio = NEGOCIO_DEFECTO  # la carpeta recordada ya no existe
    if negocio != prompts_activos["nombre"]:
        prompts_activos = cargar_prompts(negocio)
    if args.oferta:
        buscada = args.oferta.strip().lower()
        oferta_objetivo["valor"] = next(
            (o for o in prompts_activos.get("ofertas") or [] if buscada in o.lower()),
            args.oferta.strip(),
        )
    if args.consola:
        asyncio.run(modo_consola(args.consola, args.intervalo))
    else:
        modo_ventana(args.intervalo)
