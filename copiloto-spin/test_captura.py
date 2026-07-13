"""
Prueba de captura de audio del sistema (WASAPI loopback).

Graba 5 segundos de TODO lo que suena por tus parlantes/audifonos
(la voz del prospecto en una llamada) y lo guarda en un WAV.
Para probar de verdad: pon un video de YouTube sonando y ejecuta este script.
"""

import time
import wave

import pyaudiowpatch as pyaudio

DURACION_SEG = 5
ARCHIVO_SALIDA = "prueba_captura.wav"


def encontrar_loopback(p: "pyaudio.PyAudio") -> dict:
    """Devuelve el dispositivo loopback correspondiente a la salida de audio por defecto."""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    salida = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

    if salida.get("isLoopbackDevice"):
        return salida

    for loopback in p.get_loopback_device_info_generator():
        if salida["name"] in loopback["name"]:
            return loopback

    raise SystemExit(
        f"No se encontro dispositivo loopback para la salida: {salida['name']}"
    )


def main() -> None:
    with pyaudio.PyAudio() as p:
        print("Dispositivos loopback disponibles:")
        for dev in p.get_loopback_device_info_generator():
            print(f"  [{dev['index']}] {dev['name']}")

        dispositivo = encontrar_loopback(p)
        rate = int(dispositivo["defaultSampleRate"])
        canales = dispositivo["maxInputChannels"]
        print(f"\nCapturando desde: {dispositivo['name']}")
        print(f"  Sample rate: {rate} Hz | Canales: {canales}")

        with wave.open(ARCHIVO_SALIDA, "wb") as wf:
            wf.setnchannels(canales)
            wf.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(rate)

            def callback(in_data, frame_count, time_info, status):
                wf.writeframes(in_data)
                return (in_data, pyaudio.paContinue)

            with p.open(
                format=pyaudio.paInt16,
                channels=canales,
                rate=rate,
                frames_per_buffer=512,
                input=True,
                input_device_index=dispositivo["index"],
                stream_callback=callback,
            ):
                print(f"\nGrabando {DURACION_SEG}s del audio del sistema...")
                print("(si hay musica o un video sonando, quedara en el WAV)")
                time.sleep(DURACION_SEG)

        print(f"\nListo: audio guardado en {ARCHIVO_SALIDA}")


if __name__ == "__main__":
    main()
