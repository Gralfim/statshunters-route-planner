"""Konfigurace podkladove mapy: odkud se bere API klic a co dostane frontend."""
import pytest

from basemap import TILE_URL, basemap_config, resolve_api_key


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MAPY_CZ_API_KEY", raising=False)


def test_key_comes_from_config():
    assert resolve_api_key({"mapy_cz": {"api_key": "abc"}}) == "abc"


def test_environment_wins_over_config(monkeypatch):
    """Aby klic nemusel byt v commitovanem config.yaml - stejne jako u
    STATSHUNTERS_SHARE_LINK."""
    monkeypatch.setenv("MAPY_CZ_API_KEY", "from-env")
    assert resolve_api_key({"mapy_cz": {"api_key": "from-config"}}) == "from-env"


@pytest.mark.parametrize("config", [{}, {"mapy_cz": None}, {"mapy_cz": {}},
                                    {"mapy_cz": {"api_key": None}},
                                    {"mapy_cz": {"api_key": "   "}}])
def test_missing_key_is_empty_not_an_error(config):
    assert resolve_api_key(config) == ""


def test_without_a_key_the_frontend_falls_back_to_osm():
    config = basemap_config({})
    assert config["provider"] == "osm"
    assert config["api_key"] == ""
    assert config["osm_tile_url"]


def test_with_a_key_the_frontend_gets_everything_it_needs():
    config = basemap_config({"mapy_cz": {"api_key": "abc"}})
    assert config["provider"] == "mapy.cz"
    assert config["logo_url"], "logo Mapy.com je podminka pouziti jejich API"
    assert config["attribution"]


def test_tile_url_has_the_placeholders_the_frontend_replaces():
    for placeholder in ("{mapset}", "{tile_size}", "{api_key}", "{z}", "{x}", "{y}"):
        assert placeholder in TILE_URL
