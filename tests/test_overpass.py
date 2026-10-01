"""Pristup k Overpass API (src/overpass.py).

10/2026: hlavni server na dotazy vracel 504, zrcadla neodpovidala. osmnx pri
504 cekal 55 s a zkousel znovu donekonecna, ulice sly jen na hlavni server
a neuplna odpoved (200 + "runtime error") by se ulozila jako hotova.
"""
import pytest
import requests

import overpass

MAIN, SECOND, THIRD = overpass.MIRRORS


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.ok = status < 400
        self._payload = payload if payload is not None else {"elements": []}

    def json(self):
        return self._payload


@pytest.fixture
def network(monkeypatch):
    """Falesna sit: status[base] = odpovida /status?, answers[base] = Response
    nebo vyjimka pro POST. Zaznamenava, kam se postovalo."""
    state = {"status": {}, "answers": {}, "posted": []}

    def get(url, **_kwargs):
        base = url.removesuffix("/status")
        if not state["status"].get(base, True):
            raise requests.ConnectTimeout("connect timeout")
        return Response()

    def post(url, **_kwargs):
        base = url.removesuffix("/interpreter")
        state["posted"].append(base)
        answer = state["answers"].get(base, Response())
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(overpass, "_last_failure", None)
    return state


def test_a_504_moves_on_to_the_next_mirror(network):
    network["answers"][MAIN] = Response(504)
    network["answers"][SECOND] = Response(200, {"elements": [{"id": 1}]})
    assert overpass.query("out;") == [{"id": 1}]
    assert network["posted"] == [MAIN, SECOND]


def test_a_mirror_that_does_not_answer_status_is_skipped_without_posting(network):
    """Zrcadlo, ktere prijme spojeni a neodpovi, by jinak drzelo dotaz minuty."""
    network["status"][MAIN] = False
    assert overpass.query("out;") == []
    assert network["posted"] == [SECOND]


def test_a_timed_out_query_is_not_accepted_as_a_result(network):
    """Overpass pri vyprseni casu vraci 200 s poznamkou a NEUPLNYMI daty -
    neuplny seznam ulic by se ulozil jako hotovy."""
    network["answers"][MAIN] = Response(200, {
        "elements": [{"id": 1}],
        "remark": "runtime error: Query timed out in \"query\" at line 1 after 181 seconds.",
    })
    network["answers"][SECOND] = Response(200, {"elements": [{"id": 1}, {"id": 2}]})
    assert len(overpass.query("out;")) == 2


def test_when_every_mirror_fails_it_gives_up_and_pauses(network):
    for base in overpass.MIRRORS:
        network["answers"][base] = Response(504)
    with pytest.raises(overpass.OverpassUnavailable):
        overpass.query("out;")
    assert network["posted"] == list(overpass.MIRRORS)

    # chvili nic nezkousi - vyprava by jinak cekala na kazdy zdroj kazde oblasti
    network["posted"].clear()
    with pytest.raises(overpass.OverpassUnavailable):
        overpass.query("out;")
    assert network["posted"] == []


def test_osmnx_no_longer_retries_forever(network, monkeypatch, tmp_path):
    """osmnx 2.1 by pri 504 cekal 55 s a zkousel znovu bez konce; pres modul
    overpass selze hned, jak selzou vsechna zrcadla."""
    from osmnx import _overpass, settings

    for base in overpass.MIRRORS:
        network["answers"][base] = Response(504)
    monkeypatch.setattr(overpass, "_installed", False)
    monkeypatch.setattr(_overpass, "_overpass_request", _overpass._overpass_request)
    monkeypatch.setattr(settings, "cache_folder", str(tmp_path))
    overpass.install_for_osmnx()

    with pytest.raises(overpass.OverpassUnavailable):
        _overpass._overpass_request({"data": "[out:json];node(1);out;"})


def test_osmnx_answers_are_cached_as_before(network, monkeypatch, tmp_path):
    from osmnx import _overpass, settings

    network["answers"][MAIN] = Response(200, {"elements": [{"id": 7}]})
    monkeypatch.setattr(overpass, "_installed", False)
    monkeypatch.setattr(_overpass, "_overpass_request", _overpass._overpass_request)
    monkeypatch.setattr(settings, "cache_folder", str(tmp_path))
    monkeypatch.setattr(settings, "use_cache", True)
    overpass.install_for_osmnx()

    data = {"data": "[out:json];node(7);out;"}
    assert _overpass._overpass_request(data) == {"elements": [{"id": 7}]}
    assert _overpass._overpass_request(data) == {"elements": [{"id": 7}]}
    assert network["posted"] == [MAIN]          # podruhe z cache
