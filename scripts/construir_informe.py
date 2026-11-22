"""Construye docs/informe/Informe_ContentFlow.html desde la plantilla, con la bitácora
del ETL y las medidas DAX tomadas del proyecto, y lo exporta a PDF con Microsoft Edge.

    python scripts/construir_informe.py
"""
from __future__ import annotations

import html
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
import generar_pbip  # noqa: E402  (lista de medidas DAX)

INF = RAIZ / "docs" / "informe"
DATOS = RAIZ / "data" / "processed"

DESCRIPCION_TABLAS = {
    "fact_interacciones": "Hecho: una fila por reacción o comentario, con fecha, hora, sentimiento y marca de calidad.",
    "dim_usuarios": "Usuarios sin datos personales: país, género, dispositivo, dominio de correo, cohorte y antigüedad.",
    "dim_publicaciones": "Publicaciones con categoría, tipo, hora, día y métricas de interacción.",
    "dim_campanas": "Campañas con metas aplanadas, resultados logrados en su ventana y % de logro.",
    "dim_fecha": "Calendario continuo (mes, trimestre, día de semana).",
    "puente_publicacion_hashtag": "Arreglo hashtags desanidado (publicación–hashtag).",
    "puente_campana_publicacion": "Arreglo publicaciones_asociadas desanidado (campaña–publicación).",
    "control_ejecucion": "Fecha y hora de la última ejecución del ETL.",
}
MEDIDAS_INFORME = [
    "Mejor hora para publicar", "Ranking usuario", "Interacciones Top 10 usuarios",
    "Interacciones por hashtag", "Publicaciones por autor", "Última actualización",
]


def e(t) -> str:
    return html.escape(str(t))


def tabla_bitacora() -> str:
    b = pd.read_csv(DATOS / "bitacora_transformaciones.csv", encoding="utf-8-sig")
    filas = "".join(
        f"<tr><td>{i}</td><td><code>{e(r.tabla)}</code></td><td>{e(r.tipo)}</td><td>{e(r.operacion)}</td>"
        f"<td>{e(r.motivo)}</td><td style='text-align:right'>{'' if pd.isna(r.registros_afectados) else int(r.registros_afectados)}</td></tr>"
        for i, r in enumerate(b.itertuples(), 1)
    )
    return (
        "<table><tr><th style='width:4%'>#</th><th style='width:17%'>Tabla</th><th style='width:9%'>Tipo</th>"
        "<th style='width:29%'>Transformación</th><th>Justificación</th><th style='width:7%'>Reg.</th></tr>"
        f"{filas}</table>"
    )


def filas_tablas() -> str:
    out = []
    for t, desc in DESCRIPCION_TABLAS.items():
        n = len(pd.read_csv(DATOS / f"{t}.csv", encoding="utf-8-sig"))
        out.append(f"<tr><td><code>{t}</code></td><td>{n:,}".replace(",", ".") + f"</td><td>{e(desc)}</td></tr>")
    return "".join(out)


def medidas() -> str:
    m = {n: (expr, desc) for n, expr, _f, _c, desc in generar_pbip.MEDIDAS}
    bloques = []
    for nombre in MEDIDAS_INFORME:
        expr, desc = m[nombre]
        nota = f"<p class='pequeno'>{e(desc)}</p>" if desc else ""
        bloques.append(f"<p><strong>{e(nombre)}</strong></p>{nota}<pre>{e(nombre)} =\n{e(expr)}</pre>")
    return "".join(bloques)


def main() -> None:
    plantilla = (INF / "plantilla.html").read_text(encoding="utf-8")
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
    hoy = date.today()
    salida = (
        plantilla.replace("{{BITACORA}}", tabla_bitacora())
        .replace("{{TABLAS}}", filas_tablas())
        .replace("{{MEDIDAS}}", medidas())
        .replace("{{FECHA}}", f"{hoy.day} de {meses[hoy.month - 1]} de {hoy.year}")
    )
    html_out = INF / "Informe_ContentFlow.html"
    html_out.write_text(salida, encoding="utf-8")

    edge = shutil.which("msedge") or next(
        (p for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                     r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"] if Path(p).exists()), None)
    if not edge:
        print(f"HTML listo en {html_out}. No se encontró Edge: imprimir a PDF desde el navegador.")
        return
    pdf = INF / "Informe_ContentFlow.pdf"
    if pdf.exists():
        pdf.unlink()
    # perfil temporal propio: si Edge ya está abierto, sin esto la orden se entrega a esa instancia y no imprime
    perfil = Path(tempfile.gettempdir()) / "contentflow_edge_pdf"
    perfil.mkdir(exist_ok=True)
    subprocess.run([edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={perfil}", f"--print-to-pdf={pdf}", html_out.as_uri()],
                   check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # el lanzador de Edge termina antes que el proceso que imprime: esperar al archivo
    for _ in range(120):
        if pdf.exists() and pdf.stat().st_size > 0:
            time.sleep(1)
            break
        time.sleep(0.5)
    else:
        raise SystemExit("Edge no generó el PDF")
    print(f"PDF: {pdf} ({pdf.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
