"""Jumeau de la remorque : faux panneau JetFileII sur 127.0.0.1, en TCP et en UDP.

Il journalise chaque trame reçue et répond comme le contrôleur Sigma 3000 : aux lectures, avec le
CONFIG.SYS et la fiche système lus sur la remorque réelle (copies dans Sigma3000/) ; aux écritures,
par un accusé de succès. Journal et trames brutes dans banc-essai/banc/.

Mode de réponse réglable par le fichier banc-essai/banc/reply_mode.txt (relu à chaque trame) :
  none      -> ne répond rien (pour voir les réémissions)
  (absent)  -> accusé de succès
  ok:XX     -> accusé avec l'octet d'état XX (hexadécimal)

usage : python banc-essai/faux_panneau.py [port]      (9520 par défaut)
"""
import socket, struct, sys, time, os, threading

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'banc')
os.makedirs(HERE, exist_ok=True)
LOG = os.path.join(HERE, 'faux_panneau.log')
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9520

def log(msg):
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(time.strftime('%H:%M:%S ') + msg + '\n')

def mode():
    try:
        return open(os.path.join(HERE, 'reply_mode.txt')).read().strip()
    except OSError:
        return 'echo'

def checksum(b):
    return sum(b) & 0xffff

def parse(buf, strict=False):
    """Découpe les trames 55 A7/A3 ; renvoie (trames, reste)."""
    out = []
    while len(buf) >= 16:
        i = buf.find(b'\x55')
        if i < 0: return out, b''
        if i: buf = buf[i:]
        if len(buf) < 16: break
        if buf[1] not in (0xa7, 0xa3):
            buf = buf[1:]; continue
        dlen = struct.unpack_from('<H', buf, 4)[0]
        alen = buf[14] * 4
        total = 16 + alen + dlen
        # strict : comme la remorque réelle (mesuré le 27/09), une trame n'est traitée
        # qu'une fois AU MOINS UN OCTET DE PLUS reçu derrière elle
        if len(buf) < total + (1 if strict else 0): break
        out.append(buf[:total]); buf = buf[total:]
    return out, buf

def describe(fr):
    ck, dlen, src, dst, seq = struct.unpack_from('<HHHHH', fr, 2)
    cmd, sub, alen, flag = fr[12], fr[13], fr[14], fr[15]
    args = fr[16:16 + alen * 4]
    data = fr[16 + alen * 4:]
    ok = checksum(fr[4:]) == ck
    return (f"{fr[:2].hex()} ck={ck:04x}({'ok' if ok else 'BAD'}) len={dlen} src={src:04x} dst={dst:04x} "
            f"seq={seq} cmd={cmd:#x}/{sub:#x} flag={flag:#x} args={args.hex()} data[{len(data)}]={data[:64].hex()}"
            + (' ascii=' + repr(data[:80]) if data else ''))

SIGMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'donnees-remorque')
# fichiers « présents sur le panneau » : copies lues sur la remorque réelle
SIGN_FILES = {n: open(os.path.join(SIGMA, n), 'rb').read() for n in ('CONFIG.SYS', 'SEQUENT.SYS')}
# 1/0x10 rend le style d'affichage par défaut (52 octets, forme DEFAULT.SYS) — mesuré sur la remorque
SYSINFO = open(os.path.join(SIGMA, 'DEFAULT.SYS'), 'rb').read()
WRITTEN = {}

def build_reply(fr, args=b'', data=b'', flag=0):
    ck, dlen, src, dst, seq = struct.unpack_from('<HHHHH', fr, 2)
    args = args + b'\0' * (-len(args) % 4)
    hdr = bytearray(fr[:16])
    hdr[1] = 0xa8 if fr[1] == 0xa7 else 0xa4
    struct.pack_into('<HHHH', hdr, 4, len(data), dst, src, seq)
    hdr[14] = len(args) // 4
    hdr[15] = flag
    body = bytes(hdr) + args + data
    return body[:2] + struct.pack('<H', checksum(body[4:])) + body[4:]

def reply_for(fr):
    m = mode()
    if m == 'none': return None
    cmd, sub, alen = fr[12], fr[13], fr[14] * 4
    a = fr[16:16 + alen]
    if cmd == 1 and sub == 0x10:
        return build_reply(fr, data=SYSINFO)
    if cmd == 1 and sub == 2:
        name = a[:12].split(b'\0')[0].decode('latin-1')
        psz, idx = struct.unpack_from('<HH', a, 12)
        content = SIGN_FILES.get(name.upper())
        if content is None:
            log(f'  lecture de {name} : fichier inconnu')
            return build_reply(fr, args=struct.pack('<H', 0x9001), flag=1)
        chunk = content[(idx - 1) * psz: idx * psz]
        return build_reply(fr, args=struct.pack('<II', 0, len(content)), data=chunk)
    if cmd == 2 and sub == 2:
        # écriture d'un fichier système : on le garde, pour qu'une relecture le rende
        name = a[:12].split(b'\0')[0].decode('latin-1').upper()
        total, psz, npk, idx = struct.unpack_from('<IHHI', a, 12)
        part = WRITTEN.setdefault(name, {})
        part[idx] = fr[16 + alen:]
        if len(part) == npk:
            SIGN_FILES[name] = b''.join(part[k] for k in sorted(part))[:total]
            del WRITTEN[name]
            log(f'  {name} enregistré ({total} octets) : {SIGN_FILES[name].hex()}')
    return build_reply(fr, flag=int(m.split(':')[1], 16) if ':' in m else 0)

def serve(conn, addr):
    log(f'CONNEXION {addr}')
    buf = b''
    conn.settimeout(30)
    try:
        while True:
            chunk = conn.recv(65536)
            if not chunk: break
            log(f'RAW[{len(chunk)}] {chunk[:96].hex()}')
            buf += chunk
            frames, buf = parse(buf, strict=True)
            for fr in frames:
                log('TRAME ' + describe(fr))
                with open(os.path.join(HERE, 'frames.bin'), 'ab') as f:
                    f.write(struct.pack('<I', len(fr)) + fr)
                r = reply_for(fr)
                if r:
                    conn.sendall(r); log('REPONSE ' + r.hex())
    except Exception as e:
        log(f'FIN {e!r}')
    conn.close(); log('FERMETURE')

def serve_udp():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind(('127.0.0.1', PORT))
    log(f'ECOUTE UDP 127.0.0.1:{PORT}')
    while True:
        chunk, addr = u.recvfrom(65536)
        log(f'UDP RAW[{len(chunk)}] de {addr} {chunk[:96].hex()}')
        frames, _ = parse(chunk)
        for fr in frames:
            log('UDP TRAME ' + describe(fr))
            with open(os.path.join(HERE, 'frames.bin'), 'ab') as f:
                f.write(struct.pack('<I', len(fr)) + fr)
            r = reply_for(fr)
            if r:
                u.sendto(r, addr); log('UDP REPONSE ' + r.hex())

threading.Thread(target=serve_udp, daemon=True).start()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', PORT)); s.listen(5)
log(f'ECOUTE 127.0.0.1:{PORT}')
while True:
    c, a = s.accept()
    threading.Thread(target=serve, args=(c, a), daemon=True).start()
