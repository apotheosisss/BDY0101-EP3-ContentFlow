"""ETL ContentFlow: Cosmos DB (API MongoDB) -> pandas -> modelo estrella para Power BI.

Etapas
------
1. EXTRACCIÓN   consultas y pipelines de agregación ejecutados en el servidor
                (filtros, $lookup, $unwind, $group, $project) sobre las 5 colecciones.
2. TRANSFORMACIÓN  conversión de tipos, aplanado de anidamientos, normalización,
                limpieza y columnas derivadas (cada paso queda registrado).
3. CARGA        tablas CSV (UTF-8) en data/processed/, que Power BI importa.

Uso:
    python scripts/etl_contentflow.py
"""
from __future__ import annotations

import json
import os
import unicodedata
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "data" / "processed"
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
REACCIONES_POSITIVAS = {"Me gusta", "Me encanta"}

BITACORA: list[dict] = []


def registrar(tabla: str, tipo: str, operacion: str, motivo: str, afectados: int | None = None) -> None:
    """Deja constancia de cada transformación (se exporta a bitacora_transformaciones.csv)."""
    BITACORA.append(
        {"tabla": tabla, "tipo": tipo, "operacion": operacion, "motivo": motivo, "registros_afectados": afectados}
    )


# ---------------------------------------------------------------------------
# 1. EXTRACCIÓN
# ---------------------------------------------------------------------------
def conectar():
    load_dotenv(RAIZ / ".env")
    cliente = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=20000)
    return cliente, cliente[os.environ.get("MONGO_DB", "contentflow")]


def extraer(db) -> dict[str, pd.DataFrame]:
    """Consultas al servidor. Se excluyen campos no necesarios (proyección) para
    reducir transferencia y no traer datos personales que el análisis no usa."""
    datos: dict[str, list] = {}

    # Q1. Usuarios: proyección que excluye nombre y correo completo (dato personal).
    #     Solo se conserva el dominio del correo para segmentar.
    datos["usuarios"] = list(
        db.usuarios.find({}, {"_id": 1, "correo": 1, "pais": 1, "genero": 1, "fecha_registro": 1, "dispositivo": 1})
    )

    # Q2. Publicaciones con conteo de reacciones y comentarios (join en servidor).
    datos["publicaciones"] = list(
        db.publicaciones.aggregate(
            [
                {"$lookup": {"from": "reacciones", "localField": "_id", "foreignField": "publicacion_id", "as": "r"}},
                {"$lookup": {"from": "comentarios", "localField": "_id", "foreignField": "publicacion_id", "as": "c"}},
                {
                    "$project": {
                        "usuario_id": 1,
                        "fecha_publicacion": 1,
                        "categoria": 1,
                        "tipo": 1,
                        "hashtags": 1,
                        "largo_contenido": {"$strLenCP": "$contenido"},
                        "n_reacciones": {"$size": "$r"},
                        "n_comentarios": {"$size": "$c"},
                        "likes_comentarios": {"$sum": "$c.likes"},
                    }
                },
            ]
        )
    )

    # Q3. Reacciones y Q4. comentarios (el texto del comentario se reduce a su largo).
    datos["reacciones"] = list(
        db.reacciones.find({}, {"_id": 1, "usuario_id": 1, "publicacion_id": 1, "tipo_reaccion": 1, "fecha": 1})
    )
    datos["comentarios"] = list(
        db.comentarios.aggregate(
            [
                {
                    "$project": {
                        "usuario_id": 1,
                        "publicacion_id": 1,
                        "fecha": 1,
                        "likes": 1,
                        "largo_comentario": {"$strLenCP": "$comentario"},
                    }
                }
            ]
        )
    )

    # Q5. Campañas: se desanida publicaciones_asociadas ($unwind) y se aplana metricas_objetivo.
    datos["campana_publicacion"] = list(
        db.campanas.aggregate(
            [
                {"$unwind": "$publicaciones_asociadas"},
                {"$project": {"_id": 0, "campana_id": "$_id", "publicacion_id": "$publicaciones_asociadas"}},
            ]
        )
    )
    datos["campanas"] = list(
        db.campanas.aggregate(
            [
                {
                    "$project": {
                        "nombre": 1,
                        "objetivo": 1,
                        "fecha_inicio": 1,
                        "fecha_fin": 1,
                        "meta_likes": "$metricas_objetivo.likes",
                        "meta_comentarios": "$metricas_objetivo.comentarios",
                        "meta_compartidos": "$metricas_objetivo.compartidos",
                        "n_publicaciones": {"$size": "$publicaciones_asociadas"},
                    }
                }
            ]
        )
    )

    # Q6. Hashtags más usados por categoría ($unwind de arreglo + $group).
    datos["hashtags_categoria"] = list(
        db.publicaciones.aggregate(
            [
                {"$unwind": "$hashtags"},
                {"$group": {"_id": {"categoria": "$categoria", "hashtag": "$hashtags"}, "usos": {"$sum": 1}}},
                {"$project": {"_id": 0, "categoria": "$_id.categoria", "hashtag": "$_id.hashtag", "usos": 1}},
            ]
        )
    )

    for nombre, filas in datos.items():
        print(f"  extraído {nombre:22s} {len(filas):>5d} filas")
    return {k: pd.DataFrame(v) for k, v in datos.items()}


