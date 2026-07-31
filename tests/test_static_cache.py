"""Servirovani statickych souboru prohlizeci.

Regrese, ktera se tezko hleda: kdyz odpoved nenese Cache-Control, prohlizec si
podle HTTP heuristiky urci platnost sam a novou verzi app.js si nemusi vyzadat
vubec - ani po restartu serveru. Zmeny ve frontendu se pak tvari, jako by se
"nezverejnily".
"""
from api import FreshStaticFiles


def response_for(tmp_path, name="app.js", body="console.log(1)"):
    asset = tmp_path / name
    asset.write_text(body, encoding="utf-8")
    files = FreshStaticFiles(directory=tmp_path)
    scope = {"type": "http", "method": "GET", "headers": []}
    return files.file_response(str(asset), asset.stat(), scope)


def test_static_files_must_be_revalidated(tmp_path):
    assert response_for(tmp_path).headers["cache-control"] == "no-cache"


def test_validators_stay_so_304_still_works(tmp_path):
    """`no-cache` znamena "pred pouzitim se zeptej", ne "neukladej" - bez
    ETagu/Last-Modified by se telo prenaselo pokazde znovu."""
    headers = response_for(tmp_path).headers
    assert headers["etag"]
    assert headers["last-modified"]


def test_changed_file_gets_a_new_etag(tmp_path):
    """Otisk se pocita z mtime a velikosti - po editaci musi byt jiny, jinak by
    prohlizec i po dotazu dostal 304 na starou verzi."""
    import os
    import time

    before = response_for(tmp_path, body="console.log(1)").headers["etag"]
    time.sleep(0.01)
    asset = tmp_path / "app.js"
    asset.write_text("console.log('nova verze')", encoding="utf-8")
    os.utime(asset, (time.time() + 1, time.time() + 1))
    after = response_for(tmp_path, body="console.log('nova verze')").headers["etag"]
    assert before != after
