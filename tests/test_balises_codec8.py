"""Parser Codec 8 / 8E (recepteur_balises/codec8.py) : vecteur reel de
l'ATC700 (12/10/2026, brief du banc flespi) et exemples du wiki Teltonika."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "recepteur_balises"))
import codec8 as C  # noqa: E402

IMEI_FRAME = bytes.fromhex("000F383636313037303836303138393438")
ATC700 = bytes.fromhex(
    "00000000000000498E01000001A130BCC11801002063A41C941210003700AE1C000000000009"
    "000400F00000150400450100715F00050042000000430FAB0044000000B5000A00B600060000"
    "000000000100007C92")
# Exemples officiels du wiki (https://wiki.teltonika-gps.com/view/Codec)
WIKI_C8 = bytes.fromhex(
    "000000000000003608010000016B40D8EA30010000000000000000000000000000000105021503"
    "010101425E0F01F10000601A014E0000000000000000010000C7CF")
WIKI_C8E = bytes.fromhex(
    "000000000000004A8E010000016B412CEE000100000000000000000000000000000000010005000100"
    "010100010011001D00010010015E2C880002000B000000003544C87A000E000000001DD7E06A0000"
    "0100002994")


def test_imei():
    assert C.parse_imei(IMEI_FRAME) == ("866107086018948", 17)
    assert C.parse_imei(IMEI_FRAME[:10]) is None
    with pytest.raises(C.ProtocolError):
        C.parse_imei(b"\x00\x0fABCDEFGHIJKLMNO")


def test_vecteur_atc700():
    pkt, used = C.parse_avl_packet(ATC700)
    assert used == len(ATC700)
    assert pkt["codec"] == C.CODEC_8E and len(pkt["records"]) == 1
    r = pkt["records"][0]
    assert datetime.fromtimestamp(r["ts_ms"] / 1000, timezone.utc) == \
        datetime(2026, 10, 12, 15, 30, 39, tzinfo=timezone.utc)
    assert (r["lat"], r["lon"], r["alt"], r["angle"], r["sats"], r["speed"]) == \
        (47.9466, 0.212266, 55, 174, 28, 0)
    m = C.normalize(r, "866107086018948", pkt["codec"])
    assert m["movement.status"] is False
    assert m["gsm.signal.level"] == 80
    assert m["gnss.state.enum"] == 1
    assert m["battery.level"] == 95
    assert m["battery.voltage"] == pytest.approx(4.011)
    assert m["position.hdop"] == pytest.approx(0.6)
    assert m["position.pdop"] == pytest.approx(1.0)
    assert m["position.valid"] is True
    assert m["io"]["113"] == 95
    assert C.ack_records(len(pkt["records"])) == bytes.fromhex("00000001")


def test_wiki_codec8():
    pkt, used = C.parse_avl_packet(WIKI_C8)
    assert used == len(WIKI_C8) and pkt["codec"] == C.CODEC_8
    r = pkt["records"][0]
    assert r["io"][21] == 3 and r["io"][1] == 1 and r["io"][66] == 0x5E0F
    assert r["io"][241] == 0x0000601A and r["io"][78] == 0


def test_wiki_codec8e():
    pkt, used = C.parse_avl_packet(WIKI_C8E)
    assert used == len(WIKI_C8E) and pkt["codec"] == C.CODEC_8E
    r = pkt["records"][0]
    assert r["io"][1] == 1 and r["io"][17] == 0x1D and r["io"][16] == 0x015E2C88
    assert r["io"][11] == 0x3544C87A and r["io"][14] == 0x1DD7E06A
    # Pas de fix (coordonnees nulles) : position non valide
    assert C.normalize(r, "1", pkt["codec"])["position.valid"] is False


def test_paquet_incomplet_puis_complet():
    assert C.parse_avl_packet(ATC700[:5]) is None
    assert C.parse_avl_packet(ATC700[:-1]) is None
    pkt, used = C.parse_avl_packet(ATC700 + b"\x00\x00")
    assert used == len(ATC700)


def test_crc_faux_refuse():
    bad = bytearray(ATC700)
    bad[-1] ^= 0xFF
    with pytest.raises(C.ProtocolError):
        C.parse_avl_packet(bytes(bad))


def test_longueur_aberrante_refusee():
    with pytest.raises(C.ProtocolError):
        C.parse_avl_packet(b"\x00\x00\x00\x00\x7f\xff\xff\xff" + b"\x00" * 10)
    with pytest.raises(C.ProtocolError):
        C.parse_avl_packet(b"\x00\x00\x00\x01\x00\x00\x00\x10" + b"\x00" * 20)


def test_sans_fix_gnss():
    pkt, _ = C.parse_avl_packet(ATC700)
    r = dict(pkt["records"][0])
    r["io"] = dict(r["io"])
    r["io"][69] = 2
    assert C.position_valid(r) is False
    r["io"][69] = 1
    r["sats"] = 0
    assert C.position_valid(r) is False
