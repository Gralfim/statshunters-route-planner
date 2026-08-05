"""Jizdni rad z GTFS: ktere spoje do nej patri a jaky z nich vyjde interval.

Modelovy pripad je S6 z ostreho feedu (07/2026): tyz vlak vedeny dvakrat -
bezna varianta ze Smichova a vylukova ze Zlichova, odjezdy o 2 minuty vedle
sebe. Kdyz se platnost service_id ignoruje, secte se obojí a median rozestupu
sahne po tech dvou minutach misto po skutecnych triceti.
"""
import datetime
import json
import zipfile

import pytest

import transit

WEDNESDAY = datetime.date(2026, 8, 5)   # referencni vsedni den v testech

FEED = {
    "feed_info.txt": [
        "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date",
        "TEST,https://example.invalid,cs,20260801,20260831",
    ],
    "routes.txt": [
        "route_id,route_short_name,route_type,is_night",
        "L1,S6,2,0",
    ],
    "calendar.txt": [
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date",
        # bezny provoz - v referencni den UZ neplati
        "NORMAL,1,1,1,1,1,0,0,20260101,20260706",
        # vyluka - v referencni den plati
        "VYLUKA,1,1,1,1,1,0,0,20260707,20261019",
    ],
    "calendar_dates.txt": ["service_id,date,exception_type"],
    "trips.txt": [
        "route_id,service_id,trip_id,direction_id",
        "L1,NORMAL,N1,0", "L1,NORMAL,N2,0", "L1,NORMAL,N3,0",
        "L1,VYLUKA,V1,0", "L1,VYLUKA,V2,0", "L1,VYLUKA,V3,0",
        "L1,VYLUKA,VKRATKY,0",
    ],
    "stops.txt": [
        "stop_id,stop_name,stop_lat,stop_lon",
        "U1,Praha-Smichov,50.0600,14.4090",
        "U2,Praha-Zlichov,50.0480,14.4030",
        "U3,Praha-Hlubocepy,50.0410,14.3950",
        "U4,Zbuzany,50.0130,14.2560",
    ],
    "stop_times.txt": [
        "trip_id,stop_sequence,stop_id,arrival_time,departure_time",
        # bezny provoz: ze Smichova v 5:16, 5:46, 6:16
        "N1,1,U1,05:16:00,05:16:00", "N1,2,U3,05:30:00,05:30:00", "N1,3,U4,05:45:00,05:45:00",
        "N2,1,U1,05:46:00,05:46:00", "N2,2,U3,06:00:00,06:00:00", "N2,3,U4,06:15:00,06:15:00",
        "N3,1,U1,06:16:00,06:16:00", "N3,2,U3,06:30:00,06:30:00", "N3,3,U4,06:45:00,06:45:00",
        # vyluka: ze Zlichova o dve minuty pozdeji
        "V1,1,U2,05:18:00,05:18:00", "V1,2,U3,05:32:00,05:32:00", "V1,3,U4,05:47:00,05:47:00",
        "V2,1,U2,05:48:00,05:48:00", "V2,2,U3,06:02:00,06:02:00", "V2,3,U4,06:17:00,06:17:00",
        "V3,1,U2,06:18:00,06:18:00", "V3,2,U3,06:32:00,06:32:00", "V3,3,U4,06:47:00,06:47:00",
        # zkraceny spoj - platny, ale nesmi delat reprezentanta linky
        "VKRATKY,1,U2,07:18:00,07:18:00", "VKRATKY,2,U3,07:32:00,07:32:00",
    ],
}


def write_feed(path, **overrides):
    files = dict(FEED, **overrides)
    with zipfile.ZipFile(path, "w") as zip_file:
        for name, lines in files.items():
            zip_file.writestr(name, "\n".join(lines) + "\n")
    return path


@pytest.fixture
def feed(tmp_path, monkeypatch):
    monkeypatch.setattr(transit, "GRAPH_CACHE", tmp_path / "transit_graph.json")
    return write_feed(tmp_path / "gtfs.zip")


def graph_of(feed, today=WEDNESDAY):
    return transit.build_transit_graph(feed, today=today)


def line_stops(graph, line_key="L1|0"):
    edges = [edge for edge in graph["edges"] if edge[5] == line_key]
    return [graph["stops"][edges[0][0]][0]] + [graph["stops"][edge[1]][0] for edge in edges]


# --- platnost jizdniho radu ---

def test_variant_from_another_period_does_not_inflate_the_interval(feed):
    """Jadro chyby: 30minutovy interval se hlasil jako 2,5 min, protoze se
    scitaly odjezdy bezneho provozu a vyluky (lisi se o 2 minuty)."""
    assert graph_of(feed)["headways"]["L1|0"][0] == 30.0


def test_the_line_is_described_by_the_variant_that_runs(feed):
    """Itinerar posilal bezce na Smichov, ackoli behem vyluky vlak jede ze
    Zlichova."""
    assert line_stops(graph_of(feed))[0] == "Praha-Zlichov"


