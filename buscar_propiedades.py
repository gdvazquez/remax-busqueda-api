#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║         Buscador de Propiedades RE/MAX Argentina         ║
║  Busca avisos en el dominio personal del agente RE/MAX   ║
╚══════════════════════════════════════════════════════════╝

USO:
    python buscar_propiedades.py

    El script pregunta interactivamente por:
      - Nombre del agente (subdominio RE/MAX)
      - Barrio / ciudad
      - Tipo de propiedad
      - Cantidad de ambientes
      - Presupuesto (moneda, mínimo y máximo)

    Siempre devuelve los 5 primeros resultados disponibles,
    ordenados de MAYOR a MENOR precio, excluyendo propiedades
    en estado RESERVADO o EN NEGOCIACIÓN.

DEPENDENCIAS:
    pip install requests beautifulsoup4
"""

import json
import sys
import unicodedata
from urllib.parse import quote

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Faltan dependencias. Ejecutá:")
    print("   pip install requests beautifulsoup4")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
#  Mapeos
# ─────────────────────────────────────────────────────────────

TIPOS_PROPIEDAD = {
    # ── Grupos amplios ───────────────────────────────────────
    "todos":                [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,26,27,28],
    # ── Departamentos (todos los subtipos) ───────────────────
    "departamento":         [1, 2, 3, 4, 5, 6, 7, 8],
    "departamentos":        [1, 2, 3, 4, 5, 6, 7, 8],
    "depto":                [1, 2, 3, 4, 5, 6, 7, 8],
    "deptos":               [1, 2, 3, 4, 5, 6, 7, 8],
    # ── Subtipos de departamento ─────────────────────────────
    "monoambiente":         [4],
    "monoambientes":        [4],
    "loft":                 [3],
    "lofts":                [3],
    "duplex":               [1, 10],   # depto duplex + casa duplex
    "dúplex":               [1, 10],
    "departamento duplex":  [1],
    "depto duplex":         [1],
    "penthouse":            [5],
    "piso":                 [6],
    "semipiso":             [7],
    "triplex":              [8, 11],
    "tríplex":              [8, 11],
    "departamento triplex": [8],
    # ── Casas ────────────────────────────────────────────
    "casa":                 [9, 10, 11],
    "casas":                [9, 10, 11],
    "casa duplex":          [10],
    "casa triplex":         [11],
    # ── PH ───────────────────────────────────────────────────
    "ph":                   [12],
    # ── Comercial / Oficinas ─────────────────────────────────
    "local":                [17],
    "local comercial":      [17],
    "oficina":              [16],
    "oficinas":             [16],
    "fondo de comercio":    [20],
    "hotel":                [13],
    "edificio":             [14],
    "consultorio":          [27],
    "consultorios":         [27],
    # ── Industrial / Logística ───────────────────────────────
    "galpon":               [22],
    "galpón":               [22],
    "galpones":             [22],
    "deposito":             [28],
    "depósito":             [28],
    # ── Terrenos / Rural ─────────────────────────────────────
    "terreno":              [18],
    "terrenos":             [18],
    "campo":                [19],
    "quinta":               [23],
    "chacra":               [26],
    # ── Otros ────────────────────────────────────────────────
    "cochera":              [21],
    "cocheras":             [21],
    "otros":                [15],
}

MONEDAS = {
    "usd": 1, "u$s": 1, "u$d": 1, "dolar": 1, "dolares": 1, "dólares": 1,
    "ars": 2, "$": 2, "pesos": 2,
}

# id=2 → RESERVADO | id=3 → EN NEGOCIACIÓN
ESTADOS_EXCLUIDOS = {"reserved", "negotiation"}

# API de autocompletado de ubicaciones
URL_AUTOCOMPLETE = "https://api-ar.redremax.com/remaxweb-ar/api/search/findAll/{query}?level=1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}


# ─────────────────────────────────────────────────────────────
#  Normalización del nombre del agente
# ─────────────────────────────────────────────────────────────

def normalizar_agente(nombre):
    """
    Convierte el nombre del agente al formato del subdominio RE/MAX:
      - Minúsculas
      - Sin espacios
      - Sin acentos ni caracteres especiales
    Ejemplos:
      "Guido Badaro"   → "guidobadaro"
      "Noelia Neira"   → "noelianeira"
      "María García"   → "mariagarcia"
    """
    # Quitar acentos normalizando a NFD y descartando marcas diacríticas
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", nombre)
        if unicodedata.category(c) != "Mn"
    )
    # Minúsculas, sin espacios, solo letras y números
    return "".join(c for c in sin_acentos.lower() if c.isalnum())


# ─────────────────────────────────────────────────────────────
#  Resolución de ubicación
# ─────────────────────────────────────────────────────────────

def resolver_ubicacion(barrio, capital_federal):
    """
    Consulta la API de RE/MAX para obtener el ID interno de una ubicación.

    Si capital_federal=True  → toma automáticamente la primera opción que
                               diga "Capital Federal" en el label.
    Si capital_federal=False → descarta todas las opciones de Capital Federal
                               y toma la primera que quede.

    Retorna lista de (location_id, label) o [] si no se encuentra nada.
    """
    try:
        url = URL_AUTOCOMPLETE.format(query=quote(barrio))
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        resultados = data.get("data", {}).get("geoSearch", [])
        if not resultados:
            return []

        def es_capital_federal(loc):
            label = loc.get("label", "").lower()
            return "capital federal" in label

        if capital_federal:
            candidatos = [l for l in resultados if es_capital_federal(l)]
        else:
            candidatos = [l for l in resultados if not es_capital_federal(l)]

        if not candidatos:
            # Fallback: usar el primer resultado sin filtrar
            candidatos = resultados

        loc = candidatos[0]
        label = loc.get("label", barrio).replace("<b>", "").replace("</b>", "")
        loc_id = loc.get("neighborhoodId") or loc.get("cityId")
        print(f"   📍 {label}")
        return [(loc_id, label)]

    except requests.RequestException as e:
        print(f"   ⚠️  No se pudo consultar la API de ubicaciones: {e}")
        return []


# ─────────────────────────────────────────────────────────────
#  Función principal de búsqueda
# ─────────────────────────────────────────────────────────────

def buscar(
    agente,
    ubicaciones=None,
    tipo="todos",
    tipo_ids=None,
    ambientes=None,
    precio_min=0,
    precio_max=0,
    moneda="usd",
    top_n=5,
    max_paginas=10,
):
    """
    Busca propiedades en el dominio del agente RE/MAX.

    Parámetros
    ----------
    agente      : nombre del agente (ej: "gdvazquez" → gdvazquez.remax.com.ar)
    ubicaciones : lista de (location_id, label) ya resueltos por resolver_ubicacion()
                  Si es None o vacío, no filtra por ubicación.
    tipo        : string de tipo (se ignora si se pasa tipo_ids)
    tipo_ids    : lista de IDs de tipo ya resuelta (tiene prioridad sobre tipo)
    ambientes   : número exacto de ambientes (int) o None para todos
    precio_min  : precio mínimo (0 = sin límite inferior)
    precio_max  : precio máximo (0 = sin límite superior)
    moneda      : "usd" o "ars"
    top_n       : cuántos resultados devolver (default 5)
    max_paginas : límite de páginas a recorrer como seguridad (default 10)

    Retorna
    -------
    list de dicts con: titulo, precio, precio_num, ambientes, direccion, link
    Ordenados de mayor a menor precio. RESERVADO y EN NEGOCIACIÓN excluidos.
    """

    if tipo_ids is None:
        tipo_ids = TIPOS_PROPIEDAD.get(tipo.lower().strip(), TIPOS_PROPIEDAD["todos"])
    currency_id = MONEDAS.get(moneda.lower().strip(), 1)
    ubicaciones = ubicaciones or []

    resultados   = []
    total_global = None
    excluidas    = 0

    for p in range(max_paginas):

        # ── Construir query string ────────────────────────────
        params_lista = [
            ("page",           str(p)),
            ("pageSize",       "24"),
            ("sort",           "-priceUsd"),
            ("in:operationId", "1"),
            ("in:eStageId",    "0,1,2,3,4"),
            ("in:typeId",      ",".join(map(str, tipo_ids))),
        ]

        # Formato correcto del sitio: un solo param con IDs separados por coma
        # Ej: locations=in::::25024@Palermo,25006@Belgrano:::
        if ubicaciones:
            partes = ",".join(
                f"{loc_id}@{label.split(',')[0]}"   # solo el nombre corto antes de la coma
                for loc_id, label in ubicaciones
            )
            params_lista.append(("locations", f"in::::{partes}:::"))

        if precio_max > 0:
            params_lista.append(("pricein", f"{currency_id}:{precio_min}:{precio_max}"))
        elif precio_min > 0:
            params_lista.append(("pricein", f"{currency_id}:{precio_min}:0"))

        if ambientes is not None:
            params_lista.append(("eq:totalRooms", str(ambientes)))

        params_lista.append(("viewMode", "listViewMode"))

        qs = "&".join(
            f"{quote(k, safe=':')}={quote(str(v), safe=',:.')}"
            for k, v in params_lista
        )

        # ── Buscar SOLO en el subdominio del agente ──────────
        url = f"https://{agente}.remax.com.ar/listings/buy?{qs}"

        try:
            r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        except requests.RequestException as e:
            print(f"\n❌ No se pudo conectar con {agente}.remax.com.ar")
            print(f"   Error: {e}")
            print(f"   Verificá que '{agente}' sea el nombre correcto del agente.")
            return []

        # Si hubo redirección a www.remax.com.ar el subdominio no existe
        if "remax.com.ar" in r.url and not r.url.startswith(f"https://{agente}."):
            print(f"\n❌ El subdominio '{agente}.remax.com.ar' no existe o redirigió a otra URL.")
            print(f"   URL final: {r.url}")
            print(f"   Verificá el nombre del agente.")
            return []

        if r.status_code != 200 or len(r.text) < 1000:
            print(f"\n❌ El subdominio '{agente}.remax.com.ar' respondió con código {r.status_code}.")
            return []

        # ── Parsear datos ─────────────────────────────────────
        soup          = BeautifulSoup(r.text, "html.parser")
        listings_data = _extraer_listings_json(soup)

        if listings_data is None:
            if p == 0:
                print(f"\n⚠️  No se encontraron datos en la respuesta de {agente}.remax.com.ar")
                print(f"   URL consultada: {url}")
            break

        items = listings_data.get("data", [])
        if not items:
            break

        if total_global is None:
            total_global      = listings_data.get("totalItems", 0)
            total_paginas_api = listings_data.get("totalPages", 1)
            if ubicaciones:
                labels = " + ".join(l for _, l in ubicaciones)
                filtro_loc = f"en {labels}"
            else:
                filtro_loc = "(sin filtro de ubicación)"
            print(f"\n📊 {total_global:,} propiedades encontradas {filtro_loc}.")
            print(f"   Filtrando RESERVADO y EN NEGOCIACIÓN...\n")

        # ── Filtrar y acumular ────────────────────────────────
        for listing in items:
            estado_val = listing.get("listingStatus", {}).get("value", "active")

            if estado_val in ESTADOS_EXCLUIDOS:
                excluidas += 1
                continue

            slug       = listing.get("slug", "")
            titulo     = listing.get("title", "Sin título")
            precio_num = listing.get("price", 0)
            moneda_val = listing.get("currency", {}).get("value", "")
            rooms      = listing.get("totalRooms", "–")
            direccion  = listing.get("displayAddress", "")
            geo        = listing.get("geoLabel", "")
            link       = f"https://{agente}.remax.com.ar/listings/{slug}"

            resultados.append({
                "titulo":     titulo,
                "precio":     f"{precio_num:,.0f} {moneda_val}" if precio_num else "Consultar",
                "precio_num": precio_num or 0,
                "ambientes":  rooms,
                "direccion":  f"{direccion}, {geo}".strip(", "),
                "link":       link,
            })

            if len(resultados) >= top_n:
                break

        if len(resultados) >= top_n:
            break

        total_pags = listings_data.get("totalPages", 1)
        if p >= total_pags - 1:
            break

    if excluidas > 0:
        print(f"   ℹ️  Se excluyeron {excluidas} propiedad(es) RESERVADA(S) o EN NEGOCIACIÓN.")

    return resultados[:top_n]


def _extraer_listings_json(soup):
    """Extrae el JSON de listings embebido en los <script type='application/json'>."""
    for script in soup.find_all("script", {"type": "application/json"}):
        try:
            data = json.loads(script.string or "")
            if "listings-data" in data:
                return data["listings-data"]
        except (json.JSONDecodeError, TypeError):
            continue
    return None


# ─────────────────────────────────────────────────────────────
#  Interfaz interactiva
# ─────────────────────────────────────────────────────────────

def main():
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║         🏠  Buscador de Propiedades RE/MAX               ║")
    print("║    Resultados: top 5 · Mayor a menor precio              ║")
    print("║    Excluye: RESERVADO y EN NEGOCIACIÓN                   ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    # ── Datos del Lead ───────────────────────────────────────
    nombre_lead = input("👤 Nombre del Lead: ").strip()
    celular_lead = input("📱 Celular del Lead: ").strip()
    print()

    # ── Usuario RE/MAX ───────────────────────────────────────
    agente = ""
    while not agente:
        nombre_raw = input("👤 Nombre de mail RE/MAX: ").strip()
        agente = normalizar_agente(nombre_raw)
        if not agente:
            print("   ⚠️  El nombre de usuario es obligatorio.")
        else:
            print(f"   🌐 Dominio a usar: {agente}.remax.com.ar")

    # ── ¿Capital Federal? ────────────────────────────────────
    print()
    cf_raw = input("🏙️  ¿La búsqueda es en Capital Federal? (s/N): ").strip().lower()
    capital_federal = cf_raw in ("s", "si", "sí", "yes", "y")

    # ── Barrio(s) ────────────────────────────────────────────
    print()
    print("📍 Barrio(s) a buscar:")
    print("   • Uno solo  → ej: Palermo")
    print("   • Varios    → ej: Palermo, Belgrano, Recoleta")
    print("   • Enter     → sin filtro de ubicación")
    barrios_raw = input("   Tu elección: ").strip()

    ubicaciones = []
    if barrios_raw:
        terminos = [b.strip() for b in barrios_raw.split(",") if b.strip()]
        for termino in terminos:
            print(f"\n   🔎 Buscando '{termino}'...")
            locs = resolver_ubicacion(termino, capital_federal)
            if locs:
                ubicaciones.extend(locs)
            else:
                print(f"   ⚠️  No se encontró '{termino}' en el sistema de RE/MAX.")

        if ubicaciones:
            todos = " + ".join(l for _, l in ubicaciones)
            print(f"\n   ✅ Ubicaciones a incluir: {todos}")
        else:
            print("   ⚠️  Ningún barrio encontrado. Se buscará sin filtro de ubicación.")

    # ── Tipo de propiedad ────────────────────────────────────
    print()
    print("🏗️  Tipo de propiedad:")
    print("   Departamentos : departamento | monoambiente | loft | duplex |")
    print("                   penthouse | piso | semipiso | triplex")
    print("   Casas         : casa | casa duplex | casa triplex")
    print("   Otros resid.  : ph")
    print("   Comercial     : local | oficina | hotel | edificio |")
    print("                   consultorio | fondo de comercio")
    print("   Industrial    : galpon | deposito")
    print("   Terrenos      : terreno | campo | quinta | chacra")
    print("   Varios        : cochera | otros | todos")
    print("   (podés ingresar varios separados por coma, ej: ph, duplex, casa)")
    tipos_raw = input("   Elegí [todos]: ").strip().lower() or "todos"

    # Resolver múltiples tipos separados por coma
    tipo_ids = []
    tipos_validos = []
    tipos_invalidos = []
    for t in [x.strip() for x in tipos_raw.split(",") if x.strip()]:
        if t in TIPOS_PROPIEDAD:
            nuevos = [i for i in TIPOS_PROPIEDAD[t] if i not in tipo_ids]
            tipo_ids.extend(nuevos)
            tipos_validos.append(t)
        else:
            tipos_invalidos.append(t)

    if tipos_invalidos:
        print(f"   ⚠️  No reconocidos: {', '.join(tipos_invalidos)}. Se ignorarán.")
    if not tipo_ids:
        print("   ⚠️  Ningún tipo válido, se buscará todos.")
        tipo_ids = TIPOS_PROPIEDAD["todos"]
        tipos_validos = ["todos"]

    print(f"   ✅ Tipos a buscar: {', '.join(tipos_validos)}")

    # ── Ambientes ────────────────────────────────────────────
    print()
    amb_str = input("🚪 Ambientes (número exacto, Enter para todos): ").strip()
    ambientes = int(amb_str) if amb_str.isdigit() else None

    # ── Presupuesto ──────────────────────────────────────────
    print()
    print("💰 Presupuesto:")
    moneda = input("   Moneda [USD] (USD/ARS): ").strip().upper() or "USD"
    if moneda not in ("USD", "ARS"):
        moneda = "USD"

    pmin_str = input(f"   Precio mínimo en {moneda} (Enter = 0): ").strip()
    pmax_str = input(f"   Precio máximo en {moneda} (Enter = sin límite): ").strip()
    precio_min = int(pmin_str) if pmin_str.isdigit() else 0
    precio_max = int(pmax_str) if pmax_str.isdigit() else 0

    # ── Búsqueda ─────────────────────────────────────────────
    print()
    print(f"🔍 Buscando en {agente}.remax.com.ar ...")

    resultados = buscar(
        agente=agente,
        ubicaciones=ubicaciones,
        tipo_ids=tipo_ids,
        ambientes=ambientes,
        precio_min=precio_min,
        precio_max=precio_max,
        moneda=moneda.lower(),
        top_n=5,
        max_paginas=10,
    )

    # ── Resultados ───────────────────────────────────────────
    if not resultados:
        print("\n❌ No se encontraron propiedades con esos criterios.")
        print("   Sugerencias:")
        print("   • Verificá que el nombre del agente sea correcto")
        print("   • Ampliá el presupuesto o el tipo de propiedad")
        print("   • Probá sin filtro de barrio")
        input("\nPresioná Enter para cerrar...\n")
        return

    print(f"\n✅ Top {len(resultados)} propiedades (mayor a menor precio):\n")
    print("─" * 65)

    for i, r in enumerate(resultados, 1):
        print(f"{i:>3}. {r['titulo']}")
        print(f"      💲 {r['precio']}   🚪 {r['ambientes']} amb.   print(f"      📍 {r['direccion']}")
        print(f"      print(f"      🔗 {r['link']}")
        print()

    # ── Exportar a txt ───────────────────────────────────────
    print("─" * 65)
    exportar = input("\n💾 ¿Exportar resultados a un archivo .txt? (s/N): ").strip().lower()
    if exportar == "s":
        nombre_archivo = f"resultados_{agente}.txt"
        with open(nombre_archivo, "w", encoding="utf-8") as f:
            f.write(f"Búsqueda RE/MAX — Agente: {agente}\n")
            f.write(f"Lead: {nombre_lead or '(no indicado)'}  |  Cel: {celular_lead or '(no indicado)'}\n")
            f.write(
                f"Barrio: {barrios_raw or '(todos)'}  |  Tipo: {', '.join(tipos_validos)}  |  "
                f"Ambientes: {ambientes or 'todos'}  |  "
                f"Presupuesto: {precio_min:,}–{precio_max:,}–{moneda}\n"
            )
            f.write("Orden: mayor a menor precio | Excluye: RESERVADO, EN NEGOCIACIÓN\n")
            f.write("=" * 65 + "\n\n")
            for i, r in enumerate(resultados, 1):
                f.write(f"{i}. {r['titulo']}\n")
                f.write(f"   {r['precio']}  |  {r['ambientes']} amb.  |  {r['direccion']}\n")
                f.write(f"   {r['link']}\n\n")
        print(f"   ✅ Guardado en '{nombre_archivo}'")

    print("\n¡Listo! 🎉")
    input("\nPresioná Enter para cerrar...\n")


if __name__ == "__main__":
    main()
