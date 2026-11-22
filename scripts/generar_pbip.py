"""Genera el modelo semántico (TMDL) y el informe (PBIR) del tablero ContentFlow
dentro del proyecto Power BI powerbi/ContentFlow.pbip.

- Modelo: tablas importadas desde los CSV de data/processed (parámetro RutaDatos),
  relaciones en estrella y medidas DAX (KPIs).
- Informe: 4 páginas con tarjetas KPI, gráficos y segmentadores.

Uso (con Power BI Desktop cerrado):
    python scripts/generar_pbip.py
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "data" / "processed"
PBI = RAIZ / "powerbi"
SM = PBI / "ContentFlow.SemanticModel" / "definition"
RP = PBI / "ContentFlow.Report" / "definition"

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VISUAL_SCHEMA = f"{SCHEMA}/visualContainer/2.4.0/schema.json"

# ---------------------------------------------------------------------------
# MODELO SEMÁNTICO
# ---------------------------------------------------------------------------
TABLAS = [
    "dim_usuarios",
    "dim_publicaciones",
    "dim_campanas",
    "dim_fecha",
    "fact_interacciones",
    "puente_publicacion_hashtag",
    "puente_campana_publicacion",
    "control_ejecucion",
]
COLUMNAS_FECHA = {
    "fecha_registro", "fecha_publicacion", "pub_fecha", "fecha_hora", "fecha",
    "fecha_inicio", "fecha_fin", "ultima_ejecucion",
}
COLUMNAS_PCT = {"pct_logro_likes", "pct_logro_comentarios", "pct_logro_promedio"}
ORDENAR_POR = {"dia_semana": "dia_semana_num", "pub_dia_semana": "pub_dia_semana_num"}
OCULTAS = {"dia_semana_num", "pub_dia_semana_num"}

RELACIONES = [
    # (desde [muchos], hacia [uno], activa)
    ("fact_interacciones.publicacion_id", "dim_publicaciones.publicacion_id", True),
    ("fact_interacciones.usuario_id", "dim_usuarios.usuario_id", True),
    ("fact_interacciones.fecha", "dim_fecha.fecha", True),
    ("puente_publicacion_hashtag.publicacion_id", "dim_publicaciones.publicacion_id", True),
    ("puente_campana_publicacion.publicacion_id", "dim_publicaciones.publicacion_id", True),
    ("puente_campana_publicacion.campana_id", "dim_campanas.campana_id", True),
    # inactivas: crearían caminos ambiguos; se activan con USERELATIONSHIP cuando se necesitan
    ("dim_publicaciones.autor_id", "dim_usuarios.usuario_id", False),
    ("dim_publicaciones.pub_fecha", "dim_fecha.fecha", False),
]

def ranking(columna: str) -> str:
    """Posición 1..N por [Interacciones] sin empates (desempate alfabético)."""
    return "\n".join([
        f"IF(HASONEVALUE({columna}),",
        "    VAR actual = [Interacciones]",
        f"    VAR clave = SELECTEDVALUE({columna})",
        f'    VAR t = ADDCOLUMNS(ALLSELECTED({columna}), "@i", [Interacciones])',
        f"    RETURN COUNTROWS(FILTER(t, [@i] > actual || ([@i] = actual && {columna} < clave))) + 1",
        ")",
    ])


# (nombre, expresión DAX, formato, carpeta, descripción)
MEDIDAS = [
    ("Publicaciones", "COUNTROWS(dim_publicaciones)", "#,0", "Base", "Total de publicaciones"),
    ("Interacciones", "COUNTROWS(fact_interacciones)", "#,0", "Base", "Reacciones + comentarios"),
    ("Reacciones", 'CALCULATE([Interacciones], fact_interacciones[tipo_interaccion] = "Reacción")', "#,0", "Base", ""),
    ("Comentarios", 'CALCULATE([Interacciones], fact_interacciones[tipo_interaccion] = "Comentario")', "#,0", "Base", ""),
    ("Likes en comentarios", "SUM(fact_interacciones[likes])", "#,0", "Base", ""),
    ("Usuarios registrados", "COUNTROWS(dim_usuarios)", "#,0", "Base", ""),
    ("Usuarios activos", "DISTINCTCOUNT(fact_interacciones[usuario_id])", "#,0", "Base", "Usuarios con al menos una reacción o comentario"),
    ("Reacciones positivas", 'CALCULATE([Reacciones], fact_interacciones[sentimiento] = "Positivo")', "#,0", "Base", ""),
    ("Campañas", "COUNTROWS(dim_campanas)", "#,0", "Base", ""),
    # --- KPIs ---------------------------------------------------------------
    ("KPI Interacciones por publicación", "DIVIDE([Interacciones], [Publicaciones])", "0.00", "KPI",
     "KPI 1 - Popularidad del contenido: interacciones promedio que recibe cada publicación"),
    ("KPI % Usuarios activos", "DIVIDE([Usuarios activos], [Usuarios registrados])", "0.0%", "KPI",
     "KPI 2 - Participación: porcentaje de usuarios registrados que interactuó en el período"),
    ("KPI % Sentimiento positivo", "DIVIDE([Reacciones positivas], [Reacciones])", "0.0%", "KPI",
     "KPI 3 - Percepción: porcentaje de reacciones Me gusta / Me encanta"),
    ("KPI Logro de campañas", "AVERAGE(dim_campanas[pct_logro_promedio])", "0.00%", "KPI",
     "KPI 4 - Impacto de campañas: logro promedio de metas de likes y comentarios"),
    ("KPI % Interacciones consistentes",
     "DIVIDE(CALCULATE([Interacciones], fact_interacciones[consistente_temporal] = TRUE()), [Interacciones])",
     "0.0%", "KPI", "Calidad de datos: interacciones fechadas después de su publicación"),
    # --- Apoyo a visuales --------------------------------------------------
    ("Campañas con logro >= 5%", "CALCULATE([Campañas], dim_campanas[pct_logro_promedio] >= 0.05)", "#,0", "Campañas", ""),
    ("Usuarios alcanzados", "SUM(dim_campanas[usuarios_alcanzados])", "#,0", "Campañas", ""),
    ("Mejor hora para publicar",
     'VAR t = ADDCOLUMNS(VALUES(dim_publicaciones[pub_hora]), "@e", [KPI Interacciones por publicación])\n'
     "VAR h = MAXX(TOPN(1, t, [@e], DESC), dim_publicaciones[pub_hora])\n"
     'RETURN FORMAT(h, "00") & ":00 h"',
     "", "Contenido", "Hora de publicación con más interacciones por publicación"),
    ("Mejor día para publicar",
     'VAR t = ADDCOLUMNS(VALUES(dim_publicaciones[pub_dia_semana]), "@e", [KPI Interacciones por publicación])\n'
     "RETURN MAXX(TOPN(1, t, [@e], DESC), dim_publicaciones[pub_dia_semana])",
     "", "Contenido", ""),
    ("Publicaciones con hashtag", "COUNTROWS(puente_publicacion_hashtag)", "#,0", "Contenido", ""),
    ("Interacciones por hashtag",
     "DIVIDE(SUMX(puente_publicacion_hashtag, RELATED(dim_publicaciones[engagement])), [Publicaciones con hashtag])",
     "0.00", "Contenido", "Interacciones promedio de las publicaciones que usan el hashtag"),
    ("Ranking usuario", ranking("dim_usuarios[usuario_id]"), "0", "Audiencia",
     "Posición por interacciones; los empates se resuelven por orden alfabético"),
    ("Interacciones Top 10 usuarios", "IF(NOT ISBLANK([Ranking usuario]) && [Ranking usuario] <= 10, [Interacciones])",
     "#,0", "Audiencia", ""),
    ("Ranking país", ranking("dim_usuarios[pais]"), "0", "Audiencia", ""),
    ("Interacciones Top 10 países", "IF(NOT ISBLANK([Ranking país]) && [Ranking país] <= 10, [Interacciones])",
     "#,0", "Audiencia", ""),
    ("Publicaciones por autor",
     "CALCULATE([Publicaciones], USERELATIONSHIP(dim_publicaciones[autor_id], dim_usuarios[usuario_id]))",
     "#,0", "Audiencia", "Usa la relación inactiva autor -> usuario"),
    ("Última actualización",
     '"Datos actualizados: " & FORMAT(MAX(control_ejecucion[ultima_ejecucion]), "dd-mm-yyyy hh:nn")',
     "", "Control", "Momento de la última ejecución del ETL"),
]


def q(nombre: str) -> str:
    """Nombre TMDL: entre comillas simples si tiene espacios o símbolos."""
    return nombre if nombre.replace("_", "").isalnum() else "'" + nombre.replace("'", "''") + "'"


def tipo_columna(df: pd.DataFrame, col: str) -> tuple[str, str]:
    """(dataType TMDL, tipo Power Query)."""
    if col in COLUMNAS_FECHA:
        return "dateTime", "type datetime"
    k = df[col].dtype.kind
    if k == "b":
        return "boolean", "type logical"
    if k in "iu":
        return "int64", "Int64.Type"
    if k == "f":
        return "double", "type number"
    return "string", "type text"


def tmdl_tabla(nombre: str) -> str:
    df = pd.read_csv(DATOS / f"{nombre}.csv", encoding="utf-8-sig", nrows=200)
    lineas = [f"table {nombre}", f"\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, nombre)}", ""]
    tipos_m = []
    for col in df.columns:
        dt, tm = tipo_columna(df, col)
        tipos_m.append(f'{{"{col}", {tm}}}')
        lineas += [f"\tcolumn {q(col)}", f"\t\tdataType: {dt}"]
        if dt == "dateTime":
            lineas.append("\t\tformatString: dd-mm-yyyy" if col not in {"fecha_hora", "fecha_publicacion", "ultima_ejecucion"} else "\t\tformatString: dd-mm-yyyy hh:nn")
        elif col in COLUMNAS_PCT:
            lineas.append("\t\tformatString: 0.00%")
        elif dt == "double":
            lineas.append("\t\tformatString: 0.00")
        elif dt == "int64":
            lineas.append("\t\tformatString: 0")
        if col in OCULTAS:
            lineas.append("\t\tisHidden")
        if col in ORDENAR_POR:
            lineas.append(f"\t\tsortByColumn: {ORDENAR_POR[col]}")
        lineas += [
            f"\t\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, nombre + '.' + col)}",
            "\t\tsummarizeBy: none",
            f"\t\tsourceColumn: {col}",
            "",
        ]
    m = [
        "let",
        f'    Origen = Csv.Document(File.Contents(RutaDatos & "{nombre}.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
        "    Encabezados = Table.PromoteHeaders(Origen, [PromoteAllScalars = true]),",
        f'    Tipos = Table.TransformColumnTypes(Encabezados, {{{", ".join(tipos_m)}}}, "en-US")',
        "in",
        "    Tipos",
    ]
    lineas += [f"\tpartition {nombre} = m", "\t\tmode: import", "\t\tsource ="]
    lineas += ["\t\t\t\t" + l for l in m]
    lineas += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(lineas)


def tmdl_medidas() -> str:
    lineas = ["table Medidas", f"\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, 'Medidas')}", ""]
    for nombre, expr, fmt, carpeta, desc in MEDIDAS:
        if desc:
            lineas.append(f"\t/// {desc}")
        if "\n" in expr:
            lineas.append(f"\tmeasure {q(nombre)} = ```")
            lineas += ["\t\t\t" + l for l in expr.split("\n")]
            lineas.append("\t\t\t```")
        else:
            lineas.append(f"\tmeasure {q(nombre)} = {expr}")
        if fmt:
            lineas.append(f"\t\tformatString: {fmt}")
        lineas += [
            f"\t\tdisplayFolder: {carpeta}",
            f"\t\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, 'm.' + nombre)}",
            "",
        ]
    lineas += [
        "\tcolumn Columna",
        "\t\tdataType: string",
        "\t\tisHidden",
        f"\t\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, 'Medidas.Columna')}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: Columna",
        "",
        "\tpartition Medidas = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        '\t\t\t\t    Origen = #table(type table [Columna = text], {})',
        "\t\t\t\tin",
        "\t\t\t\t    Origen",
        "",
        "\tannotation PBI_ResultType = Table",
        "",
    ]
    return "\n".join(lineas)


def generar_modelo() -> None:
    (SM / "tables").mkdir(parents=True, exist_ok=True)
    for viejo in (SM / "tables").glob("*.tmdl"):
        viejo.unlink()
    for t in TABLAS:
        (SM / "tables" / f"{t}.tmdl").write_text(tmdl_tabla(t), encoding="utf-8")
    (SM / "tables" / "Medidas.tmdl").write_text(tmdl_medidas(), encoding="utf-8")

    ruta = str(DATOS) + "\\"
    (SM / "expressions.tmdl").write_text(
        f'expression RutaDatos = "{ruta}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]\n'
        f"\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, 'RutaDatos')}\n\n"
        "\tannotation PBI_ResultType = Text\n",
        encoding="utf-8",
    )

    rel = []
    for desde, hacia, activa in RELACIONES:
        rel += [f"relationship {uuid.uuid5(uuid.NAMESPACE_URL, desde + hacia)}"]
        if not activa:
            rel.append("\tisActive: false")
        rel += [f"\tfromColumn: {desde}", f"\ttoColumn: {hacia}", ""]
    (SM / "relationships.tmdl").write_text("\n".join(rel), encoding="utf-8")

    orden = json.dumps(["RutaDatos", *TABLAS, "Medidas"], ensure_ascii=False)
    (SM / "model.tmdl").write_text(
        "model Model\n"
        "\tculture: es-MX\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: es-CL\n"
        "\tvalueFilterBehavior: independent\n"
        "\tdataAccessOptions\n"
        "\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n\n"
        f"annotation PBI_QueryOrder = {orden}\n\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n\n"
        'annotation PBI_ProTooling = ["DevMode"]\n\n'
        + "".join(f"ref table {t}\n" for t in [*TABLAS, "Medidas"])
        + "\nref cultureInfo es-MX\n",
        encoding="utf-8",
    )
    # La caché del modelo se descarta para que Power BI recargue con la nueva definición
    cache = PBI / "ContentFlow.SemanticModel" / ".pbi" / "cache.abf"
    if cache.exists():
        cache.unlink()


# ---------------------------------------------------------------------------
# INFORME (PBIR)
# ---------------------------------------------------------------------------
def lit(valor) -> dict:
    if isinstance(valor, bool):
        v = "true" if valor else "false"
    elif isinstance(valor, (int, float)):
        v = f"{valor}D"
    else:
        v = "'" + str(valor).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": v}}}


def col(tabla: str, campo: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": tabla}}, "Property": campo}}


def med(campo: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "Medidas"}}, "Property": campo}}


def proy(campo: dict) -> dict:
    k = "Column" if "Column" in campo else "Measure"
    ent = campo[k]["Expression"]["SourceRef"]["Entity"]
    prop = campo[k]["Property"]
    return {"field": campo, "queryRef": f"{ent}.{prop}", "nativeQueryRef": prop}


class Pagina:
    def __init__(self, nombre: str, titulo: str):
        self.id = nombre
        self.titulo = titulo
        self.visuales: list[dict] = []

    def visual(self, tipo: str, x, y, w, h, roles: dict, titulo: str | None = None,
               orden: tuple[dict, str] | None = None, objetos: dict | None = None) -> None:
        vid = f"{self.id}_v{len(self.visuales) + 1:02d}"
        query = {"queryState": {rol: {"projections": [proy(c) for c in campos]} for rol, campos in roles.items()}}
        if orden:
            query["sortDefinition"] = {"sort": [{"field": orden[0], "direction": orden[1]}], "isDefaultSort": False}
        v = {"visualType": tipo, "query": query, "drillFilterOtherVisuals": True}
        if objetos:
            v["objects"] = objetos
        if titulo:
            v["visualContainerObjects"] = {
                "title": [{"properties": {"show": lit(True), "text": lit(titulo)}}],
                "subTitle": [{"properties": {"show": lit(False)}}],
            }
        else:
            v["visualContainerObjects"] = {"title": [{"properties": {"show": lit(False)}}]}
        self.visuales.append(
            {
                "$schema": VISUAL_SCHEMA,
                "name": vid,
                "position": {"x": x, "y": y, "z": len(self.visuales) * 1000, "height": h, "width": w,
                             "tabOrder": len(self.visuales) * 1000},
                "visual": v,
            }
        )

    def kpi(self, x, y, medida: str, titulo: str, w=230, h=110, tam=22):
        # sin etiqueta de categoría (repetía el nombre de la medida y cortaba el valor)
        self.visual("card", x, y, w, h, {"Values": [med(medida)]}, titulo,
                    objetos={"categoryLabels": [{"properties": {"show": lit(False)}}],
                             "labels": [{"properties": {"fontSize": lit(tam)}}]})

    def segmentador(self, x, y, tabla: str, campo: str, titulo: str, w=230, h=70, modo="Dropdown"):
        # sin encabezado propio: el título del contenedor ya identifica el campo
        self.visual("slicer", x, y, w, h, {"Values": [col(tabla, campo)]}, titulo,
                    objetos={"data": [{"properties": {"mode": lit(modo)}}],
                             "header": [{"properties": {"show": lit(False)}}]})


def segmentadores_comunes(p: Pagina, y=10):
    p.segmentador(10, y, "dim_fecha", "mes", "Mes")
    p.segmentador(250, y, "dim_usuarios", "pais", "País del usuario")
    p.segmentador(490, y, "dim_usuarios", "dispositivo", "Dispositivo")
    p.segmentador(730, y, "dim_publicaciones", "categoria", "Categoría")
    p.kpi(970, y, "Última actualización", None, w=300, h=70, tam=12)


def construir_paginas() -> list[Pagina]:
    # 1. Resumen ejecutivo ----------------------------------------------------
    r = Pagina("resumen", "Resumen ejecutivo")
    segmentadores_comunes(r)
    r.kpi(10, 90, "KPI Interacciones por publicación", "Interacciones / publicación")
    r.kpi(250, 90, "KPI % Usuarios activos", "% Usuarios activos")
    r.kpi(490, 90, "KPI % Sentimiento positivo", "% Sentimiento positivo")
    r.kpi(730, 90, "KPI Logro de campañas", "Logro de campañas")
    r.kpi(970, 90, "KPI % Interacciones consistentes", "Calidad: datos consistentes", w=300)
    r.visual("lineChart", 10, 210, 620, 250,
             {"Category": [col("dim_fecha", "mes")], "Y": [med("Interacciones")], "Series": [col("fact_interacciones", "tipo_interaccion")]},
             "Evolución mensual de interacciones", orden=(col("dim_fecha", "mes"), "Ascending"))
    r.visual("clusteredBarChart", 640, 210, 630, 250,
             {"Category": [col("dim_publicaciones", "categoria")], "Y": [med("KPI Interacciones por publicación")]},
             "Interacciones por publicación según categoría", orden=(med("KPI Interacciones por publicación"), "Descending"))
    r.visual("donutChart", 10, 470, 400, 240,
             {"Category": [col("fact_interacciones", "subtipo")], "Y": [med("Reacciones")]},
             "Distribución de reacciones")
    r.visual("clusteredColumnChart", 420, 470, 850, 240,
             {"Category": [col("dim_publicaciones", "tipo")], "Y": [med("Reacciones"), med("Comentarios")]},
             "Reacciones y comentarios por tipo de publicación")

    # 2. Contenido y horarios -------------------------------------------------
    c = Pagina("contenido", "Contenido y horarios")
    segmentadores_comunes(c)
    c.kpi(10, 90, "Mejor hora para publicar", "Mejor hora para publicar")
    c.kpi(250, 90, "Mejor día para publicar", "Mejor día para publicar")
    c.kpi(490, 90, "Publicaciones", "Publicaciones")
    c.kpi(730, 90, "Likes en comentarios", "Likes en comentarios")
    c.visual("clusteredColumnChart", 10, 210, 840, 250,
             {"Category": [col("dim_publicaciones", "pub_hora")], "Y": [med("KPI Interacciones por publicación")]},
             "Interacciones por publicación según hora de publicación", orden=(col("dim_publicaciones", "pub_hora"), "Ascending"))
    c.visual("clusteredColumnChart", 860, 210, 410, 250,
             {"Category": [col("dim_publicaciones", "pub_dia_semana")], "Y": [med("KPI Interacciones por publicación")]},
             "Según día de la semana", orden=(col("dim_publicaciones", "pub_dia_semana"), "Ascending"))
    c.visual("clusteredBarChart", 10, 470, 620, 240,
             {"Category": [col("puente_publicacion_hashtag", "hashtag")], "Y": [med("Interacciones por hashtag")]},
             "Interacciones promedio por hashtag", orden=(med("Interacciones por hashtag"), "Descending"))
    c.visual("pivotTable", 640, 470, 630, 240,
             {"Rows": [col("dim_publicaciones", "categoria")], "Columns": [col("dim_publicaciones", "tipo")],
              "Values": [med("KPI Interacciones por publicación")]},
             "Interacciones por publicación: categoría x tipo")

    # 3. Audiencia --------------------------------------------------------------
    a = Pagina("audiencia", "Audiencia")
    segmentadores_comunes(a)
    a.kpi(10, 90, "Usuarios registrados", "Usuarios registrados")
    a.kpi(250, 90, "Usuarios activos", "Usuarios activos")
    a.kpi(490, 90, "KPI % Usuarios activos", "% Usuarios activos")
    a.kpi(730, 90, "Interacciones", "Interacciones")
    a.visual("clusteredBarChart", 10, 210, 420, 500,
             {"Category": [col("dim_usuarios", "pais")], "Y": [med("Interacciones Top 10 países")]},
             "Top 10 países por interacciones", orden=(med("Interacciones Top 10 países"), "Descending"))
    a.visual("donutChart", 440, 210, 400, 250,
             {"Category": [col("dim_usuarios", "dispositivo")], "Y": [med("Interacciones")]},
             "Interacciones por dispositivo")
    a.visual("clusteredColumnChart", 850, 210, 420, 250,
             {"Category": [col("dim_usuarios", "genero")], "Y": [med("KPI % Sentimiento positivo")]},
             "% Sentimiento positivo por género")
    a.visual("tableEx", 440, 470, 830, 240,
             {"Values": [col("dim_usuarios", "usuario_id"), col("dim_usuarios", "pais"), col("dim_usuarios", "dispositivo"),
                         col("dim_usuarios", "cohorte_registro"), med("Interacciones Top 10 usuarios")]},
             "Top 10 usuarios más activos", orden=(med("Interacciones Top 10 usuarios"), "Descending"))

    # 4. Campañas ---------------------------------------------------------------
    k = Pagina("campanas", "Campañas")
    k.segmentador(10, 10, "dim_campanas", "objetivo", "Objetivo de campaña")
    k.segmentador(250, 10, "dim_campanas", "estado", "Estado")
    k.kpi(970, 10, "Última actualización", None, w=300, h=70, tam=12)
    k.kpi(10, 90, "Campañas", "Campañas")
    k.kpi(250, 90, "KPI Logro de campañas", "Logro de metas")
    k.kpi(490, 90, "Campañas con logro >= 5%", "Campañas logro ≥ 5%")
    k.kpi(730, 90, "Usuarios alcanzados", "Usuarios alcanzados")
    k.visual("clusteredBarChart", 10, 210, 500, 250,
             {"Category": [col("dim_campanas", "objetivo")], "Y": [med("KPI Logro de campañas")]},
             "Logro promedio por objetivo", orden=(med("KPI Logro de campañas"), "Descending"))
    k.visual("clusteredColumnChart", 520, 210, 750, 250,
             {"Category": [col("dim_campanas", "n_publicaciones")], "Y": [med("KPI Logro de campañas")]},
             "Logro según cantidad de publicaciones asociadas", orden=(col("dim_campanas", "n_publicaciones"), "Ascending"))
    k.visual("tableEx", 10, 470, 1260, 240,
             {"Values": [col("dim_campanas", "etiqueta"), col("dim_campanas", "objetivo"), col("dim_campanas", "n_publicaciones"),
                         col("dim_campanas", "meta_likes"), col("dim_campanas", "reacciones_positivas"),
                         col("dim_campanas", "meta_comentarios"), col("dim_campanas", "comentarios_logrados"),
                         col("dim_campanas", "pct_logro_promedio")]},
             "Detalle de campañas (ordenado por logro)", orden=(col("dim_campanas", "pct_logro_promedio"), "Descending"))
    return [r, c, a, k]


def generar_informe() -> None:
    paginas_dir = RP / "pages"
    for d in paginas_dir.iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    paginas = construir_paginas()
    for p in paginas:
        pd_dir = paginas_dir / p.id
        (pd_dir / "visuals").mkdir(parents=True)
        (pd_dir / "page.json").write_text(json.dumps({
            "$schema": f"{SCHEMA}/page/2.1.0/schema.json",
            "name": p.id, "displayName": p.titulo, "displayOption": "FitToPage", "height": 720, "width": 1280,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        for v in p.visuales:
            vd = pd_dir / "visuals" / v["name"]
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")
    (paginas_dir / "pages.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/pagesMetadata/1.1.0/schema.json",
        "pageOrder": [p.id for p in paginas], "activePageName": paginas[0].id,
    }, indent=2), encoding="utf-8")
    print(f"  {len(paginas)} páginas, {sum(len(p.visuales) for p in paginas)} visuales")


if __name__ == "__main__":
    generar_modelo()
    print(f"  modelo: {len(TABLAS) + 1} tablas, {len(RELACIONES)} relaciones, {len(MEDIDAS)} medidas")
    generar_informe()
