"""Podkladova mapa v UI.

Vychozi OSM podklad nekresli turisticke znacky ani cyklotrasy - tedy prave to,
podle ceho se trasa planuje (RUN_PREFERENCES, atribut `trail`) a podle ceho se
pak bezi. Mapset `outdoor` z Mapy.cz je kresli primo, takze jde na podkladu
zkontrolovat, kudy naplanovana trasa vede.

Dlazdice chodi pres oficialni REST API Mapy.com, ktere vyzaduje API klic
(https://developer.mapy.com - bezplatna kvota staci na osobni pouziti). Bez
klice zustava OSM, aplikace funguje dal.

Klic konzumuje prohlizec, takze se posila do frontendu (`GET /api/basemap`) -
jinak by dlazdice nesly nacist. To je u klientskych map bezny model; klic jde
na developer.mapy.com omezit na konkretni domenu. Proxovat dlazdice pres backend
by slo, ale pro lokalni nastroj to je jen latence navic.
"""
import os

# Overeno 07/2026: bez klice vraci 401, s neplatnym klicem 403 (cesta zije).
TILE_URL = "https://api.mapy.cz/v1/maptiles/{mapset}/{tile_size}/{z}/{x}/{y}?apikey={api_key}"
LOGO_URL = "https://api.mapy.cz/img/api/logo.svg"
ATTRIBUTION = '&copy; <a href="https://mapy.com/" target="_blank">Seznam.cz a.s.</a> a dalsi'

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'


def resolve_api_key(config):
    """Klic z promenne prostredi ma prednost pred configem - stejne jako
    STATSHUNTERS_SHARE_LINK, aby nemusel byt v commitovanem config.yaml."""
    env_key = os.environ.get("MAPY_CZ_API_KEY")
    if env_key:
        return env_key.strip()
    return ((config.get("mapy_cz") or {}).get("api_key") or "").strip()


def basemap_config(config):
    """Podklady pro UI: co ma frontend nabidnout a cim to podepsat."""
    api_key = resolve_api_key(config)
    return {
        "provider": "mapy.cz" if api_key else "osm",
        "api_key": api_key,
        "tile_url": TILE_URL,
        "logo_url": LOGO_URL,
        "attribution": ATTRIBUTION,
        "osm_tile_url": OSM_TILE_URL,
        "osm_attribution": OSM_ATTRIBUTION,
    }
