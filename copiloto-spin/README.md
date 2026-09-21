# Copiloto SPIN

Asistente de ventas en tiempo real: escucha tu videollamada (Zoom, Meet o
cualquier plataforma), transcribe en vivo lo que dice el prospecto (y tú),
y cada ~20 segundos Claude sugiere qué decir a continuación en una ventana
flotante que solo tú ves.

Tiene **dos modos** (uno por cada llamada del proceso de venta), intercambiables
con el selector de arriba de la ventana en cualquier momento:

- **1ª llamada (SPIN)** — modo por defecto: indagación de dolores, implicaciones
  y sueño según SPIN Selling.
- **Cierre (oferta)** — segunda llamada: reconexión del dolor, presentación de
  la oferta, anclaje y entrega del precio, manejo de objeciones del catálogo y
  cierre con compromiso concreto. Detecta señales de compra y te avisa cuándo
  dejar de presentar y cerrar.

## Uso en llamadas reales

```
python copiloto.py                        # arranca como lo dejaste la última vez
python copiloto.py --cierre               # venta en 2 llamadas, hoy toca la de cierre
python copiloto.py --una-llamada          # toda la venta en una sola llamada
python copiloto.py --modelo economico     # cerebro: economico | max
python copiloto.py --negocio demo-boletas # vender OTRO negocio (ver negocios/)
python copiloto.py --oferta imperio       # fijar la oferta a vender HOY
```

**Multi-negocio:** cada negocio vive en `negocios/<nombre>/` con su
`negocio.md` (qué se vende, avatares, prueba social, reglas de voz) y su
`objeciones.md`. Se crean y editan desde la ventana: botón **Tu negocio** →
*Nuevo negocio…* → pegar el material del negocio (o adjuntar PDF/TXT) →
**Organizar con IA** → revisar → **Guardar y usar en la llamada** (el copiloto
lo toma sin reiniciar y lo recuerda). La pestaña *Prospecto de hoy* edita el
`contexto_llamada.md`. El de Diego es `negocios/imperio/` (defecto).

**Cómo vendes** (arriba en la ventana; se recuerda en `preferencias.json`):
*1 llamada* (indaga + presenta + precio + cierre en la misma llamada) o
*2 llamadas* (eliges si hoy toca la 1ª de indagación o la 2ª de cierre).

**Selector de modelo** (en Ajustes, cambiable en vivo): Máx = Opus 5 (~$1,50
por llamada de 30 min, defecto) · Económico = Sonnet 5 (~$0,60-0,90). Los dos
con la misma clave de Anthropic. GLM-5.2 abierto (~$0,50, exige Workers Paid
en Cloudflare) quedó solo por flag: `--modelo glm`.

**Selector "Vender:"** (en la ventana, cambiable en vivo): fija qué oferta del
negocio priorizar HOY (las lee de los encabezados `## Oferta N — NOMBRE` del
`negocio.md`). Con una oferta fijada, todas las sugerencias empujan hacia ella
con prioridad absoluta (solo sugiere pivotear si el prospecto no califica o
pide la otra); en **Auto** el modelo decide según el contexto. Cambiarlo fuerza
un re-análisis inmediato. También por consola: `--oferta imperio` (basta parte
del nombre).

Se abre una ventana flotante siempre visible. Antes de la llamada:

1. Usa **audífonos** (así tu micrófono no capta la voz del prospecto y no se duplica).
2. Nunca compartas la pantalla completa — comparte solo la ventana de tu presentación.
3. Buena práctica: avisa al prospecto que grabas la llamada para tomar notas.

Botón **"Analizar ahora"**: fuerza un análisis sin esperar el intervalo (también
sirve para pedir sugerencias frescas justo después de cambiar de modo).

### Antes de cada llamada: `contexto_llamada.md`

Edita `contexto_llamada.md` con la info del prospecto de HOY (respuestas del
formulario, avatar probable, qué oferta priorizar). El copiloto lo carga al
arrancar con **prioridad máxima** sobre las reglas generales. Si no hay
contexto previo, vacía el archivo o bórralo y el copiloto funciona genérico.
**Reinicia el copiloto después de editarlo** (se carga solo al arrancar).

**Antes de una llamada de CIERRE**: llena además la sección "Para la llamada de
CIERRE" de ese archivo (dolores confirmados en la 1ª llamada, sueño, objeciones
que ya salieron y el precio que vas a presentar). El modo Cierre la usa para la
reconexión y para defender el precio.

## Pruebas

```
python copiloto.py --consola 45        # prueba de 45s en consola (pon un video sonando)
python test_captura.py                 # prueba solo la captura de audio
python transcripcion_vivo.py 20        # prueba solo la transcripción en vivo
python test_claude.py                  # prueba solo el API de Claude
python test_cierre.py                  # prueba E2E del modo cierre (transcripción simulada)
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `copiloto.py` | La aplicación completa (ventana + audio + Deepgram + IA) |
| `prompt_spin.py` | El MÉTODO universal (SPIN, cierre, auditoría) + carga del negocio |
| `ventana_negocio.py` | Ventana "Tu negocio": cargar/editar lo que vendes, con organizador por IA |
| `test_ventana.py` | Vista previa de las ventanas con datos de ejemplo (sin audio ni API) |
| `negocios/<nombre>/` | El NEGOCIO: `negocio.md` + `objeciones.md` (+ `contexto_llamada.md`) |
| `negocios/_plantilla/` | Plantilla para crear un negocio nuevo |
| `contexto_llamada.md` | Info del prospecto de HOY (flujo clásico; editar antes de cada llamada) |
| `llamadas/` | Transcripciones (`llamada_*.txt`) y auditorías (`auditoria_*.md`) |
| `config.py` | Carga las claves desde `.env` |
| `.env` | API keys (Deepgram, Anthropic y opcional Cloudflare). **Nunca compartir.** |

## Costos aproximados

- Deepgram: ~$0.92/hora de llamada (2 streams). Crédito inicial: $200 gratis.
- Claude Máx (Opus 5): ~$3/hora de llamada con análisis cada 20s; Económico
  (Sonnet 5): ~$1.20-1.80/hora.

## Próximas mejoras (pendientes)

- [ ] Probar el modo cierre en una llamada real y pulir el prompt con lo que salga
- [ ] Feedback de Diego tras usar ambos modos en llamadas reales