def test_a_short_working_does_not_become_the_face_of_the_line(feed):
    """Mezi platnymi spoji rozhoduje pocet zastavek - zkraceny spoj by linku
    pripravil o kus site."""
    assert line_stops(graph_of(feed))[-1] == "Zbuzany"


def test_cancelled_day_removes_the_service(feed, tmp_path):
    """calendar_dates.txt s vyjimkou 2: v ten den sluzba nejede."""
    path = write_feed(tmp_path / "cancelled.zip", **{
        "calendar_dates.txt": ["service_id,date,exception_type", "VYLUKA,20260805,2"],
    })
    assert "L1|0" not in graph_of(path)["headways"]


def test_extra_day_adds_a_service(feed, tmp_path):
    """Vyjimka 1 prida spoj i mimo platnost calendar.txt - bez cteni
    calendar_dates.txt by tenhle provoz zmizel."""
    path = write_feed(tmp_path / "extra.zip", **{
        "calendar_dates.txt": ["service_id,date,exception_type", "NORMAL,20260805,1"],
    })
    assert line_stops(graph_of(path))[0] == "Praha-Smichov"


# --- referencni dny ---

def test_reference_dates_are_the_next_weekday_and_saturday(feed):
    with zipfile.ZipFile(feed) as zip_file:
        weekday, weekend = transit._reference_dates(zip_file, datetime.date(2026, 8, 3))
    assert (weekday.isoformat(), weekend.isoformat()) == ("2026-08-05", "2026-08-08")


def test_expired_feed_falls_back_to_the_last_days_it_covers(feed):
    """Kdyz se aktualizace nepovede, stavi se na poslednim dni uvnitr platnosti -
    stary rad je porad lepsi nez zadny."""
    with zipfile.ZipFile(feed) as zip_file:
        weekday, weekend = transit._reference_dates(zip_file, datetime.date(2026, 9, 20))
    assert weekday.isoformat() == "2026-08-26" and weekday.weekday() == transit.REFERENCE_WEEKDAY
    assert weekend.isoformat() == "2026-08-29" and weekend.weekday() == transit.REFERENCE_WEEKEND


# --- aktualizace feedu ---

def test_a_valid_feed_is_not_downloaded_again(feed, monkeypatch):
    monkeypatch.setattr(transit, "GTFS_ZIP", feed)
    monkeypatch.setattr(transit, "download_gtfs", lambda *_: pytest.fail("stahovalo se zbytecne"))
    transit.refresh_gtfs(WEDNESDAY)


def test_an_expired_feed_is_downloaded(feed, monkeypatch):
    """Drive se stahovalo jen kdyz soubor vubec nebyl - planovac tak jel na
    libovolne starem radu (namereno pul mesice po konci platnosti)."""
    called = []
    monkeypatch.setattr(transit, "GTFS_ZIP", feed)
    monkeypatch.setattr(transit, "download_gtfs", lambda *_: called.append(True))
    transit.refresh_gtfs(datetime.date(2026, 9, 1))
    assert called


def test_a_failed_update_keeps_the_old_timetable(feed, monkeypatch, capsys):
    def boom(*_args):
        raise OSError("sit je pryc")

    monkeypatch.setattr(transit, "GTFS_ZIP", feed)
    monkeypatch.setattr(transit, "download_gtfs", boom)
    transit.refresh_gtfs(datetime.date(2026, 9, 1))       # nesmi vyhodit
    assert "VAROVANI" in capsys.readouterr().err


# --- cache grafu ---

def test_graph_is_rebuilt_after_the_feed_changes(feed, tmp_path, monkeypatch):
    monkeypatch.setattr(transit, "GTFS_ZIP", feed)
    graph = graph_of(feed)
    assert transit._graph_matches_feed(graph, feed, today=WEDNESDAY)

    write_feed(feed, **{"feed_info.txt": [
        "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date",
        "TEST,https://example.invalid,cs,20260901,20260930",
    ]})
    assert not transit._graph_matches_feed(graph, feed, today=WEDNESDAY)


def test_graph_is_rebuilt_once_its_reference_day_passes(feed):
    graph = graph_of(feed, today=WEDNESDAY)
    assert not transit._graph_matches_feed(graph, feed, today=datetime.date(2026, 8, 12))


def test_expired_feed_does_not_force_a_rebuild_every_time(feed):
    """Referencni dny prosleho feedu jsou nutne v minulosti - prestavba by nic
    nezmenila, jen by bezela porad dokola."""
    graph = graph_of(feed, today=datetime.date(2026, 9, 20))
    assert transit._graph_matches_feed(graph, feed, today=datetime.date(2026, 9, 21))


def test_graph_records_what_it_was_built_from(feed):
    graph = graph_of(feed)
    assert graph["built_for"] == ["2026-08-05", "2026-08-08"]
    assert json.loads(transit.GRAPH_CACHE.read_text(encoding="utf-8"))["version"] \
        == transit.GRAPH_VERSION
