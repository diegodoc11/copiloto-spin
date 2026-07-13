# Copiloto SPIN — asistente de ventas en tiempo real 🎧🤖

Escucha tu videollamada de ventas (Zoom, Meet o cualquier plataforma),
transcribe en vivo lo que dicen tú y el prospecto, y **Claude te sugiere en una
ventana flotante —que solo tú ves— exactamente qué preguntar o decir a
continuación** según el método SPIN Selling. Tiene modo de 1ª llamada
(indagación), modo de cierre (oferta, precio y objeciones) y auditoría completa
post-llamada.

> Esta es una **versión beta de prueba**: cada persona usa **sus propias claves
> de API** (instrucciones abajo). Nada de lo que hables sale de tu computador
> salvo hacia Deepgram (transcripción) y Anthropic (sugerencias).

## Requisitos

- **Windows 10 u 11** (la captura de audio usa tecnología exclusiva de Windows).
- **Python 3.12** — exactamente 3.12, ni 3.13 ni anteriores
  ([descargar aquí](https://www.python.org/downloads/release/python-3129/);
  al instalar, marca la casilla **"Add python.exe to PATH"**).
- **Audífonos** para las llamadas (evita ecos y voces duplicadas).
- Dos claves de API **tuyas** (gratis de crear, pasos abajo).

## Instalación (una sola vez, ~10 minutos)

**1. Descarga el proyecto.** Botón verde **Code → Download ZIP** aquí en GitHub
y descomprímelo donde quieras (o `git clone` si sabes usarlo).

**2. Instala las dependencias.** Abre una terminal (PowerShell) en la carpeta
del proyecto y corre:

```
cd copiloto-spin
pip install -r requirements.txt
```

**3. Crea tus claves de API** (cada persona usa las suyas):

- **Deepgram** (transcripción): crea cuenta gratis en
  [console.deepgram.com](https://console.deepgram.com) — te regalan **$200 de
  crédito** (alcanza para ~200 horas de llamadas). Luego *API Keys → Create Key*
  y copia la clave.
- **Anthropic / Claude** (el cerebro): crea cuenta en
  [console.anthropic.com](https://console.anthropic.com), ve a *API Keys →
  Create Key* y copia la clave. ⚠️ Necesitas **cargar créditos** en *Plans &
  Billing*: con **$5–10 USD** alcanza para varias llamadas (~$1.50 por llamada
  de 30 minutos).

**4. Pega tus claves.** En la carpeta `copiloto-spin/`, copia el archivo
`.env.example`, renombra la copia a `.env` (exactamente así, con el punto) y
ábrelo con el Bloc de notas para pegar tus dos claves. **El `.env` es personal:
no lo compartas con nadie.**

**5. Prueba que todo funciona.** Pon un video en YouTube sonando y corre:

```
python copiloto.py --consola 45
```

Si en ~45 segundos ves la transcripción del video y una sugerencia de Claude,
estás listo. 🎉

## Uso en llamadas reales

```
cd copiloto-spin
python copiloto.py                 # 1ª llamada (indagación SPIN)
python copiloto.py --cierre        # llamada de cierre (oferta y precio)
```

Se abre la ventana flotante siempre visible. Reglas de oro:

1. Usa **audífonos** (si no, tu micrófono capta la voz del prospecto y se duplica).
2. **Nunca compartas la pantalla completa** — comparte solo la ventana de tu
   presentación, nunca la del copiloto.
3. Buena práctica: avisa al prospecto que grabas la llamada para tomar notas.

La guía completa de uso (modos, selector de modelo, botón "Analizar ahora",
contexto del prospecto del día) está en
[`copiloto-spin/README.md`](copiloto-spin/README.md).

## Configurar TU negocio (para alumnos con oferta propia)

El copiloto viene configurado por defecto con la oferta de Imperio (Diego).
Si vendes otra cosa:

1. Copia la carpeta `copiloto-spin/negocios/_plantilla/` y renómbrala con el
   nombre de tu negocio (ej.: `negocios/mi-agencia/`).
2. Llena `negocio.md` (qué vendes, precios, avatares, prueba social) y
   `objeciones.md` (tus objeciones con sus respuestas).
3. Arranca con: `python copiloto.py --negocio mi-agencia`

Hay un ejemplo completo ya lleno en `negocios/demo-boletas/`.

## Auditoría post-llamada

Al terminar una llamada tienes dos caminos:

- **Con Claude Code** (recomendado, no gasta créditos de API si tienes
  suscripción de Claude): abre Claude Code en la carpeta del proyecto y escribe
  `/auditar-llamada`. Genera un informe de cerrador experto: nota por fases,
  errores con citas y plan para la próxima llamada.
- **Botón verde "Auditar llamada completa"** de la ventana: hace lo mismo vía
  API (tarda varios minutos y consume ~$1-2 de tus créditos de Anthropic).

Las transcripciones y auditorías se guardan solas en `copiloto-spin/llamadas/`
(solo en tu computador; nunca se suben a este repositorio).

## Costos aproximados por hora de llamada

| Servicio | Costo | Nota |
|---|---|---|
| Deepgram (transcripción) | ~$0.92/hora | los $200 gratis dan ~200 horas |
| Claude modelo **max** (Opus) | ~$3/hora | el mejor; es el defecto |
| Claude modelo **premium** (Sonnet) | ~$1.80/hora | casi igual de bueno |

El modelo se cambia en vivo con el selector de la ventana o con
`--modelo premium`.

## Problemas comunes

- **"Falta DEEPGRAM_API_KEY en el archivo .env"** → el archivo se llama `.env`
  (no `env.txt` ni `.env.example`) y está dentro de `copiloto-spin/`.
- **Error de Anthropic "credit balance is too low"** → carga créditos en
  console.anthropic.com → *Plans & Billing*.
- **No transcribe nada** → verifica que el audio de la llamada esté sonando por
  la salida de audio predeterminada de Windows y habla cerca del micrófono.
- **Acentos raros (`�`) en la consola** → es solo cosmético de PowerShell; los
  datos van bien.
