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

**1. Instala Python 3.12** si no lo tienes
([descargar aquí](https://www.python.org/downloads/release/python-3129/) →
*Windows installer (64-bit)*). Al instalar, marca la casilla **"Add python.exe
to PATH"**.

**2. Descarga el proyecto.** Botón verde **Code → Download ZIP** aquí en GitHub
y descomprímelo donde quieras (por ejemplo en Documentos).

**3. Crea tus dos claves de API** (cada persona usa las suyas):

- **Deepgram** (transcripción): crea cuenta gratis en
  [console.deepgram.com](https://console.deepgram.com) — te regalan **$200 de
  crédito** (alcanza para ~200 horas de llamadas). Luego *API Keys → Create Key*
  y copia la clave.
- **Anthropic / Claude** (el cerebro): crea cuenta en
  [console.anthropic.com](https://console.anthropic.com), ve a *API Keys →
  Create Key* y copia la clave. ⚠️ Necesitas **cargar créditos** en *Plans &
  Billing*: con **$5–10 USD** alcanza para varias llamadas (~$1.50 por llamada
  de 30 minutos).

**4. Doble clic en `INSTALAR.bat`.** Instala lo necesario y te abre el archivo
de claves en el Bloc de notas: pega tus dos claves donde dice
`pega_aqui_tu_clave...`, guarda y cierra. **Ese archivo (`.env`) es personal:
no lo compartas con nadie.**

**5. Doble clic en `ABRIR COPILOTO.bat`.** Se abre la ventana flotante. Para
probar sin estar en una llamada, pon un video de YouTube con alguien hablando:
en unos segundos ves la transcripción y, a los ~25 segundos, la primera
sugerencia. 🎉

> Si Windows muestra un aviso azul "Windows protegió su PC" al abrir un `.bat`,
> es normal en archivos descargados: *Más información → Ejecutar de todas formas*.

<details>
<summary>Instalación manual por terminal (si prefieres)</summary>

```
cd copiloto-spin
pip install -r requirements.txt
copy .env.example .env      # y pega tus claves en .env
python copiloto.py --consola 45   # prueba en consola con un video sonando
```
</details>

## Uso en llamadas reales

Doble clic en **`ABRIR COPILOTO.bat`** (o `python copiloto.py` dentro de
`copiloto-spin/`). Se abre la ventana flotante siempre visible. Arriba le dices **cómo vendes**
(queda guardado para la próxima vez):

- **Vendo en 1 llamada:** indagas, presentas, das el precio y cierras en la
  misma llamada. El copiloto te lleva por todo el recorrido.
- **Vendo en 2 llamadas:** marcas cuál toca hoy — *1ª: indagar* (preguntas
  SPIN) o *2ª: cerrar la venta* (oferta, precio y objeciones).

Reglas de oro:

1. Usa **audífonos** (si no, tu micrófono capta la voz del prospecto y se duplica).
2. **Nunca compartas la pantalla completa** — comparte solo la ventana de tu
   presentación, nunca la del copiloto.
3. Buena práctica: avisa al prospecto que grabas la llamada para tomar notas.

La guía completa de uso (modos, selector de modelo, botón "Analizar ahora",
contexto del prospecto del día) está en
[`copiloto-spin/README.md`](copiloto-spin/README.md).

## Configurar TU negocio (para alumnos con oferta propia)

El copiloto viene configurado por defecto con la oferta de Imperio (Diego).
Si vendes otra cosa, se lo cargas desde la misma ventana, sin tocar carpetas:

1. Pulsa el botón **Tu negocio** (arriba a la derecha) → menú del nombre →
   **Nuevo negocio…** y ponle nombre.
2. **Pega todo lo que tengas** de tu negocio, en cualquier orden: el texto de
   tu página, el guion de tu video de ventas, precios, testimonios, chats con
   clientes, las objeciones que te ponen. También puedes adjuntar PDF o TXT.
3. Pulsa **Organizar con IA** (1 a 3 minutos, unos centavos de tu clave de
   Anthropic). La IA arma dos documentos: *Lo que vendo* y *Objeciones*. Lo
   que no encontró lo marca como `[FALTA: …]` para que lo completes.
4. Revisa, corrige lo que quieras y pulsa **Guardar y usar en la llamada**. El
   copiloto empieza a vender ese negocio de inmediato y lo recuerda.

En la pestaña **Prospecto de hoy** pegas lo que sepas de la persona de la
llamada (formulario, chat previo); eso manda sobre todo lo demás.

Si prefieres llenarlo a mano, cada pestaña trae una guía. Hay un ejemplo
completo en `negocios/demo-boletas/`. Todo se guarda en
`copiloto-spin/negocios/<tu-negocio>/`.

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
| Claude modelo **Máx** (Opus 5) | ~$3/hora | el mejor; es el defecto |
| Claude modelo **Económico** (Sonnet 5) | ~$1.20-1.80/hora | casi igual de bueno |

Los dos usan la misma clave de Anthropic. El modelo se cambia en vivo en
**Ajustes** (abajo en la ventana) o con `--modelo economico`.

## Problemas comunes

- **"Falta DEEPGRAM_API_KEY en el archivo .env"** → el archivo se llama `.env`
  (no `env.txt` ni `.env.example`) y está dentro de `copiloto-spin/`.
- **Error de Anthropic "credit balance is too low"** → carga créditos en
  console.anthropic.com → *Plans & Billing*.
- **No transcribe nada** → verifica que el audio de la llamada esté sonando por
  la salida de audio predeterminada de Windows y habla cerca del micrófono.
- **Acentos raros (`�`) en la consola** → es solo cosmético de PowerShell; los
  datos van bien.
