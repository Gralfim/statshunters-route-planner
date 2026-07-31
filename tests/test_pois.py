"""Orientacni body a obcerstveni: klasifikace OSM prvku a odstupnovani podle
priblizeni (min_zoom)."""
import pytest

from pois import CATEGORIES, SKIPPED_MEMORIALS, _classify, _min_zoom

BASE_ZOOM = {key: zoom for key, _f, zoom, _l, _i in CATEGORIES}


def node(osm_id=1, lat=50.0, lon=14.0, **tags):
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


def only(elements):
    points = _classify(elements)
    assert len(points) == 1
    return points[0]


@pytest.mark.parametrize("tags, expected", [
    ({"tourism": "viewpoint"}, "viewpoint"),
    ({"man_made": "tower", "tower:type": "observation"}, "tower"),
    ({"natural": "peak"}, "peak"),
    ({"natural": "spring"}, "spring"),
    ({"amenity": "drinking_water"}, "drinking_water"),
    ({"historic": "castle", "name": "Vysehrad"}, "castle"),
    ({"historic": "memorial", "name": "Pamatnik"}, "monument"),
    ({"amenity": "cafe", "name": "Kavarna"}, "refreshment"),
])
def test_osm_tags_map_to_categories(tags, expected):
    assert only([node(**tags)])["kind"] == expected


def test_unrelated_tags_are_ignored():
    assert _classify([node(amenity="parking"), node(shop="bakery")]) == []


def test_water_beats_refreshment_for_a_spring_with_a_pub():
    """Poradi CATEGORIES rozhoduje - bod se zaradi jednou, do te dulezitejsi."""
    assert only([node(natural="spring", amenity="pub", name="U studanky")])["kind"] == "spring"


def test_ways_use_their_centre():
    """Hrad je v OSM plocha; Overpass vraci teziste v 'center'."""
    way = {"type": "way", "id": 7, "center": {"lat": 50.06, "lon": 14.42},
           "tags": {"historic": "castle", "name": "Vysehrad"}}
    point = only([way])
    assert (point["lat"], point["lon"]) == (50.06, 14.42)


def test_element_without_position_is_skipped():
    assert _classify([{"type": "relation", "id": 3, "tags": {"natural": "spring"}}]) == []


# --- odstupnovani podle priblizeni ---

def test_landmarks_appear_sooner_than_restaurants():
    """Rozhledna slouzi k orientaci z dalky, restaurace az v ulici."""
    assert BASE_ZOOM["viewpoint"] < BASE_ZOOM["refreshment"]
    assert BASE_ZOOM["spring"] < BASE_ZOOM["refreshment"]


def test_notable_place_appears_one_zoom_sooner():
    plain = only([node(tourism="viewpoint", name="Vyhlidka")])["min_zoom"]
    notable = only([node(tourism="viewpoint", name="Vyhlidka", wikidata="Q42")])["min_zoom"]
    assert notable == plain - 1
    assert _min_zoom("peak", 12, {"wikipedia": "cs:Petrin"}) == 11


# --- co je sum a co ne ---

@pytest.mark.parametrize("subtype", sorted(SKIPPED_MEMORIALS))
def test_plaques_and_stumbling_stones_are_dropped(subtype):
    """Kameny zmizelych a pametni desticky jsou taky `historic=memorial`, ale
    v dlazbe nebo na zdi - jako orientacni bod za behu nefunguji a je jich rad
    (888 z 1 277 pojmenovanych 'pamatniku' v okoli Karlova nam.)."""
    assert _classify([node(historic="memorial", memorial=subtype, name="Adolf Zikán")]) == []


def test_real_statues_and_war_memorials_survive():
    kept = _classify([
        node(historic="memorial", memorial="statue", name="Jan Hus"),
        node(osm_id=2, lat=50.1, historic="memorial", memorial="war_memorial", name="Padlym"),
        node(osm_id=3, lat=50.2, historic="monument", name="Pomnik"),
        node(osm_id=4, lat=50.3, historic="ruins", name="Zricenina"),
    ])
    assert len(kept) == 4
    assert all(point["kind"] == "monument" for point in kept)


def test_unnamed_restaurant_is_dropped():
    """Bezejmenna restaurace nic nerika; bezejmenna studanka porad znaci vodu."""
    assert _classify([node(amenity="restaurant")]) == []
    assert len(_classify([node(natural="spring")])) == 1


def test_unnamed_point_falls_back_to_its_category_label():
    assert only([node(amenity="drinking_water")])["name"] == "pitna voda"


def test_duplicate_points_collapse():
    """Tentyz bod byva v OSM zaznamenany jako node i jako way."""
    duplicate = [node(natural="spring", name="Studanka"),
                 node(osm_id=2, natural="spring", name="Studanka")]
    assert len(_classify(duplicate)) == 1


def test_points_come_sorted_by_importance():
    points = _classify([
        node(amenity="cafe", name="Kavarna"),
        node(osm_id=2, lat=50.1, tourism="viewpoint", name="Vyhlidka"),
    ])
    assert [p["kind"] for p in points] == ["viewpoint", "refreshment"]


def test_every_category_has_an_icon_and_label():
    for key, _filter, zoom, label, icon in CATEGORIES:
        assert label and icon, key
        assert 10 <= zoom <= 18, key
