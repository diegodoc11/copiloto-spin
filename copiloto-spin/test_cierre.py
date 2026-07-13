"""Prueba E2E del modo CIERRE con una transcripcion simulada de segunda llamada.

Simula el momento critico: Diego dio el precio y el prospecto suelta la
objecion de dinero + la del socio. Valida que el copiloto detecte el momento,
la objecion (con respuesta del catalogo adaptada) y sugiera cierres, no pitch.

Correr despues de recargar creditos:  python test_cierre.py
"""

import anthropic

from config import ANTHROPIC_API_KEY
from prompt_spin import PROMPT_CIERRE

TRANSCRIPCION_SIMULADA = """
[10:02:11] Tú: Bueno, la vez pasada me contabas que invertiste en anuncios y te fue mal, que perdiste como mil dólares con un freelancer. ¿Sigue igual eso?
[10:02:25] Prospecto: Sí, tal cual, ahí quedó eso. Por eso te digo que me da miedo volver a meterle plata a publicidad.
[10:03:02] Tú: Te entiendo. Por eso esta vez la idea es que no lo hagas tú solo. Nosotros montamos todo el sistema completo, los anuncios, la página, el seguimiento de los clientes por WhatsApp.
[10:04:40] Prospecto: ¿Y eso cuánto valdría?
[10:05:01] Tú: El servicio completo son mil quinientos dólares al mes, más el presupuesto de anuncios que ya me dijiste que puedes conseguir.
[10:05:20] Prospecto: Uy no, es que ahorita mil quinientos al mes se me hace mucho. Está caro.
[10:05:35] Prospecto: Además eso lo tendría que hablar con mi esposa, porque es una plata grande.
"""

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

respuesta = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=800,
    system=[
        {
            "type": "text",
            "text": PROMPT_CIERRE,
            "cache_control": {"type": "ephemeral"},
        }
    ],
    messages=[
        {
            "role": "user",
            "content": "Transcripcion de la llamada hasta ahora:\n\n"
            + TRANSCRIPCION_SIMULADA,
        }
    ],
)

print(next(b.text for b in respuesta.content if b.type == "text"))
print("\n--- uso:", respuesta.usage)
