#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║       API REST — Buscador de Propiedades RE/MAX          ║
║  Expone la función de búsqueda como endpoint HTTP        ║
║  para ser llamada desde Make, N8N, Zapier, etc.          ║
╚══════════════════════════════════════════════════════════╝

INSTALACIÓN LOCAL:
    pip install fastapi uvicorn requests beautifulsoup4

CORRER LOCAL (para pruebas):
    uvicorn api_busqueda:app --host 0.0.0.0 --port 8000
    → Documentación automática: http://localhost:8000/docs

DEPLOY EN RAILWAY:
    1. Subir este archivo + buscar_propiedades.py + requirements_api.txt a GitHub
    2. Crear proyecto en railway.app → Deploy from GitHub
    3. Railway asigna una URL pública automáticamente
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from buscar_propiedades import (
    buscar,
    resolver_ubicacion,
    normalizar_agente,
    TIPOS_PROPIEDAD,
    MONEDAS,
)

app = FastAPI(
    title="RE/MAX Buscador API",
    description="API para buscar propiedades en RE/MAX Argentina desde Make / N8N / WhatsApp",
    version="1.0",
)


# ─────────────────────────────────────────────────────────────
#  Modelos de entrada y salida
# ─────────────────────────────────────────────────────────────

class BusquedaRequest(BaseModel):
    agente: str
    """Nombre de mail RE/MAX del agente (ej: 'guidobadaro' o 'Guido Badaro')"""

    barrios: List[str] = []
    """Lista de barrios. Vacío = sin filtro de ubicación."""

    capital_federal: bool = True
    """True si la búsqueda es en Capital Federal (CABA)."""

    tipos: List[str] = ["todos"]
    """Lista de tipos de propiedad. Ej: ['departamento', 'ph']"""

    ambientes: Optional[int] = None
    """Número exacto de ambientes. None = todos."""

    precio_min: int = 0
    """Precio mínimo (0 = sin límite)."""

    precio_max: int = 0
    """Precio máximo (0 = sin límite)."""

    moneda: str = "usd"
    """'usd' o 'ars'."""

    top_n: int = 5
    """Cuántos resultados devolver (máximo recomendado: 5)."""

    nombre_lead: str = ""
    """Nombre del lead para el que se hace la búsqueda."""

    celular_lead: str = ""
    """Celular del lead."""


class Propiedad(BaseModel):
    titulo: str
    precio: str
    precio_num: float
    ambientes: object
    direccion: str
    link: str


class BusquedaResponse(BaseModel):
    agente: str
    nombre_lead: str
    celular_lead: str
    total_resultados: int
    resultados: List[dict]
    mensaje_whatsapp: str
    """Texto pre-formateado listo para enviar por WhatsApp."""


# ─────────────────────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "servicio": "RE/MAX Buscador API v1.0"}


@app.post("/buscar", response_model=BusquedaResponse)
def buscar_endpoint(req: BusquedaRequest):
    """
    Ejecuta una búsqueda de propiedades en el dominio del agente RE/MAX.
    Devuelve los resultados en JSON + un campo 'mensaje_whatsapp' listo para enviar.
    """

    # ── 1. Normalizar agente ──────────────────────────────────
    agente = normalizar_agente(req.agente)
    if not agente:
        raise HTTPException(status_code=400, detail="Nombre de agente inválido o vacío.")

    # ── 2. Resolver ubicaciones ──────────────────────────────
    ubicaciones = []
    barrios_resueltos = []
    for barrio in req.barrios:
        if barrio.strip():
            locs = resolver_ubicacion(barrio.strip(), req.capital_federal)
            ubicaciones.extend(locs)
            barrios_resueltos.extend([label for _, label in locs])

    # ── 3. Resolver tipos de propiedad ────────────────────────
    tipo_ids = []
    tipos_validos = []
    for t in req.tipos:
        ids = TIPOS_PROPIEDAD.get(t.lower().strip())
        if ids:
            nuevos = [i for i in ids if i not in tipo_ids]
            tipo_ids.extend(nuevos)
            tipos_validos.append(t.lower().strip())

    if not tipo_ids:
        tipo_ids = TIPOS_PROPIEDAD["todos"]
        tipos_validos = ["todos"]

    # ── 4. Ejecutar búsqueda ──────────────────────────────────
    resultados = buscar(
        agente=agente,
        ubicaciones=ubicaciones,
        tipo_ids=tipo_ids,
        ambientes=req.ambientes,
        precio_min=req.precio_min,
        precio_max=req.precio_max,
        moneda=req.moneda.lower(),
        top_n=req.top_n,
        max_paginas=10,
    )

    # ── 5. Armar mensaje para WhatsApp ────────────────────────
    mensaje = _formatear_whatsapp(
        resultados=resultados,
        agente=agente,
        nombre_lead=req.nombre_lead,
        barrios=barrios_resueltos or req.barrios,
        tipos=tipos_validos,
        ambientes=req.ambientes,
        precio_max=req.precio_max,
        moneda=req.moneda.upper(),
    )

    return BusquedaResponse(
        agente=agente,
        nombre_lead=req.nombre_lead,
        celular_lead=req.celular_lead,
        total_resultados=len(resultados),
        resultados=resultados,
        mensaje_whatsapp=mensaje,
    )


# ─────────────────────────────────────────────────────────────
#  Formateador de mensaje WhatsApp
# ─────────────────────────────────────────────────────────────

def _formatear_whatsapp(
    resultados, agente, nombre_lead,
    barrios, tipos, ambientes, precio_max, moneda
) -> str:

    if not resultados:
        return (
            "❌ No encontré propiedades con esos criterios.\n\n"
            "Sugerencias:\n"
            "• Ampliá el presupuesto\n"
            "• Probá con más barrios o tipo 'todos'\n"
            "• Verificá que el nombre del agente sea correcto"
        )

    # Encabezado
    barrios_str = " + ".join(barrios) if barrios else "sin filtro de barrio"
    tipos_str   = ", ".join(tipos)
    amb_str     = f"{ambientes} amb." if ambientes else "cualquier cant. de ambientes"
    precio_str  = f"hasta {precio_max:,} {moneda}" if precio_max else "sin límite de precio"

    lead_str = f" para *{nombre_lead}*" if nombre_lead else ""

    lineas = [
        f"🏠 *Búsqueda RE/MAX{lead_str}*",
        f"📍 {barrios_str}  |  {tipos_str}  |  {amb_str}  |  {precio_str}",
        f"🌐 {agente}.remax.com.ar",
        "",
        f"✅ *Top {len(resultados)} resultados* (mayor a menor precio):",
        "─────────────────────────",
    ]

    for i, r in enumerate(resultados, 1):
        lineas.append(f"*{i}.* {r['titulo']}")
        lineas.append(f"   💲 {r['precio']}   🚪 {r['ambientes']} amb.")
        lineas.append(f"   📍 {r['direccion']}")
        lineas.append(f"   🔗 {r['link']}")
        lineas.append("")

    lineas.append("_Excluye: RESERVADO y EN NEGOCIACIÓN_")

    return "\n".join(lineas)
