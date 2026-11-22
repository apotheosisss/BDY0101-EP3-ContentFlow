"""Demo para la presentación: muestra que el tablero se actualiza con datos nuevos.

    python scripts/demo_en_vivo.py insertar    # agrega interacciones nuevas en Cosmos DB
    python scripts/demo_en_vivo.py revertir    # elimina solo las interacciones de la demo
    python scripts/demo_en_vivo.py estado      # cuenta documentos

Flujo sugerido en clase:
    1. Mostrar el tablero (p. ej. Interacciones = 999, Me encanta = 111).
    2. python scripts/demo_en_vivo.py insertar
    3. powershell -File scripts/actualizar.ps1   (ETL)
    4. En Power BI: Inicio > Actualizar  -> los KPIs cambian.
    5. python scripts/demo_en_vivo.py revertir  (deja la base como estaba)

Los documentos de la demo llevan el campo "origen": "demo" para poder borrarlos
sin tocar los datos originales.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

RAIZ = Path(__file__).resolve().parents[1]
CANTIDAD = 60  # reacciones "Me encanta" + 20 comentarios


def conectar():
    load_dotenv(RAIZ / ".env")
    cliente = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=20000)
    return cliente, cliente[os.environ.get("MONGO_DB", "contentflow")]


def insertar(db) -> None:
    random.seed(7)
    # publicaciones de Tecnología: el cambio se ve claro en "Interacciones por categoría"
    pubs = list(db.publicaciones.find({"categoria": {"$regex": "^Tecnolog"}}, {"_id": 1, "fecha_publicacion": 1}))
    usuarios = [u["_id"] for u in db.usuarios.find({}, {"_id": 1})]
    reacciones, comentarios = [], []
    for i in range(CANTIDAD):
        p = random.choice(pubs)
        fecha = datetime.fromisoformat(p["fecha_publicacion"]) + timedelta(hours=random.randint(1, 72))
        reacciones.append({
            "_id": f"react_demo_{i + 1}", "usuario_id": random.choice(usuarios), "publicacion_id": p["_id"],
            "tipo_reaccion": "Me encanta", "fecha": fecha.strftime("%Y-%m-%dT%H:%M:%S"), "origen": "demo",
        })
    for i in range(20):
        p = random.choice(pubs)
        fecha = datetime.fromisoformat(p["fecha_publicacion"]) + timedelta(hours=random.randint(1, 72))
        comentarios.append({
            "_id": f"comm_demo_{i + 1}", "usuario_id": random.choice(usuarios), "publicacion_id": p["_id"],
            "comentario": "Comentario de demostración en vivo", "fecha": fecha.strftime("%Y-%m-%dT%H:%M:%S"),
            "likes": random.randint(0, 50), "origen": "demo",
        })
    revertir(db, silencioso=True)
    db.reacciones.insert_many(reacciones)
    db.comentarios.insert_many(comentarios)
    print(f"Insertadas {len(reacciones)} reacciones 'Me encanta' y {len(comentarios)} comentarios en publicaciones de Tecnología.")


def revertir(db, silencioso: bool = False) -> None:
    r = db.reacciones.delete_many({"origen": "demo"}).deleted_count
    c = db.comentarios.delete_many({"origen": "demo"}).deleted_count
    if not silencioso:
        print(f"Eliminadas {r} reacciones y {c} comentarios de la demo.")


def estado(db) -> None:
    for col in ["reacciones", "comentarios"]:
        total = db[col].count_documents({})
        demo = db[col].count_documents({"origen": "demo"})
        print(f"{col:12s} total {total:>4d}   de la demo {demo:>3d}")


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else "estado"
    cliente, db = conectar()
    {"insertar": insertar, "revertir": revertir, "estado": estado}[accion](db)
    cliente.close()
