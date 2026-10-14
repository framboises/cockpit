"""codec8.py - Protocole Teltonika Codec 8 / 8 Extended sur TCP.

BIBLIOTHEQUE STANDARD UNIQUEMENT : ce module tourne dans le recepteur
(compte svc-balises, C:\\Python313, sans acces au venv de Cockpit) et il est
aussi importe par Cockpit et par les tests. Ne jamais y ajouter de
dependance.

Reference : https://wiki.teltonika-gps.com/view/Codec
Plan et regles : docs/claude/balises.md.

Sequence TCP :
  1. balise -> 00 0F + IMEI ASCII (15 octets)
  2. serveur -> 01 (accepte) ou 00 (refuse)
  3. balise -> paquet AVL (preambule 0, longueur, codec, N1, records, N2, CRC)
  4. serveur -> nombre de records acceptes sur 4 octets big-endian
     (sans cet ACK la balise renvoie les memes records)
"""

import struct

CODEC_8 = 0x08
CODEC_8E = 0x8E
SUPPORTED_CODECS = (CODEC_8, CODEC_8E)

# Garde-fou memoire : un paquet AVL Teltonika tient en quelques Ko (la
# balise decoupe ses rattrapages). Au-dela, c'est du bruit ou une attaque.
MAX_AVL_DATA_LEN = 64 * 1024


class ProtocolError(ValueError):
    """Trame invalide : la connexion doit etre fermee sans ACK."""


# ---------------------------------------------------------------------------
# CRC-16/IBM (poly 0xA001 reflechi, init 0)
# ---------------------------------------------------------------------------

