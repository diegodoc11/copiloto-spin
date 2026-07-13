"""
Transcripcion en vivo del audio del sistema con Deepgram (espanol).

Captura lo que suena por tus parlantes/audifonos (la voz del prospecto)
y lo convierte en texto en tiempo real.

Uso:  python transcripcion_vivo.py [segundos]   (por defecto 20)
"""

import asyncio
import audioop
import json
import sys
import time

import pyaudiowpatch as pyaudio
import websockets

from config import DEEPGRAM_API_KEY

CHUNK_MS = 100  # tamano de cada paquete de audio enviado


def encontrar_loopback(p: "pyaudio.PyAudio") -> dict:
    """Dispositivo loopback correspondiente a la salida de audio por defecto."""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    salida = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if salida.get("isLoopbackDevice"):
        return salida
    for loopback in p.get_loopback_device_info_generator():
        if salida["name"] in loopback["name"]:
            return loopback
    raise SystemExit(f"No se encontro loopback para: {salida['name']}")


async def emisor(ws, stream, frames_por_chunk: int, canales: int) -> None:
    """Lee audio del sistema y lo envia al websocket de Deepgram."""
    loop = asyncio.get_running_loop()
    while True:
        datos = await loop.run_in_executor(
            None, lambda: stream.read(frames_por_chunk, exception_on_overflow=False)
        )
        if canales == 2:
            datos = audioop.tomono(datos, 2, 0.5, 0.5)
        await ws.send(datos)


async def receptor(ws) -> None:
    """Imprime cada frase final que devuelve Deepgram."""
    async for mensaje in ws:
        msg = json.loads(mensaje)
        if msg.get("type") != "Results":
            continue
        texto = msg["channel"]["alternatives"][0]["transcript"].strip()
        if texto and msg.get("is_final"):
            print(f"[{time.strftime('%H:%M:%S')}] {texto}", flush=True)


async def main(duracion: int) -> None:
    with pyaudio.PyAudio() as p:
        dispositivo = encontrar_loopback(p)
        rate = int(dispositivo["defaultSampleRate"])
        canales = dispositivo["maxInputChannels"]
        frames_por_chunk = int(rate * CHUNK_MS / 1000)

        url = (
            "wss://api.deepgram.com/v1/listen"
            "?model=nova-2&language=es&smart_format=true&interim_results=false"
            f"&encoding=linear16&sample_rate={rate}&channels=1"
        )

        stream = p.open(
            format=pyaudio.paInt16,
            channels=canales,
            rate=rate,
            frames_per_buffer=frames_por_chunk,
            input=True,
            input_device_index=dispositivo["index"],
        )
        try:
            async with websockets.connect(
                url,
                additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
            ) as ws:
                print(f"Escuchando: {dispositivo['name']}")
                print(f"Transcribiendo en vivo durante {duracion}s...\n")
                tarea_emisor = asyncio.create_task(
                    emisor(ws, stream, frames_por_chunk, canales)
                )
                tarea_receptor = asyncio.create_task(receptor(ws))

                await asyncio.sleep(duracion)

                tarea_emisor.cancel()
                await ws.send(json.dumps({"type": "CloseStream"}))
                try:
                    await asyncio.wait_for(tarea_receptor, timeout=5)
                except asyncio.TimeoutError:
                    tarea_receptor.cancel()
        finally:
            stream.stop_stream()
            stream.close()

    print("\nFin de la prueba.")


if __name__ == "__main__":
    segundos = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    try:
        asyncio.run(main(segundos))
    except KeyboardInterrupt:
        print("\nDetenido.")
