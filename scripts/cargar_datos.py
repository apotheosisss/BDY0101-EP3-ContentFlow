"""Carga los JSON del anexo (Forma A - ContentFlow) en Azure Cosmos DB (API MongoDB).

Uso:
    python scripts/cargar_datos.py            # carga (reemplaza) las 5 colecciones
    python scripts/cargar_datos.py --verificar  # solo cuenta documentos

Los documentos se cargan tal como vienen en el anexo (fechas como texto,
arreglos y objetos anidados). La limpieza y conversión de tipos ocurre
después, en etl_contentflow.py, para que el proceso de transformación
quede documentado y sea reproducible.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import BulkWriteError, OperationFailure

RAIZ = Path(__file__).resolve().parents[1]
DATA_RAW = RAIZ / "data" / "raw"

# archivo -> colección
COLECCIONES = {
    "usuarios.json": "usuarios",
    "publicaciones.json": "publicaciones",
    "reacciones.json": "reacciones",
    "comentarios.json": "comentarios",
    "campanas.json": "campanas",
}

# Índices que aceleran los $lookup del ETL
INDICES = {
    "publicaciones": ["usuario_id", "categoria", "fecha_publicacion"],
    "reacciones": ["usuario_id", "publicacion_id"],
    "comentarios": ["usuario_id", "publicacion_id"],
}


def conectar():
    load_dotenv(RAIZ / ".env")
    cliente = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=20000)
    cliente.admin.command("ping")
    return cliente, cliente[os.environ.get("MONGO_DB", "contentflow")]


def insertar_con_reintento(col, docs, lote=100):
    """Inserta por lotes; Cosmos DB puede responder 16500 (429, too many requests)."""
    for i in range(0, len(docs), lote):
        bloque = docs[i : i + lote]
        for intento in range(8):
            try:
                col.insert_many(bloque, ordered=False)
                break
            except BulkWriteError as e:
                pendientes_ids = {
                    err["op"]["_id"] for err in e.details.get("writeErrors", []) if err.get("code") != 11000
                }
                if not pendientes_ids:
                    break
                bloque = [d for d in bloque if d["_id"] in pendientes_ids]
                time.sleep(1 + intento)
            except OperationFailure as e:
                if e.code == 16500:
                    time.sleep(1 + intento)
                    continue
                raise


def main():
    cliente, db = conectar()
    solo_verificar = "--verificar" in sys.argv
    for archivo, nombre in COLECCIONES.items():
        col = db[nombre]
        if not solo_verificar:
            docs = json.loads((DATA_RAW / archivo).read_text(encoding="utf-8"))
            col.drop()
            insertar_con_reintento(col, docs)
            for campo in INDICES.get(nombre, []):
                col.create_index(campo)
        print(f"{nombre:15s} {col.count_documents({}):>5d} documentos")
    cliente.close()


if __name__ == "__main__":
    main()
