import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import balises_trace as T  # noqa: E402
from routing import _encode_polyline6  # noqa: E402


def P(lat, lng, t):
    return {"lat": lat, "lng": lng, "t": t}


def test_split_trips_gap_and_jitter():
    pts = [P(47.0, 0.2, 0), P(47.00001, 0.2, 15), P(47.001, 0.2, 30), P(47.01, 0.2, 30 + T.TRIP_GAP_S + 1)]
    trips = T.split_trips(pts)
    assert len(trips) == 2
    assert len(trips[0]) == 2          # le point a 1 m est fondu dans le premier
    assert trips[0][0]["t_end"] == 15


def _straight_road():
    # route nord-sud : 3 sommets, 2 arcs d'environ 111 m
    shape = [(47.0, 0.2), (47.001, 0.2), (47.002, 0.2)]
    return {
        "shape": _encode_polyline6(shape),
        "edges": [{"begin_shape_index": 0, "end_shape_index": 1},
                  {"begin_shape_index": 1, "end_shape_index": 2}],
    }


def test_pairs_matched_follow_shape():
    chunk = [P(47.0, 0.2001, 0), P(47.0015, 0.2001, 15), P(47.002, 0.2001, 30)]
    res = _straight_road()
    res["matched_points"] = [
        {"type": "matched", "edge_index": 0, "distance_along_edge": 0.0, "distance_from_trace_point": 7, "lat": 47.0, "lon": 0.2},
        {"type": "matched", "edge_index": 1, "distance_along_edge": 0.5, "distance_from_trace_point": 7, "lat": 47.0015, "lon": 0.2},
        {"type": "matched", "edge_index": 1, "distance_along_edge": 1.0, "distance_from_trace_point": 7, "lat": 47.002, "lon": 0.2},
    ]
    pairs = T.pairs_from_match(chunk, res)
    assert [s for s, _ in pairs] == [True, True]
    # la 1re paire passe par le sommet intermediaire de la route
    assert [47.001, 0.2] in [[round(c[0], 6), round(c[1], 6)] for c in pairs[0][1]]


def test_pairs_unmatched_and_far_are_guessed():
    chunk = [P(47.0, 0.2, 0), P(47.001, 0.2, 15), P(47.002, 0.2005, 30)]
    res = _straight_road()
    res["matched_points"] = [
        {"type": "matched", "edge_index": 0, "distance_along_edge": 0.0, "distance_from_trace_point": 1, "lat": 47.0, "lon": 0.2},
        {"type": "unmatched", "lat": 47.001, "lon": 0.2},
        {"type": "matched", "edge_index": 1, "distance_along_edge": 1.0, "distance_from_trace_point": 38, "lat": 47.002, "lon": 0.2},
    ]
    pairs = T.pairs_from_match(chunk, res)
    assert [s for s, _ in pairs] == [False, False]
    # point trop loin de la voie : trait devine vers la position brute
    assert pairs[1][1][1] == [47.002, 0.2005]


def test_invented_detour_is_guessed():
    # deux points a 10 m mais recales a ~1 km l'un de l'autre le long de la route
    chunk = [P(47.0, 0.2, 0), P(47.0, 0.20013, 30)]
    res = {
        "shape": _encode_polyline6([(47.0, 0.2), (47.005, 0.2), (47.01, 0.2)]),
        "edges": [{"begin_shape_index": 0, "end_shape_index": 1},
                  {"begin_shape_index": 1, "end_shape_index": 2}],
    }
    res["matched_points"] = [
        {"type": "matched", "edge_index": 0, "distance_along_edge": 0.0, "distance_from_trace_point": 1, "lat": 47.0, "lon": 0.2},
        {"type": "matched", "edge_index": 1, "distance_along_edge": 0.9, "distance_from_trace_point": 1, "lat": 47.0095, "lon": 0.2},
    ]
    assert [s for s, _ in T.pairs_from_match(chunk, res)] == [False]


def test_valhalla_down_gives_dashed_trace(monkeypatch):
    def boom(chunk):
        raise OSError("injoignable")
    monkeypatch.setattr(T, "_valhalla_match", boom)
    monkeypatch.setattr(T, "_down_until", 0.0)
    T._cache.clear()
    pts = [P(47.0 + i * 0.001, 0.2, i * 15) for i in range(5)]
    segs, status = T.build_segments(pts)
    assert status == "off"
    assert len(segs) == 1 and segs[0]["m"] is False and len(segs[0]["c"]) == 5
    monkeypatch.setattr(T, "_down_until", 0.0)


def test_segments_stay_continuous(monkeypatch):
    def fake(chunk):
        res = _straight_road()
        res["matched_points"] = [
            {"type": "matched", "edge_index": 0, "distance_along_edge": 0.0, "distance_from_trace_point": 1, "lat": 47.0, "lon": 0.2},
            {"type": "unmatched", "lat": 47.001, "lon": 0.2},
            {"type": "matched", "edge_index": 1, "distance_along_edge": 0.5, "distance_from_trace_point": 1, "lat": 47.0015, "lon": 0.2},
            {"type": "matched", "edge_index": 1, "distance_along_edge": 1.0, "distance_from_trace_point": 1, "lat": 47.002, "lon": 0.2},
        ]
        return res
    monkeypatch.setattr(T, "_valhalla_match", fake)
    monkeypatch.setattr(T, "_down_until", 0.0)
    T._cache.clear()
    pts = [P(47.0, 0.2, 0), P(47.001, 0.2003, 15), P(47.0015, 0.2, 30), P(47.002, 0.2, 45)]
    segs, status = T.build_segments(pts)
    assert status == "ok"
    assert [s["m"] for s in segs] == [False, True]
    for a, b in zip(segs, segs[1:]):
        assert a["c"][-1] == b["c"][0]