# ---------------------------------------------------------------------------
# 2. TRANSFORMACIÓN
# ---------------------------------------------------------------------------
def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def a_fecha(df: pd.DataFrame, col: str, tabla: str) -> pd.DataFrame:
    df[col] = pd.to_datetime(df[col], format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    malas = int(df[col].isna().sum())
    registrar(tabla, "formato", f"'{col}' texto ISO-8601 -> datetime", "Permite filtros por fecha, jerarquías y series de tiempo en Power BI", len(df))
    if malas:
        registrar(tabla, "limpieza", f"{malas} fechas inválidas en '{col}' quedan nulas", "No se pueden ubicar en el tiempo", malas)
    return df


def agregar_partes_fecha(df: pd.DataFrame, col: str, prefijo: str) -> pd.DataFrame:
    df[f"{prefijo}fecha"] = df[col].dt.normalize()
    df[f"{prefijo}hora"] = df[col].dt.hour
    df[f"{prefijo}dia_semana_num"] = df[col].dt.dayofweek + 1
    df[f"{prefijo}dia_semana"] = df[col].dt.dayofweek.map(dict(enumerate(DIAS)))
    df[f"{prefijo}franja"] = pd.cut(
        df[col].dt.hour, bins=[-1, 5, 11, 17, 23], labels=["Madrugada (0-5)", "Mañana (6-11)", "Tarde (12-17)", "Noche (18-23)"]
    ).astype(str)
    return df


def transformar(df: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    corte = datetime(2025, 7, 15)  # fin del período observado en los datos

    # --- dim_usuarios -------------------------------------------------------
    u = df["usuarios"].rename(columns={"_id": "usuario_id"})
    u["dominio_correo"] = u["correo"].str.split("@").str[-1].str.lower()
    u["tipo_correo"] = np.where(u["dominio_correo"].isin(["gmail.com", "yahoo.com", "hotmail.com", "outlook.com"]), "Personal", "Corporativo")
    u = u.drop(columns="correo")
    registrar("dim_usuarios", "semántica", "correo -> dominio y tipo (Personal/Corporativo); se elimina el correo", "Minimiza datos personales y permite segmentar audiencia", len(u))
    u = a_fecha(u, "fecha_registro", "dim_usuarios")
    u["antiguedad_dias"] = (corte - u["fecha_registro"]).dt.days
    u["cohorte_registro"] = u["fecha_registro"].dt.to_period("Q").astype(str)
    registrar("dim_usuarios", "semántica", "antigüedad en días y cohorte trimestral de registro", "Analizar retención según antigüedad del usuario", len(u))
    for col in ["pais", "genero", "dispositivo"]:
        u[col] = u[col].str.strip()
    registrar("dim_usuarios", "limpieza", "trim de pais/genero/dispositivo", "Evita categorías duplicadas por espacios", len(u))

    # --- dim_publicaciones + puente hashtags --------------------------------
    p = df["publicaciones"].rename(columns={"_id": "publicacion_id", "usuario_id": "autor_id"})
    p = a_fecha(p, "fecha_publicacion", "dim_publicaciones")
    p = agregar_partes_fecha(p, "fecha_publicacion", "pub_")
    registrar("dim_publicaciones", "semántica", "hora, día de semana y franja horaria de publicación", "Responde a la pregunta de negocio sobre horarios de publicación", len(p))

    ph = p[["publicacion_id", "hashtags"]].explode("hashtags").dropna()
    ph["hashtag"] = ph["hashtags"].map(lambda h: sin_tildes(h.strip().lower().lstrip("#")))
    ph = ph.drop(columns="hashtags").drop_duplicates()
    registrar("puente_publicacion_hashtag", "estructural", "arreglo 'hashtags' desanidado a tabla puente (1 fila por hashtag), sin '#', minúsculas, sin tildes", "Power BI no filtra sobre listas; la tabla puente permite relación muchos-a-muchos", len(ph))
    p["n_hashtags"] = p["hashtags"].map(len)
    p = p.drop(columns="hashtags")
    p["engagement"] = p["n_reacciones"] + p["n_comentarios"]
    p["engagement_ponderado"] = p["n_reacciones"] + 2 * p["n_comentarios"] + 0.1 * p["likes_comentarios"]
    registrar("dim_publicaciones", "semántica", "engagement = reacciones + comentarios; ponderado = reacciones + 2*comentarios + 0,1*likes de comentarios", "Un comentario exige más esfuerzo que una reacción; mide popularidad del contenido", len(p))

    # --- fact_interacciones (reacciones + comentarios en una sola tabla) ----
    r = df["reacciones"].rename(columns={"_id": "interaccion_id", "tipo_reaccion": "subtipo"})
    antes = len(r)
    r = a_fecha(r, "fecha", "fact_interacciones")
    r = r.sort_values("fecha").drop_duplicates(["usuario_id", "publicacion_id"], keep="last")
    registrar("fact_interacciones", "limpieza", "reacciones duplicadas (mismo usuario y publicación): se conserva la más reciente", "Una persona tiene una sola reacción vigente por publicación", antes - len(r))
    r["tipo_interaccion"] = "Reacción"
    r["likes"] = 0
    r["largo_comentario"] = np.nan
    r["sentimiento"] = np.where(r["subtipo"].isin(REACCIONES_POSITIVAS), "Positivo", "Negativo")
    registrar("fact_interacciones", "semántica", "tipo de reacción -> sentimiento (Me gusta/Me encanta = Positivo; Me enoja/Me entristece = Negativo)", "KPI de percepción del contenido", len(r))

    c = df["comentarios"].rename(columns={"_id": "interaccion_id"})
    c = a_fecha(c, "fecha", "fact_interacciones")
    c["tipo_interaccion"] = "Comentario"
    c["subtipo"] = "Comentario"
    c["sentimiento"] = "Sin clasificar"

    f = pd.concat([r, c], ignore_index=True)
    registrar("fact_interacciones", "estructural", "reacciones y comentarios unificados en una tabla de hechos (tipo_interaccion)", "Un solo hecho permite medir actividad total por usuario, publicación y tiempo", len(f))
    f = f.merge(p[["publicacion_id", "fecha_publicacion"]], on="publicacion_id", how="left")
    f["consistente_temporal"] = f["fecha"] >= f["fecha_publicacion"]
    f["horas_desde_publicacion"] = ((f["fecha"] - f["fecha_publicacion"]).dt.total_seconds() / 3600).round(1)
    inconsistentes = int((~f["consistente_temporal"]).sum())
    registrar("fact_interacciones", "limpieza", "marca 'consistente_temporal' = interacción posterior a la publicación", "Se detectaron interacciones fechadas antes de su publicación; se marcan (no se borran) para poder filtrarlas y reportar calidad de datos", inconsistentes)
    f = f.drop(columns="fecha_publicacion").rename(columns={"fecha": "fecha_hora"})
    f = agregar_partes_fecha(f, "fecha_hora", "")  # crea 'fecha' (solo día) para relacionar con dim_fecha
    f["mes"] = f["fecha"].dt.to_period("M").astype(str)
    f["huerfana"] = ~f["publicacion_id"].isin(p["publicacion_id"]) | ~f["usuario_id"].isin(u["usuario_id"])
    registrar("fact_interacciones", "limpieza", "validación de integridad referencial usuario/publicación", "Evita hechos sin dimensión en el modelo", int(f["huerfana"].sum()))
    f = f[~f["huerfana"]].drop(columns="huerfana")

    # --- dim_campanas + puente campaña-publicación --------------------------
    k = df["campanas"].rename(columns={"_id": "campana_id"})
    k = a_fecha(k, "fecha_inicio", "dim_campanas")
    k = a_fecha(k, "fecha_fin", "dim_campanas")
    k["etiqueta"] = k["nombre"] + " (" + k["campana_id"].str.replace("camp_", "#") + ")"
    registrar("dim_campanas", "semántica", "etiqueta = nombre + id", f"{len(k) - k['nombre'].nunique()} campañas comparten nombre; sin el id se mezclarían en los gráficos", len(k))
    k["duracion_dias"] = (k["fecha_fin"] - k["fecha_inicio"]).dt.days

    kp = df["campana_publicacion"].drop_duplicates()
    kp = kp[kp["publicacion_id"].isin(p["publicacion_id"])]
    registrar("puente_campana_publicacion", "estructural", "arreglo 'publicaciones_asociadas' desanidado ($unwind) a tabla puente", "Relación muchos-a-muchos campaña-publicación", len(kp))

    # Resultados reales de cada campaña: interacciones en sus publicaciones DENTRO de la ventana de la campaña
    x = kp.merge(k[["campana_id", "fecha_inicio", "fecha_fin"]], on="campana_id").merge(
        f[["publicacion_id", "tipo_interaccion", "sentimiento", "fecha_hora", "usuario_id"]], on="publicacion_id"
    )
    x = x[(x["fecha_hora"] >= x["fecha_inicio"]) & (x["fecha_hora"] <= x["fecha_fin"])]
    res = x.groupby("campana_id").agg(
        reacciones_logradas=("tipo_interaccion", lambda s: (s == "Reacción").sum()),
        reacciones_positivas=("sentimiento", lambda s: (s == "Positivo").sum()),
        comentarios_logrados=("tipo_interaccion", lambda s: (s == "Comentario").sum()),
        usuarios_alcanzados=("usuario_id", "nunique"),
    )
    conteos = ["reacciones_logradas", "reacciones_positivas", "comentarios_logrados", "usuarios_alcanzados"]
    k = k.merge(res, on="campana_id", how="left")
    k[conteos] = k[conteos].fillna(0).astype(int)  # el left join deja NaN (float): se vuelven enteros
    k["pct_logro_likes"] = (k["reacciones_positivas"] / k["meta_likes"]).round(4)
    k["pct_logro_comentarios"] = (k["comentarios_logrados"] / k["meta_comentarios"]).round(4)
    k["pct_logro_promedio"] = ((k["pct_logro_likes"] + k["pct_logro_comentarios"]) / 2).round(4)
    k["estado"] = np.where(k["fecha_fin"] < corte, "Finalizada", "Activa")
    registrar("dim_campanas", "semántica", "logro = interacciones reales en la ventana de la campaña / meta (likes ≈ reacciones positivas)", "Mide el impacto de cada campaña frente a su objetivo. 'compartidos' no existe en los datos: queda como meta sin medición", len(k))

    # --- dim_fecha ------------------------------------------------------------
    fechas = pd.date_range(min(u["fecha_registro"].min(), k["fecha_inicio"].min()).normalize(), corte, freq="D")
    d = pd.DataFrame({"fecha": fechas})
    d["anio"] = d["fecha"].dt.year
    d["mes_num"] = d["fecha"].dt.month
    d["mes"] = d["fecha"].dt.to_period("M").astype(str)
    d["trimestre"] = "T" + d["fecha"].dt.quarter.astype(str)
    d["dia_semana"] = d["fecha"].dt.dayofweek.map(dict(enumerate(DIAS)))
    d["dia_semana_num"] = d["fecha"].dt.dayofweek + 1
    registrar("dim_fecha", "estructural", "tabla calendario continua", "Inteligencia de tiempo en Power BI (tendencias mensuales, comparaciones)", len(d))

    hc = df["hashtags_categoria"].copy()
    hc["hashtag"] = hc["hashtag"].map(lambda h: sin_tildes(h.strip().lower().lstrip("#")))

    return {
        "dim_usuarios": u,
        "dim_publicaciones": p,
        "dim_campanas": k,
        "dim_fecha": d,
        "fact_interacciones": f,
        "puente_publicacion_hashtag": ph,
        "puente_campana_publicacion": kp,
        "agg_hashtags_categoria": hc,
    }


# ---------------------------------------------------------------------------
# 3. CARGA
# ---------------------------------------------------------------------------
def cargar(tablas: dict[str, pd.DataFrame]) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    for nombre, t in tablas.items():
        # utf-8-sig (con BOM): Power BI detecta la codificación y respeta tildes y ñ
        t.to_csv(SALIDA / f"{nombre}.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d %H:%M:%S")
        print(f"  cargado {nombre:28s} {len(t):>5d} filas x {t.shape[1]} columnas")
    pd.DataFrame(BITACORA).to_csv(SALIDA / "bitacora_transformaciones.csv", index=False, encoding="utf-8-sig")
    # Tabla de control: Power BI muestra cuándo corrió el ETL por última vez
    pd.DataFrame(
        [{"ultima_ejecucion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "origen": "Azure Cosmos DB (API MongoDB)", "filas_hechos": len(tablas["fact_interacciones"])}]
    ).to_csv(SALIDA / "control_ejecucion.csv", index=False, encoding="utf-8-sig")
    (SALIDA / "ultima_ejecucion.json").write_text(
        json.dumps({"ejecutado": datetime.now().isoformat(timespec="seconds"), "filas": {k: len(v) for k, v in tablas.items()}}, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    print("1/3 Extracción desde Cosmos DB")
    cliente, db = conectar()
    crudos = extraer(db)
    cliente.close()
    print("2/3 Transformación")
    tablas = transformar(crudos)
    print("3/3 Carga a data/processed")
    cargar(tablas)
    print(f"Listo: {len(BITACORA)} transformaciones registradas en bitacora_transformaciones.csv")


if __name__ == "__main__":
    main()
