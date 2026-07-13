"""Valida el API key de Deepgram transcribiendo el WAV capturado (espanol)."""

import json
import urllib.request

from config import DEEPGRAM_API_KEY

URL = "https://api.deepgram.com/v1/listen?model=nova-2&language=es&smart_format=true"

with open("prueba_captura.wav", "rb") as f:
    audio = f.read()

req = urllib.request.Request(
    URL,
    data=audio,
    headers={
        "Authorization": f"Token {DEEPGRAM_API_KEY}",
        "Content-Type": "audio/wav",
    },
    method="POST",
)

with urllib.request.urlopen(req, timeout=60) as resp:
    resultado = json.load(resp)

alternativa = resultado["results"]["channels"][0]["alternatives"][0]
print("Transcripcion:", alternativa["transcript"] or "(vacia - el audio no tenia voz)")
print("Confianza:", round(alternativa["confidence"], 3))