def crc16_ibm(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


# ---------------------------------------------------------------------------
# Identification
# ---------------------------------------------------------------------------

def parse_imei(buf):
    """(imei, octets consommes), ou None si la trame est incomplete.
    Leve ProtocolError si ce n'est pas une identification Teltonika."""
    if len(buf) < 2:
        return None
    (n,) = struct.unpack(">H", buf[:2])
    if n < 8 or n > 20:
        raise ProtocolError("longueur IMEI invalide (%d)" % n)
    if len(buf) < 2 + n:
        return None
    raw = bytes(buf[2:2 + n])
    if not raw.isdigit():
        raise ProtocolError("IMEI non numerique")
    return raw.decode("ascii"), 2 + n


# ---------------------------------------------------------------------------
# Paquet AVL
# ---------------------------------------------------------------------------

def avl_packet_size(buf):
    """Taille totale du paquet AVL en tete de buf, ou None si l'en-tete
    (8 octets) n'est pas encore arrive. Leve ProtocolError sur un en-tete
    impossible."""
    if len(buf) < 8:
        return None
    pre, length = struct.unpack(">II", buf[:8])
    if pre != 0:
        raise ProtocolError("preambule non nul")
    if length < 3 or length > MAX_AVL_DATA_LEN:
        raise ProtocolError("longueur de donnees invalide (%d)" % length)
    return 8 + length + 4


def parse_avl_packet(buf):
    """(paquet, octets consommes), ou None si incomplet.

    paquet = {"codec": int, "records": [record, ...]}
    record = {"ts_ms", "priority", "lon", "lat", "alt", "angle", "sats",
              "speed", "event_id", "io": {id: valeur}}
    Les valeurs NX (longueur variable, Codec 8E) sont rendues en hexa."""
    size = avl_packet_size(buf)
    if size is None or len(buf) < size:
        return None
    (length,) = struct.unpack(">I", buf[4:8])
    data = bytes(buf[8:8 + length])
    (crc,) = struct.unpack(">I", buf[8 + length:size])
    if crc16_ibm(data) != crc:
        raise ProtocolError("CRC invalide")
    codec = data[0]
    if codec not in SUPPORTED_CODECS:
        raise ProtocolError("codec non gere (0x%02X)" % codec)
    n1 = data[1]
    if data[-1] != n1:
        raise ProtocolError("nombre de records incoherent (%d / %d)" % (n1, data[-1]))
    pos = 2
    records = []
    for _ in range(n1):
        rec, pos = _parse_record(data, pos, codec)
        records.append(rec)
    if pos != len(data) - 1:
        raise ProtocolError("octets en trop dans le paquet")
    return {"codec": codec, "records": records}, size


def _take(data, pos, n):
    if pos + n > len(data):
        raise ProtocolError("record tronque")
    return data[pos:pos + n], pos + n


def _parse_record(data, pos, codec):
    head, pos = _take(data, pos, 24)
    ts_ms, prio, lon, lat, alt, angle, sats, speed = struct.unpack(">QBiihHBH", head)
    ext = codec == CODEC_8E
    id_fmt, id_len = (">H", 2) if ext else (">B", 1)

    def read_id(p):
        raw, p = _take(data, p, id_len)
        return struct.unpack(id_fmt, raw)[0], p

    event_id, pos = read_id(pos)
    _total, pos = read_id(pos)
    io = {}
    for size, fmt in ((1, ">B"), (2, ">H"), (4, ">I"), (8, ">Q")):
        count, pos = read_id(pos)
        for _ in range(count):
            io_id, pos = read_id(pos)
            raw, pos = _take(data, pos, size)
            io[io_id] = struct.unpack(fmt, raw)[0]
    if ext:
        count, pos = read_id(pos)
        for _ in range(count):
            io_id, pos = read_id(pos)
            raw, pos = _take(data, pos, 2)
            (n,) = struct.unpack(">H", raw)
            raw, pos = _take(data, pos, n)
            io[io_id] = raw.hex().upper()
    return {
        "ts_ms": ts_ms, "priority": prio,
        "lon": lon / 1e7, "lat": lat / 1e7, "alt": alt,
        "angle": angle, "sats": sats, "speed": speed,
        "event_id": event_id, "io": io,
    }, pos


def ack_records(n):
    """ACK d'un paquet : nombre de records acceptes, 4 octets big-endian."""
    return struct.pack(">I", n)


# ---------------------------------------------------------------------------
# Format normalise (cles a points, style flespi)
# ---------------------------------------------------------------------------

# AVL ID -> (cle normalisee, conversion). Mapping de la gamme FMB, coherent
# avec les valeurs observees sur l'ATC700 le 12/10/2026 (vecteur du brief) :
# a confirmer avec la "ATC700 AVL ID list". Les IO bruts sont TOUJOURS
# conserves dans "io" : un mapping corrige plus tard se rejoue sur l'historique.
IO_MAP = {
    240: ("movement.status", bool),
    21: ("gsm.signal.level", lambda v: v * 20),
    69: ("gnss.state.enum", int),
    66: ("external.powersource.voltage", lambda v: v / 1000.0),
    67: ("battery.voltage", lambda v: v / 1000.0),
    68: ("battery.current", lambda v: v / 1000.0),
    113: ("battery.level", int),
    181: ("position.pdop", lambda v: v / 10.0),
    182: ("position.hdop", lambda v: v / 10.0),
    16: ("vehicle.mileage", int),
    199: ("segment.distance", int),
}


def position_valid(rec):
    """Fix GNSS exploitable : coordonnees non nulles, satellites vus, et
    etat GNSS 'fix' quand la balise le transmet (IO 69 : 1 = fix,
    2 = sans fix, 3 = veille). Sans fix la balise repete souvent sa
    derniere position : on ne la prend pas pour une nouvelle."""
    if rec["lat"] == 0 and rec["lon"] == 0:
        return False
    if rec["sats"] <= 0:
        return False
    gnss = rec["io"].get(69)
    return gnss is None or gnss == 1


def normalize(rec, imei, codec, peer=None, server_ts=None):
    """Record AVL -> message normalise (meme schema que les messages flespi)."""
    msg = {
        "ident": str(imei),
        "codec.id": codec,
        "timestamp": rec["ts_ms"] / 1000.0,
        "event.priority.enum": rec["priority"],
        "event.enum": rec["event_id"],
        "position.latitude": rec["lat"],
        "position.longitude": rec["lon"],
        "position.altitude": rec["alt"],
        "position.direction": rec["angle"],
        "position.satellites": rec["sats"],
        "position.speed": rec["speed"],
        "position.valid": position_valid(rec),
        "io": {str(k): v for k, v in rec["io"].items()},
    }
    for io_id, (key, conv) in IO_MAP.items():
        if io_id in rec["io"]:
            try:
                msg[key] = conv(rec["io"][io_id])
            except (TypeError, ValueError):
                pass
    if peer:
        msg["peer"] = peer
    if server_ts is not None:
        msg["server.timestamp"] = server_ts
    return msg
