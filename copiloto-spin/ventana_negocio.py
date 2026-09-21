"""
Ventana "Tu negocio": donde el vendedor le carga al copiloto lo que vende.

Tres pestañas de texto (lo que vendo, objeciones, prospecto de hoy) que se
guardan en negocios/<nombre>/, y "Completar con IA": el vendedor pega material
suelto (o adjunta PDF/TXT) y Claude lo organiza en los documentos del negocio.
La abre copiloto.py; al guardar avisa por `al_guardar(nombre)` para que el
copiloto recargue su cerebro sin reiniciar.
"""

import base64
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from anthropic import Anthropic

import prompt_spin
from config import ANTHROPIC_API_KEY

MODELO_ORGANIZADOR = "claude-opus-5"  # redaccion en español: siempre el mejor
MAX_ADJUNTOS_BYTES = 20 * 1024 * 1024

PESTANAS = (
    ("Lo que vendo", "negocio",
     "Quién eres, tus ofertas con precio, a quién le vendes, tus casos de éxito "
     "y cómo hablas. Cada oferta empieza con una línea \"## Oferta 1 — Nombre\"."),
    ("Objeciones", "objeciones",
     "Las objeciones que de verdad te ponen y la respuesta que mejor te "
     "funciona, escrita como la dirías en la llamada."),
    ("Prospecto de hoy", "contexto",
     "Lo que sepas de la persona de esta llamada: formulario, chat previo, qué "
     "oferta priorizar. Manda sobre todo lo demás. Cámbialo antes de cada llamada."),
)


def nombre_visible(carpeta: str) -> str:
    return carpeta.replace("-", " ").strip().capitalize()


def organizar_con_ia(material: str, adjuntos: list[Path], actuales: dict[str, str]) -> dict[str, str]:
    """Convierte material suelto en {"negocio", "objeciones"}. Bloqueante."""
    contenido: list[dict] = []
    for ruta in adjuntos:
        if ruta.suffix.lower() == ".pdf":
            contenido.append({
                "type": "document",
                "title": ruta.name,
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(ruta.read_bytes()).decode("ascii"),
                },
            })
        else:
            texto = ruta.read_text(encoding="utf-8", errors="replace")
            contenido.append({"type": "text", "text": f"ARCHIVO {ruta.name}:\n\n{texto}"})
    partes = []
    if actuales.get("negocio", "").strip():
        partes.append(
            "DOCUMENTOS ACTUALES del negocio (intégrales lo nuevo):\n\n"
            f"=== negocio.md ===\n{actuales['negocio']}\n\n"
            f"=== objeciones.md ===\n{actuales.get('objeciones', '')}"
        )
    partes.append("MATERIAL DEL VENDEDOR:\n\n" + (material.strip() or "(solo los archivos adjuntos)"))
    contenido.append({"type": "text", "text": "\n\n".join(partes)})

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    # Streaming: con max_tokens alto una peticion normal se corta por timeout.
    with client.messages.stream(
        model=MODELO_ORGANIZADOR,
        max_tokens=32000,
        thinking={"type": "adaptive"},
        system=prompt_spin.prompt_organizador(),
        messages=[{"role": "user", "content": contenido}],
    ) as stream:
        respuesta = stream.get_final_message()
    if respuesta.stop_reason == "refusal":
        raise RuntimeError("la IA declinó procesar este material")
    if respuesta.stop_reason == "max_tokens":
        raise RuntimeError("el material es demasiado largo; pégalo en dos tandas")
    texto = next((b.text for b in respuesta.content if b.type == "text"), "")
    return prompt_spin.separar_documentos(texto)


