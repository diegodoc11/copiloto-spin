"""
Vista previa de la ventana del copiloto SIN audio ni API: la llena con una
sugerencia de ejemplo para revisar el diseño.

  python test_ventana.py                   # venta en 2 llamadas, 1ª (SPIN); queda abierta
  python test_ventana.py cierre            # 2ª llamada (cierre), con los ajustes abiertos
  python test_ventana.py completa          # venta en una sola llamada
  python test_ventana.py negocio           # abre además la ventana "Tu negocio"
  python test_ventana.py ia                # "Tu negocio" en la vista Completar con IA
  python test_ventana.py spin foto.png     # guarda una captura y se cierra
"""

import sys

import copiloto

SPIN = """FASE ACTUAL: Implicación
AVATAR: Empleado que quiere montar agencia con IA
DOLORES DETECTADOS: Trabaja 10 horas y no le alcanza | No sabe conseguir clientes | Probó cursos y no aplicó nada
OBJECIÓN: No tengo tiempo → Justo por eso: el sistema está hecho para montarse en 1 hora al día sin dejar tu trabajo.
PREGUNTA AHORA:
1. Si sigues 6 meses más con esas 10 horas diarias y el mismo sueldo, ¿qué te cuesta eso a ti y a tu familia?
2. ¿Cuántas veces has sentido que podías más, pero no sabías por dónde empezar?
3. ¿Qué has dejado de hacer por no tener ese ingreso extra?"""

CIERRE = """MOMENTO: Precio
SEÑAL DE COMPRA: Preguntó si puede pagar en dos cuotas
OBJECIÓN: Lo tengo que hablar con mi esposa → Perfecto, ¿qué crees que te va a preguntar ella? Resolvámoslo ahora para que llegues con todo claro.
DI ESTO AHORA:
1. Sí, se puede en dos cuotas. ¿Te lo dejo así para que arranques hoy mismo?
2. ¿Qué tarjeta te queda más cómoda para la primera cuota?
3. Listo, te mando el link y lo hacemos juntos en la llamada."""

COMPLETA = """FASE ACTUAL: Presentación
SEÑAL DE COMPRA: ninguna aún
DOLORES DETECTADOS: Gasta 24.000 al mes en pauta sin pacientes nuevos | Solo le escriben "info" y no compran | Su equipo está sentado sin trabajo
OBJECIÓN: ninguna
DI ESTO AHORA:
1. "Por lo que me cuentas, el problema no es la pauta sino a quién le está llegando. ¿Te muestro cómo lo resolveríamos nosotros?"
2. ¿Qué pasaría en tu consultorio si de esos mensajes te agendaran 10 pacientes al mes?
3. Antes de mostrarte, ¿quién más decide contigo sobre esto?"""

LINEAS = [
    "Prospecto: Yo trabajo en una empresa de logística, entro a las siete y salgo a las cinco.",
    "Tú: ¿Y cómo te sientes con eso hoy?",
    "Prospecto: Cansado, la verdad. Siento que trabajo mucho y no avanzo, he comprado cursos pero no aplico nada.",
    "Tú: Entiendo. ¿Qué te ha frenado para aplicarlos?",
    "Prospecto: El tiempo. No tengo tiempo para nada.",
]

if __name__ == "__main__":
    vista_pedida = sys.argv[1] if len(sys.argv) > 1 else "spin"
    foto = sys.argv[2] if len(sys.argv) > 2 else None
    modo = vista_pedida if vista_pedida in ("spin", "cierre", "completa") else "spin"
    copiloto.modo_analisis["valor"] = modo
    v = copiloto.modo_ventana(20, arrancar_nucleo=False)
    v.ui.put(("estado", "Escuchando (Prospecto)"))
    for linea in LINEAS:
        v.ui.put(("linea", linea))
    v.ui.put(("sugerencia", {"spin": SPIN, "cierre": CIERRE, "completa": COMPLETA}[modo]))
    if modo == "cierre":
        v.alternar_ajustes()

    objetivo = v.raiz
    if vista_pedida in ("negocio", "ia"):
        v.raiz.update()
        objetivo = v.abrir_negocio()
        if vista_pedida == "ia":
            objetivo._probar["mostrar_vista"]("ia")
            objetivo._probar["material"].insert(
                "1.0", "Vendo un programa de inglés para adultos, 3 meses, $350 USD...")

    if foto:
        from PIL import ImageGrab

        def capturar():
            objetivo.update()
            x, y = objetivo.winfo_rootx(), objetivo.winfo_rooty()
            ImageGrab.grab(
                (x, y, x + objetivo.winfo_width(), y + objetivo.winfo_height())
            ).save(foto)
            v.raiz.destroy()

        v.raiz.after(1500, capturar)
    v.raiz.mainloop()
