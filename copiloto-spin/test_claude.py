"""Valida el API key de Anthropic con una llamada minima."""

import anthropic

from config import ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

respuesta = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=64,
    messages=[{"role": "user", "content": "Responde solo con: OK, copiloto SPIN listo."}],
)

print(next(b.text for b in respuesta.content if b.type == "text"))