def abrir(raiz: tk.Tk, kit, negocio_activo: str, al_guardar, empezar_nuevo: bool = False):
    """Abre la ventana. `kit` trae el estilo de la ventana principal (C, px,
    fuentes, Segmentado, boton). Devuelve el Toplevel."""
    C, px = kit.C, kit.px
    win = tk.Toplevel(raiz)
    win.title("Tu negocio — Copiloto SPIN")
    win.configure(bg=C["fondo"])
    win.geometry(
        f"{px(690)}x{px(740)}+{raiz.winfo_x() + raiz.winfo_width() + px(12)}+{raiz.winfo_y()}"
    )
    win.minsize(px(480), px(520))
    win.attributes("-topmost", True)

    est = {
        "negocio": negocio_activo,
        "pestana": "negocio",
        "textos": prompt_spin.leer_negocio(negocio_activo),
        "guardado": {},
        "adjuntos": [],
    }
    est["guardado"] = dict(est["textos"])
    resultados: queue.Queue = queue.Queue()

    # --- cabecera ------------------------------------------------------
    cabecera = tk.Frame(win, bg=C["fondo"])
    cabecera.pack(fill="x", padx=px(18), pady=(px(16), 0))
    tk.Label(cabecera, text="Tu negocio", bg=C["fondo"], fg=C["texto"],
             font=kit.FUENTE_TITULO).pack(side="left")
    selector = tk.Menubutton(
        cabecera, bg=C["superficie"], fg=C["texto"], activebackground=C["elevada"],
        activeforeground=C["texto"], relief="flat", font=kit.FUENTE_NEGRITA,
        padx=px(12), pady=px(5), cursor="hand2", borderwidth=0,
    )
    selector.pack(side="right")
    menu = tk.Menu(selector, tearoff=0, bg=C["superficie"], fg=C["texto"],
                   activebackground=C["elevada"], activeforeground=C["texto"],
                   font=kit.FUENTE_TEXTO, borderwidth=0)
    selector.configure(menu=menu)

    tk.Label(
        win, bg=C["fondo"], fg=C["tenue"], font=kit.FUENTE_CHICA, justify="left",
        anchor="w", wraplength=px(650),
        text="Lo que escribas aquí es todo lo que el copiloto sabe de tu negocio. "
             "Entre más concreto (precios, cifras, frases reales de tus clientes), "
             "mejores las sugerencias en la llamada.",
    ).pack(fill="x", padx=px(18), pady=(px(6), 0))

    # --- vista del editor ------------------------------------------------
    vista_editor = tk.Frame(win, bg=C["fondo"])
    vista_ia = tk.Frame(win, bg=C["fondo"])

    def cambiar_pestana(clave):
        recoger_texto()
        est["pestana"] = clave
        mostrar_pestana()

    pestanas = kit.Segmentado(
        vista_editor, [(titulo, clave) for titulo, clave, _ in PESTANAS],
        est["pestana"], cambiar_pestana, fuente=kit.FUENTE_PESTANA,
    )
    pestanas.pack(fill="x", pady=(px(14), 0))
    pista = tk.Label(vista_editor, bg=C["fondo"], fg=C["tenue"], font=kit.FUENTE_CHICA,
                     justify="left", anchor="w", wraplength=px(650))
    pista.pack(fill="x", pady=(px(8), px(6)))

    def caja_texto(padre, alto):
        return tk.Text(
            padre, height=alto, bg=C["superficie"], fg=C["texto"], font=kit.FUENTE_EDITOR,
            wrap="word", relief="flat", padx=px(14), pady=px(12), undo=True,
            insertbackground=C["ambar"], selectbackground=C["elevada"],
            selectforeground=C["texto"], highlightthickness=0, borderwidth=0,
            spacing2=px(2),
        )

    editor = caja_texto(vista_editor, 10)
    editor.pack(fill="both", expand=True)

    estado = tk.Label(win, bg=C["fondo"], fg=C["tenue"], font=kit.FUENTE_CHICA,
                      anchor="w", justify="left", wraplength=px(650))

    def decir(texto, color="tenue"):
        estado.configure(text=texto, fg=C[color])

    def recoger_texto():
        est["textos"][est["pestana"]] = editor.get("1.0", "end-1c")

    def mostrar_pestana():
        editor.delete("1.0", "end")
        editor.insert("1.0", est["textos"].get(est["pestana"], ""))
        editor.edit_reset()
        pista.configure(text=next(p for _, c, p in PESTANAS if c == est["pestana"]))

    def hay_cambios() -> bool:
        recoger_texto()
        return any(
            est["textos"].get(c, "").strip() != est["guardado"].get(c, "").strip()
            for c in prompt_spin.ARCHIVOS_NEGOCIO
        )

    def guardar(_e=None):
        recoger_texto()
        if not est["textos"]["negocio"].strip():
            decir("Escribe primero qué vendes (pestaña Lo que vendo), o usa Completar con IA.", "coral")
            return "break"
        prompt_spin.guardar_negocio(est["negocio"], est["textos"])
        est["guardado"] = dict(est["textos"])
        al_guardar(est["negocio"])
        faltan = sum(t.count("[FALTA") for t in est["textos"].values())
        aviso = f" Quedan {faltan} datos marcados con [FALTA: …] por completar." if faltan else ""
        decir(f"Guardado. El copiloto ya vende {nombre_visible(est['negocio'])}.{aviso}",
              "ambar" if faltan else "verde")
        return "break"

    acciones = tk.Frame(vista_editor, bg=C["fondo"])
    acciones.pack(fill="x", pady=(px(12), 0))
    kit.boton(acciones, "Guardar y usar en la llamada", guardar, primario=True).pack(
        side="left", fill="x", expand=True)
    kit.boton(acciones, "Completar con IA", lambda: mostrar_vista("ia")).pack(
        side="left", fill="x", expand=True, padx=(px(8), 0))
    win.bind("<Control-s>", guardar)

    # --- vista "Completar con IA" ----------------------------------------
    tk.Label(vista_ia, text="Completar con IA", bg=C["fondo"], fg=C["texto"],
             font=kit.FUENTE_NEGRITA, anchor="w").pack(fill="x", pady=(px(14), 0))
    tk.Label(
        vista_ia, bg=C["fondo"], fg=C["tenue"], font=kit.FUENTE_CHICA, justify="left",
        anchor="w", wraplength=px(650),
        text="Pega todo lo que tengas de tu negocio, en cualquier orden: el texto "
             "de tu página, el guion de tu video de ventas, precios, testimonios, "
             "chats con clientes, las objeciones que te ponen. La IA lo organiza en "
             "Lo que vendo y Objeciones; tú lo revisas y lo guardas. Cuesta unos "
             "centavos de tu clave de Anthropic y tarda 1 a 3 minutos.",
    ).pack(fill="x", pady=(px(6), px(8)))
    material = caja_texto(vista_ia, 10)
    material.pack(fill="both", expand=True)

    fila_adjuntos = tk.Frame(vista_ia, bg=C["fondo"])
    fila_adjuntos.pack(fill="x", pady=(px(10), 0))
    lista_adjuntos = tk.Label(fila_adjuntos, bg=C["fondo"], fg=C["tenue"], font=kit.FUENTE_CHICA,
                              anchor="w", justify="left", wraplength=px(380),
                              text="Sin archivos adjuntos")

    def adjuntar():
        rutas = filedialog.askopenfilenames(
            parent=win, title="Archivos de tu negocio",
            filetypes=[("PDF o texto", "*.pdf *.txt *.md"), ("Todos", "*.*")],
        )
        for r in rutas:
            ruta = Path(r)
            if ruta.suffix.lower() not in (".pdf", ".txt", ".md"):
                decir(f"{ruta.name}: solo se aceptan PDF, TXT o MD. Si es Word, "
                      "copia el texto y pégalo arriba.", "coral")
                continue
            if ruta not in est["adjuntos"]:
                est["adjuntos"].append(ruta)
        if sum(r.stat().st_size for r in est["adjuntos"]) > MAX_ADJUNTOS_BYTES:
            est["adjuntos"].clear()
            decir("Los archivos pesan más de 20 MB en total. Adjunta menos o pega el texto.", "coral")
        lista_adjuntos.configure(
            text=", ".join(r.name for r in est["adjuntos"]) or "Sin archivos adjuntos")

    kit.boton(fila_adjuntos, "  Adjuntar PDF o TXT  ", adjuntar).pack(side="left")
    lista_adjuntos.pack(side="left", fill="x", expand=True, padx=(px(10), 0))

    def organizar():
        if est.get("ocupado"):
            return
        texto = material.get("1.0", "end-1c")
        if not texto.strip() and not est["adjuntos"]:
            decir("Pega algo de tu negocio o adjunta un archivo primero.", "coral")
            return
        if not ANTHROPIC_API_KEY:
            decir("Falta ANTHROPIC_API_KEY en el archivo .env.", "coral")
            return
        # Una plantilla sin llenar no es "documento actual": se parte de cero.
        actuales = {} if "PLANTILLA:" in est["textos"].get("negocio", "") else dict(est["textos"])
        est["ocupado"] = True
        decir("Organizando con IA… tarda 1 a 3 minutos. Puedes dejar esta ventana abierta.", "ambar")

        def trabajo():
            try:
                resultados.put(("ok", organizar_con_ia(texto, list(est["adjuntos"]), actuales)))
            except Exception as e:  # noqa: BLE001 — se muestra en la ventana
                resultados.put(("error", str(e)))

        threading.Thread(target=trabajo, daemon=True).start()

    acciones_ia = tk.Frame(vista_ia, bg=C["fondo"])
    acciones_ia.pack(fill="x", pady=(px(12), 0))
    kit.boton(acciones_ia, "Organizar con IA", organizar, primario=True).pack(
        side="left", fill="x", expand=True)
    kit.boton(acciones_ia, "Volver sin usar la IA", lambda: mostrar_vista("editor")).pack(
        side="left", fill="x", expand=True, padx=(px(8), 0))

    def revisar_resultados():
        try:
            tipo, dato = resultados.get_nowait()
        except queue.Empty:
            pass
        else:
            est["ocupado"] = False
            if tipo == "ok":
                est["textos"]["negocio"] = dato["negocio"]
                if dato["objeciones"]:
                    est["textos"]["objeciones"] = dato["objeciones"]
                est["pestana"] = "negocio"
                pestanas.valor = "negocio"
                pestanas.pintar()
                material.delete("1.0", "end")
                est["adjuntos"].clear()
                lista_adjuntos.configure(text="Sin archivos adjuntos")
                mostrar_vista("editor", recoger=False)
                faltan = sum(dato[c].count("[FALTA") for c in ("negocio", "objeciones"))
                decir(
                    "Listo. Revisa Lo que vendo y Objeciones"
                    + (f"; la IA marcó {faltan} datos que le faltaron con [FALTA: …]" if faltan else "")
                    + ". Todavía no está guardado: pulsa Guardar y usar en la llamada.",
                    "ambar",
                )
            else:
                decir(f"No se pudo organizar: {dato}", "coral")
        if win.winfo_exists():
            win.after(300, revisar_resultados)

    def mostrar_vista(cual, recoger=True):
        if cual == "ia":
            if recoger:
                recoger_texto()
            vista_editor.pack_forget()
            vista_ia.pack(fill="both", expand=True, padx=px(18), before=estado)
            material.focus_set()
        else:
            vista_ia.pack_forget()
            vista_editor.pack(fill="both", expand=True, padx=px(18), before=estado)
            mostrar_pestana()

    # --- cambiar de negocio / crear uno nuevo ----------------------------
    def cargar(negocio, vista="editor"):
        est["negocio"] = negocio
        est["textos"] = prompt_spin.leer_negocio(negocio)
        est["guardado"] = dict(est["textos"])
        selector.configure(text=f"{nombre_visible(negocio)}  ▾")
        armar_menu()
        mostrar_pestana()  # el editor siempre refleja el negocio cargado
        mostrar_vista(vista, recoger=False)

    def cambiar_a(negocio):
        if negocio == est["negocio"]:
            return
        if hay_cambios() and messagebox.askyesno(
            "Cambios sin guardar", parent=win,
            message=f"¿Guardar los cambios de {nombre_visible(est['negocio'])} antes de cambiar?",
        ):
            guardar()
        cargar(negocio)
        decir(f"Viendo {nombre_visible(negocio)}. Para vender este negocio en la "
              "llamada, pulsa Guardar y usar en la llamada.")

    def nuevo_negocio():
        dlg = tk.Toplevel(win)
        dlg.title("Nuevo negocio")
        dlg.configure(bg=C["fondo"], padx=px(18), pady=px(16))
        dlg.attributes("-topmost", True)
        dlg.transient(win)
        dlg.geometry(f"+{win.winfo_x() + px(80)}+{win.winfo_y() + px(140)}")
        tk.Label(dlg, text="¿Cómo se llama el negocio o la marca?", bg=C["fondo"],
                 fg=C["texto"], font=kit.FUENTE_NEGRITA, anchor="w").pack(fill="x")
        entrada = tk.Entry(dlg, width=34, bg=C["superficie"], fg=C["texto"], relief="flat",
                           font=kit.FUENTE_EDITOR, insertbackground=C["ambar"],
                           highlightthickness=px(1), highlightbackground=C["linea"],
                           highlightcolor=C["ambar"])
        entrada.pack(fill="x", pady=(px(10), 0), ipady=px(6))
        aviso = tk.Label(dlg, text="", bg=C["fondo"], fg=C["coral"], font=kit.FUENTE_CHICA, anchor="w")
        aviso.pack(fill="x", pady=(px(4), 0))

        def crear(_e=None):
            carpeta = prompt_spin.nombre_carpeta(entrada.get())
            if not carpeta or carpeta.startswith("_"):
                aviso.configure(text="Escribe un nombre con letras o números.")
                return
            if carpeta not in prompt_spin.negocios_disponibles():
                prompt_spin.guardar_negocio(carpeta, prompt_spin.plantilla_negocio())
            dlg.destroy()
            cargar(carpeta, vista="ia")
            decir(f"Creado {nombre_visible(carpeta)}. Pega aquí su información y la IA "
                  "arma los documentos, o vuelve para llenarlos a mano con la guía.")

        kit.boton(dlg, "Crear negocio", crear, primario=True).pack(fill="x", pady=(px(10), 0))
        entrada.bind("<Return>", crear)
        entrada.focus_set()

    def armar_menu():
        menu.delete(0, "end")
        for n in prompt_spin.negocios_disponibles():
            menu.add_command(label=nombre_visible(n), command=lambda n=n: cambiar_a(n))
        menu.add_separator()
        menu.add_command(label="Nuevo negocio…", command=nuevo_negocio)

    def cerrar():
        if hay_cambios() and messagebox.askyesno(
            "Cambios sin guardar", parent=win,
            message="¿Guardar los cambios antes de cerrar?",
        ):
            guardar()
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", cerrar)
    estado.pack(fill="x", padx=px(18), pady=(px(10), px(16)), side="bottom")
    cargar(negocio_activo)
    decir("Se guarda con el botón o con Ctrl+S, y el copiloto lo toma de inmediato.")
    revisar_resultados()
    if empezar_nuevo:
        win.after(200, nuevo_negocio)
    win._probar = {"mostrar_vista": mostrar_vista, "est": est, "material": material,
                   "guardar": guardar, "cargar": cargar, "organizar": organizar, "decir": estado}  # para test_ventana.py
    return win
