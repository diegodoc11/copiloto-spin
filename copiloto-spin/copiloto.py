"""
Copiloto SPIN — asistente de ventas en tiempo real.

Escucha la llamada (voz del prospecto por el audio del sistema + tu voz por
el microfono), transcribe en vivo con Deepgram y cada ~25 segundos le pide
a Claude las siguientes preguntas SPIN, mostradas en una ventana flotante.

Tiene dos modos (Diego vende a dos llamadas), intercambiables en la ventana:
  - SPIN (defecto): primera llamada, indagacion de dolores e implicaciones.
  - Cierre: segunda llamada — oferta, precio y manejo de objeciones.

Uso:
  python copiloto.py                        # ventana flotante (uso real en llamadas)
  python copiloto.py --cierre               # arranca directo en modo cierre
  python copiloto.py --modelo economico     # cerebro GLM-5.2 (abierto, via Workers AI)
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
import subprocess
import threading
import time
import tomllib
import warnings
from pathlib import Path

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
from prompt_spin import NEGOCIO_DEFECTO, cargar_prompts

CHUNK_MS = 100
MODELO = "claude-opus-4-8"
CARPETA_LLAMADAS = Path(__file__).parent / "llamadas"
# Raiz del proyecto (donde vive .claude/skills/auditar-llamada): claude -p debe
# correr desde ahi para que encuentre la skill.
RAIZ_PROYECTO = Path(__file__).parent.parent

# Tiers del analisis EN VIVO (A/B con llamadas reales del 2026-07-11, ver
# ab_modelos.md): max = Opus 4.8 (el mejor, ~$1.50/llamada de 30 min);
# premium = Sonnet 5 sin razonamiento (casi Opus, ~$0.90); economico = GLM-5.2
# (abierto MIT via Workers AI, empata con Sonnet en el prompt SPIN, ~$0.50).
MODELOS_VIVO = {
    "max": {"id": "claude-opus-4-8", "etiqueta": "Máx · Opus 4.8"},
    "premium": {"id": "claude-sonnet-5", "etiqueta": "Premium · Sonnet 5"},
    "economico": {"id": None, "etiqueta": "Económico · GLM-5.2"},
}

# Workers AI (tier economico): si no hay CLOUDFLARE_API_TOKEN en .env se usa el
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
# Prompts del negocio activo ({"nombre","spin","cierre","auditoria"}); el
# flag --negocio los recarga en el arranque. Defecto: imperio (el de Diego).
prompts_activos = cargar_prompts()
# Modo del asesor: "spin" (1a llamada, indagacion) o "cierre" (2a llamada,
# oferta/precio/objeciones). Dict compartido entre el hilo de tkinter y el
# de asyncio; el asesor lo lee en cada analisis, se puede cambiar en vivo.
modo_analisis = {"valor": "spin"}
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
    if tier == "economico":
        return await _analizar_glm(prompt, texto)
    # Sonnet 5 piensa por defecto y el razonamiento se comeria el max_tokens
    # (devolveria vacio); para el loop en vivo va sin razonamiento. Opus 4.8
    # sin el parametro ya corre sin pensar.
    extra = {"thinking": {"type": "disabled"}} if tier == "premium" else {}
    respuesta = await client.messages.create(
        model=MODELOS_VIVO[tier]["id"],
        max_tokens=800,
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

        prompt = (
            prompts_activos["cierre" if modo_analisis["valor"] == "cierre" else "spin"]
            + _bloque_oferta()
        )
        tier = modelo_vivo["valor"]
        etiqueta = MODELOS_VIVO[tier]["etiqueta"]
        ui.put(("estado", f"Analizando con {etiqueta}..."))
        try:
            texto = await analizar_en_vivo(client, tier, prompt, transcript.formatear())
            ui.put(("sugerencia", texto))
            ui.put(("estado", f"Sugerencia de {etiqueta}: {time.strftime('%H:%M:%S')}"))
        except Exception as e:  # noqa: BLE001 — mostrar en UI y dejar rastro
            _log_error(f"Analisis en vivo ({tier}) fallo: {e}")
            ui.put(("estado", f"Error al analizar ({etiqueta}): {e}"))


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


def modo_ventana(intervalo: int) -> None:
    import tkinter as tk

    transcript = Transcript()
    ui: queue.Queue = queue.Queue()

    threading.Thread(
        target=lambda: asyncio.run(nucleo(transcript, ui, intervalo)),
        daemon=True,
    ).start()

    raiz = tk.Tk()
    raiz.title(f"Copiloto SPIN — {prompts_activos['nombre']}")
    raiz.geometry("440x720+40+40")
    raiz.configure(bg="#1a1a2e")
    raiz.attributes("-topmost", True)

    estado = tk.Label(
        raiz, text="Iniciando...", bg="#1a1a2e", fg="#8888aa",
        font=("Segoe UI", 9), anchor="w",
    )
    estado.pack(fill="x", padx=10, pady=(8, 0))

    TITULOS = {"spin": "SUGERENCIAS SPIN", "cierre": "SUGERENCIAS DE CIERRE"}
    modo_var = tk.StringVar(value=modo_analisis["valor"])

    def cambiar_modo():
        modo_analisis["valor"] = modo_var.get()
        titulo.configure(text=TITULOS[modo_var.get()])
        forzar_analisis.set()  # re-analiza ya con el cerebro del modo nuevo

    marco_modo = tk.Frame(raiz, bg="#1a1a2e")
    marco_modo.pack(fill="x", padx=10, pady=(6, 0))
    for texto, valor in (("1ª llamada (SPIN)", "spin"), ("Cierre (oferta)", "cierre")):
        tk.Radiobutton(
            marco_modo, text=texto, value=valor, variable=modo_var,
            command=cambiar_modo, bg="#1a1a2e", fg="#ffffff",
            selectcolor="#16213e", activebackground="#1a1a2e",
            activeforeground="#ffffff", font=("Segoe UI", 9), anchor="w",
        ).pack(side="left", expand=True, fill="x")

    # Selector del modelo (cambiable en vivo; aplica desde el siguiente
    # analisis — no fuerza uno para no gastar de mas).
    modelo_var = tk.StringVar(value=modelo_vivo["valor"])

    def cambiar_modelo():
        modelo_vivo["valor"] = modelo_var.get()

    marco_modelo = tk.Frame(raiz, bg="#1a1a2e")
    marco_modelo.pack(fill="x", padx=10, pady=(2, 0))
    tk.Label(
        marco_modelo, text="Modelo:", bg="#1a1a2e", fg="#8888aa",
        font=("Segoe UI", 9),
    ).pack(side="left")
    for texto, valor in (
        ("Económico", "economico"), ("Premium", "premium"), ("Máx", "max"),
    ):
        tk.Radiobutton(
            marco_modelo, text=texto, value=valor, variable=modelo_var,
            command=cambiar_modelo, bg="#1a1a2e", fg="#ffffff",
            selectcolor="#16213e", activebackground="#1a1a2e",
            activeforeground="#ffffff", font=("Segoe UI", 9), anchor="w",
        ).pack(side="left", expand=True, fill="x")

    # Selector de la oferta a vender HOY (cambiable en vivo; fuerza re-analisis
    # para que las sugerencias apunten ya a la oferta elegida).
    oferta_var = tk.StringVar(value=oferta_objetivo["valor"])

    def cambiar_oferta():
        oferta_objetivo["valor"] = oferta_var.get()
        forzar_analisis.set()

    ofertas_ui = prompts_activos.get("ofertas") or []
    if ofertas_ui:
        marco_oferta = tk.Frame(raiz, bg="#1a1a2e")
        marco_oferta.pack(fill="x", padx=10, pady=(2, 0))
        tk.Label(
            marco_oferta, text="Vender:", bg="#1a1a2e", fg="#8888aa",
            font=("Segoe UI", 9),
        ).pack(side="left")
        for texto, valor in [("Auto", "")] + [
            (o.capitalize(), o) for o in ofertas_ui
        ]:
            tk.Radiobutton(
                marco_oferta, text=texto, value=valor, variable=oferta_var,
                command=cambiar_oferta, bg="#1a1a2e", fg="#ffffff",
                selectcolor="#16213e", activebackground="#1a1a2e",
                activeforeground="#ffffff", font=("Segoe UI", 9), anchor="w",
            ).pack(side="left", expand=True, fill="x")

    titulo = tk.Label(
        raiz, text=TITULOS[modo_analisis["valor"]], bg="#1a1a2e", fg="#e94560",
        font=("Segoe UI", 10, "bold"), anchor="w",
    )
    titulo.pack(fill="x", padx=10, pady=(8, 0))
    sugerencias = tk.Text(
        raiz, height=15, bg="#16213e", fg="#ffffff", font=("Segoe UI", 11),
        wrap="word", relief="flat", padx=10, pady=8, state="disabled",
    )
    sugerencias.pack(fill="both", expand=True, padx=10, pady=(4, 8))

    tk.Button(
        raiz, text="Analizar ahora", command=forzar_analisis.set,
        bg="#e94560", fg="white", relief="flat", font=("Segoe UI", 10, "bold"),
    ).pack(fill="x", padx=10, pady=(0, 6))

    tk.Button(
        raiz, text="Auditar llamada completa (al terminar)",
        command=solicitar_auditoria.set,
        bg="#0f7d5c", fg="white", relief="flat", font=("Segoe UI", 10, "bold"),
    ).pack(fill="x", padx=10, pady=(0, 8))

    tk.Label(
        raiz, text="TRANSCRIPCION", bg="#1a1a2e", fg="#8888aa",
        font=("Segoe UI", 9, "bold"), anchor="w",
    ).pack(fill="x", padx=10)
    caja_trans = tk.Text(
        raiz, height=8, bg="#0f1626", fg="#9999aa", font=("Segoe UI", 9),
        wrap="word", relief="flat", padx=8, pady=6, state="disabled",
    )
    caja_trans.pack(fill="x", padx=10, pady=(4, 10))

    def escribir(widget, texto, reemplazar=False):
        widget.configure(state="normal")
        if reemplazar:
            widget.delete("1.0", "end")
        widget.insert("end", texto)
        widget.see("end")
        widget.configure(state="disabled")

    def refrescar():
        while True:
            try:
                tipo, contenido = ui.get_nowait()
            except queue.Empty:
                break
            if tipo == "linea":
                escribir(caja_trans, contenido + "\n")
            elif tipo == "sugerencia":
                escribir(sugerencias, contenido, reemplazar=True)
            elif tipo == "estado":
                estado.configure(text=contenido)
        raiz.after(200, refrescar)

    refrescar()
    raiz.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copiloto SPIN en tiempo real")
    parser.add_argument("--consola", type=int, metavar="SEG",
                        help="modo prueba en consola durante N segundos")
    parser.add_argument("--intervalo", type=int, default=20,
                        help="segundos entre analisis SPIN (defecto: 20)")
    parser.add_argument("--cierre", action="store_true",
                        help="arranca en modo llamada de cierre (oferta/objeciones)")
    parser.add_argument("--modelo", choices=list(MODELOS_VIVO), default="max",
                        help="cerebro del analisis en vivo: economico (GLM-5.2 "
                             "abierto, ~$0.50/llamada), premium (Sonnet 5, "
                             "~$0.90) o max (Opus 4.8, ~$1.50; defecto)")
    parser.add_argument("--negocio", default=NEGOCIO_DEFECTO,
                        help="que negocio se vende: carpeta en negocios/ con su "
                             "negocio.md y objeciones.md (defecto: imperio)")
    parser.add_argument("--oferta", metavar="NOMBRE",
                        help="oferta a priorizar HOY (ej. 'imperio' o 'agencia'; "
                             "basta parte del nombre; tambien cambiable en la "
                             "ventana con el selector 'Vender:')")
    args = parser.parse_args()

    if args.cierre:
        modo_analisis["valor"] = "cierre"
    modelo_vivo["valor"] = args.modelo
    if args.negocio != prompts_activos["nombre"]:
        prompts_activos = cargar_prompts(args.negocio)
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
