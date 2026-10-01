"""Pristup k Overpass API: zrcadla, kratke cekani, zadne nekonecne opakovani.

Overpass byva pretizeny (504) nebo nedostupny - 10/2026 hlavni server na dotazy
vracel 504 a obe zrcadla neodpovidala vubec. Tri veci, ktere to delalo horsi:

- osmnx 2.1 pri 504/429 ceka 55 s a zkousi to znovu BEZ omezeni poctu pokusu;
  kdyz se k serveru neda pripojit, ceka 60 s "pauzu" a pak az 180 s na spojeni.
  Planovani v nove oblasti se tak mohlo zaseknout na neurcito.
- Pri vyprseni casu na sve strane vraci Overpass HTTP 200 s poznamkou (remark)
  "runtime error" a NEUPLNYMI daty. osmnx to jen zaloguje - neuplny seznam
  ulic by se ulozil jako hotovy.
- Ulice, vodni toky a pesi graf sly jen na hlavni server, jen znacene trasy
  zkousely zrcadla.

Zde: zrcadla po rade, kazde napred kratkym dotazem na /status; 5xx/429 nebo
remark s chybou = dalsi zrcadlo. Kdyz selzou vsechna, COOLDOWN_S se nic
nezkousi - vyprava jinak cekala na kazdy zdroj kazde oblasti zvlast. Vysledek
selhani se nikam neuklada (to resi volajici), pamatuje se jen cas posledniho
neuspechu.
"""
import json
import time

MIRRORS = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
)
STATUS_TIMEOUT_S = 10   # zrcadlo, ktere neodpovi ani na /status, se preskoci
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 240    # samotny dotaz smi trvat dlouho (Praha ~ desitky sekund)
COOLDOWN_S = 300
RETRY_STATUSES = {429, 500, 502, 503, 504}
HEADERS = {"User-Agent": "statshunters-route-planner"}


class OverpassUnavailable(Exception):
    """Zadne zrcadlo nevratilo uplnou odpoved."""


_last_failure = None


def _host(base):
    return base.split("/")[2]


def _alive(base):
    import requests

    try:
        return requests.get(base + "/status", headers=HEADERS, timeout=STATUS_TIMEOUT_S).ok
    except requests.RequestException:
        return False


def _error_remark(payload):
    remark = payload.get("remark", "") if isinstance(payload, dict) else ""
    return remark if "error" in remark.lower() else None


def post(data):
    """POST na interpreter prvniho zrcadla, ktere vrati uplnou odpoved.

    Vraci (response, payload) - payload je uz rozparsovany JSON. Vyhazuje
    OverpassUnavailable, kdyz selzou vsechna zrcadla (nebo bezi cooldown)."""
    import requests

    global _last_failure
    if _last_failure is not None and time.monotonic() - _last_failure < COOLDOWN_S:
        raise OverpassUnavailable(
            f"Overpass pred chvili neodpovidal, dalsi pokus nejdriv za {COOLDOWN_S} s")

    errors = []
    for base in MIRRORS:
        host = _host(base)
        if not _alive(base):
            errors.append(f"{host}: neodpovida")
            continue
        try:
            response = requests.post(base + "/interpreter", data=data, headers=HEADERS,
                                     timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        except requests.RequestException as error:
            errors.append(f"{host}: {type(error).__name__}")
            continue
        if response.status_code in RETRY_STATUSES or not response.ok:
            errors.append(f"{host}: HTTP {response.status_code}")
            continue
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            errors.append(f"{host}: odpoved neni JSON")
            continue
        remark = _error_remark(payload)
        if remark:
            errors.append(f"{host}: {remark[:80]}")
            continue
        _last_failure = None
        return response, payload

    _last_failure = time.monotonic()
    raise OverpassUnavailable("; ".join(errors))


def query(overpass_ql):
    """Prvky odpovedi na dotaz v Overpass QL."""
    _response, payload = post({"data": overpass_ql})
    return payload.get("elements", [])


_installed = False


def install_for_osmnx():
    """Presmeruje dotazy osmnx (pesi graf, features) pres post().

    Trvale a jednou - server obsluhuje pozadavky ve vice vlaknech a docasne
    nahrazovani by mezi nimi kolidovalo. Cache osmnx zustava v platnosti (klic
    je odvozeny z hlavniho serveru jako v osmnx, takze drive stazene odpovedi
    plati dal). Kdyz osmnx tyto interni funkce nema (jina verze), nedela nic.
    """
    global _installed
    if _installed:
        return
    try:
        import requests
        from osmnx import _http, _overpass, settings
        from osmnx._errors import InsufficientResponseError
        _overpass._overpass_request  # noqa: B018 - jen kontrola, ze existuje
    except (ImportError, AttributeError):
        return

    def overpass_request(data):
        url = settings.overpass_url.rstrip("/") + "/interpreter"
        prepared_url = str(requests.Request("GET", url, params=data).prepare().url)
        cached = _http._retrieve_from_cache(prepared_url)
        if isinstance(cached, dict):
            return cached
        response, payload = post(data)
        if not isinstance(payload, dict):
            raise InsufficientResponseError("Overpass API did not return a dict of results.")
        _http._save_to_cache(prepared_url, payload, response.ok)
        return payload

    _overpass._overpass_request = overpass_request
    _installed = True
