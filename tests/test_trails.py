"""Znacene trasy: co se z OSM relaci bere jako pouzitelne voditko.

Cyklotrasy jsou v Praze v OSM vedene dvojmo - skutecna trasa a navrh. Popisek
z navrhu posila bezce po znaceni, ktere v terenu neexistuje.
"""
import landmarks


def label(**tags):
    return landmarks._trail_label(tags)


def test_planned_cycle_route_is_not_a_guide():
    assert label(route="bicycle", ref="A17", state="proposed") is None


def test_recommended_cycle_route_is_not_a_guide():
    """`recommended` = doporuceni cyklokoordinatora, ne znaceni v terenu.
    Vsech 100 prazskych tras s prefixem X je takovych - X13 se jmenuje
    "Klidova alternativa bud. A13"."""
    assert label(route="bicycle", ref="X13", state="recommended") is None
    assert label(route="bicycle", ref="A235", state="recommended",
                 complete="proposed") is None


def test_existing_cycle_route_survives_even_when_incomplete():
    """`complete=no` znamena dira v trase, ne chybejici znaceni - zbytek
    znaceny je (A1 Vltava, levobrezni trasa)."""
    assert label(route="bicycle", ref="A1", complete="no") == "cyklotrasa A1"
    assert label(route="bicycle", ref="A2", complete="yes") == "cyklotrasa A2"


def test_hiking_routes_are_untouched_by_the_state_filter():
    """Turisticke trasy `state` nepouzivaji (113 ze 113 relaci v Praze) a jsou
    v CR znacene spolehlive - filtr by je jen zbytecne ubiral."""
    assert label(route="hiking", **{"osmc:symbol": "yellow:white:yellow_bar"}) \
        == "zluta turisticka"


def test_trails_cache_name_stays_parsable():
    """_covering_cache_path deli nazev pres split("_") - verze v prefixu proto
    musi byt oddelena pomlckou, jinak se cache prestane nachazet."""
    path = landmarks._cache_path(landmarks.TRAILS_CACHE, 50.075, 14.420, 10)
    assert len(path.stem.split("_")) == 4


# --- selhani stahovani se nesmi zacachovat ---

def test_a_failed_download_is_not_remembered(tmp_path, monkeypatch):
    """Jedno 504 od Overpassu drive pripravilo celou oblast o znacene trasy
    natrvalo: chyba se spolkla a do cache se zapsal prazdny seznam. Protoze
    znacka zlevnuje hrany, zmenilo to i navrzenou trasu."""
    monkeypatch.setattr(landmarks, "BARRIER_DIR", tmp_path)
    monkeypatch.setattr(landmarks, "OVERPASS_MIRRORS", ("https://127.0.0.1:1/nope",))
    monkeypatch.setattr(landmarks, "_CACHE_MEMORY", {})

    assert landmarks.load_trails(50.05, 14.41, 12) == []
    assert list(tmp_path.iterdir()) == []          # nic se nezapsalo
    assert landmarks._CACHE_MEMORY == {}           # ani do pameti


def test_every_mirror_is_tried_before_giving_up(monkeypatch):
    tried = []

    def fail(request, timeout=None):
        tried.append(request.full_url)
        raise OSError("504")

    monkeypatch.setattr(landmarks.urllib.request, "urlopen", fail)
    try:
        landmarks._overpass("out;")
    except landmarks.SourceUnavailable:
        pass
    else:
        raise AssertionError("melo vyhodit SourceUnavailable")
    assert tried == list(landmarks.OVERPASS_MIRRORS)


def test_an_empty_area_is_a_valid_result_not_a_failure(monkeypatch):
    """osmnx hlasi "v okoli nic neni" vyjimkou - to je platna odpoved a cachovat
    se smi, na rozdil od vypadku site."""
    from osmnx._errors import InsufficientResponseError

    class FakeOx:
        @staticmethod
        def features_from_point(*_args, **_kwargs):
            raise InsufficientResponseError("nic")

    assert landmarks._features(FakeOx, 50.0, 14.0, 1000, {"highway": ["primary"]}) is None
