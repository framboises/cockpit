"""Dialogue reseau avec une remorque PMV (controleur Sigma 3000, JetFileII, TCP 9520).

Portage de docs/pmv/reference/envoyer.py (classe Panneau), valide sur remorque le
27/09/2026. Sans Flask ni Mongo : pmv.py l'appelle depuis un thread de travail.

REGLES DE DIALOGUE -- NON NEGOCIABLES (chacune vient d'un incident reel sur remorque,
voir docs/pmv/docs/SIGMA3000_PROTOCOLE.md section 10 et docs/pmv/LISEZ-MOI.md 4.2) :

 1. Ajouter un octet 00 apres CHAQUE trame envoyee. Le panneau ne traite une trame
    qu'apres avoir recu au moins un octet de plus : sans lui, aucune reponse.
 2. Numero de sequence UNIQUE sur toute la session TCP (1, 2, 3..., jamais de retour
    a 1). Sinon un accuse tardif est pris pour la reponse suivante : c'est ce qui a
    detruit la liste de lecture d'une remorque le 27/09.
 3. Reponse qui ne correspond pas (sequence, commande, somme) : l'IGNORER et continuer
    d'ecouter, sans renvoyer.
 4. Delais : 1,5 s au 1er essai, puis 10 s, 3 essais au total, trame renvoyee
    IDENTIQUE. Duree d'une operation toujours bornee (echeance globale).
 5. Validation d'une reponse exactement comme check_reply (pmv_protocole).
 6. Liste blanche des commandes appliquee dans le code : 1/0x02, 2/0x08, 2/0x02.
    JAMAIS 2/0x0C. Toute autre commande leve une exception AVANT l'envoi.
 7. Avant tout envoi : lire CONFIG.SYS et arreter si la dalle n'est pas 96 x 64.
 8. Sauvegarde de l'affichage d'avant (SEQUENT.SYS) avant d'ecrire -- geree par pmv.py.
 9. Une seule operation a la fois par remorque -- verrou gere par pmv.py.
10. La memoire D: est une flash qui s'use : pas de renvoi en boucle -- gere par pmv.py.
"""
import random
import socket
import struct
import time

import pmv_protocole as pp

DELAIS_ESSAIS = (1.5, 10.0, 10.0)
DELAI_CONNEXION_S = 10.0
ECHEANCE_OPERATION_S = 180.0


class EchecPanneau(Exception):
    """Echec d'une operation. `code` alimente le message en clair cote interface."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _hexa(b, n=48):
    h = b[:n].hex()
    return h + ("..." if len(b) > n else "")


class Panneau:
    """Une session TCP avec une remorque. A utiliser dans un `with` (fermeture garantie)."""

    def __init__(self, hote, port=pp.PORT, dst=pp.DST, journal=None,
                 echeance_s=ECHEANCE_OPERATION_S, delais=DELAIS_ESSAIS):
        self.hote, self.port, self.dst = hote, port, dst
        self.delais = tuple(delais)
        self.fin = time.monotonic() + echeance_s
        self._journal = journal
        self._seq = 0
        self.tampon = b""
        self.s = None
        self.log("connexion TCP %s:%d" % (hote, port))
        try:
            self.s = socket.create_connection((hote, port), timeout=DELAI_CONNEXION_S)
        except OSError as e:
            raise EchecPanneau("injoignable", "connexion impossible : %s" % e)
        self.log("connecte")

    # ---------------------------------------------------------- outillage
    def log(self, texte, sens="info"):
        if self._journal:
            try:
                self._journal(sens, texte)
            except Exception:
                pass

    def fermer(self):
        if self.s is not None:
            try:
                self.s.close()
            except OSError:
                pass
            self.s = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fermer()
        return False

    def _reste(self):
        return self.fin - time.monotonic()

    def _lire_reponse(self):
        """Lit une reponse complete (55 A8), en sautant d'eventuels octets parasites."""
        fin = time.monotonic() + self.s.gettimeout()
        while True:
            i = self.tampon.find(b"\x55")
            while i >= 0 and i + 1 < len(self.tampon) and self.tampon[i + 1] not in (0xA8, 0xA4):
                i = self.tampon.find(b"\x55", i + 1)
            if i > 0:
                self.tampon = self.tampon[i:]
            elif i < 0 and self.tampon:
                self.tampon = self.tampon[-1:] if self.tampon[-1:] == b"\x55" else b""
            if i >= 0 and len(self.tampon) >= 16:
                total = 16 + self.tampon[14] * 4 + struct.unpack_from("<H", self.tampon, 4)[0]
                if len(self.tampon) >= total:
                    rep, self.tampon = self.tampon[:total], self.tampon[total:]
                    return rep
            reste = fin - time.monotonic()
            if reste <= 0:
                raise socket.timeout()
            self.s.settimeout(reste)
            morceau = self.s.recv(65536)
            if not morceau:
                raise EchecPanneau("connexion_fermee", "le panneau a ferme la connexion")
            self.tampon += morceau

    # ---------------------------------------------------------- une commande
    def commande(self, cmd, sub, args=b"", data=b""):
        """Envoie une trame, attend l'accuse ; rend la reponse. Numero de sequence unique
        sur toute la session (regle 2), octet 00 final (regle 1), 3 essais (regle 4)."""
        pp.verifier_commande(cmd, sub)                     # regle 6, avant toute emission
        self._seq = self._seq % 0xFFFF + 1
        seq = self._seq
        fr = pp.frame(cmd, sub, args, data, seq=seq, dst=self.dst)
        for essai, attente in enumerate(self.delais, start=1):
            reste = self._reste()
            if reste <= 0:
                raise EchecPanneau("delai_depasse", "duree maximale de l'operation depassee")
            self.s.settimeout(max(0.05, min(attente, reste)))
            self.log("%d/0x%02x seq=%d essai %d : %s" % (cmd, sub, seq, essai, _hexa(fr)), "->")
            try:
                self.s.sendall(fr + b"\x00")
            except OSError as e:
                raise EchecPanneau("connexion_fermee", "envoi impossible : %s" % e)
            code = 2
            while code == 2:
                try:
                    rep = self._lire_reponse()
                except socket.timeout:
                    self.log("pas de reponse a %d/0x%02x seq=%d (essai %d/%d)"
                             % (cmd, sub, seq, essai, len(self.delais)))
                    break
                except OSError as e:
                    raise EchecPanneau("connexion_fermee", "connexion perdue : %s" % e)
                code = pp.check_reply(rep, seq, cmd, sub)
                if code == 2:
                    # regle 3 : typiquement l'accuse TARDIF d'une trame deja renvoyee
                    self.log("reponse ignoree (ne correspond pas a seq=%d) : %s" % (seq, _hexa(rep, 16)), "<-")
                else:
                    self.log(_hexa(rep), "<-")
            if code == 2:
                continue
            if code == 0:
                return rep
            raise EchecPanneau("refus_panneau", "le panneau refuse %d/0x%02x : code 0x%04x" % (cmd, sub, code))
        raise EchecPanneau("ne_repond_pas", "aucune reponse valable a %d/0x%02x apres %d essais"
                           % (cmd, sub, len(self.delais)))

    # ---------------------------------------------------------- operations
    def lire_fichier_systeme(self, nom):
        """Commande 1/0x02, paquet par paquet. Rend le contenu, ou None si le panneau refuse
        le 1er paquet (le fichier n'existe pas). Une absence de reponse reste une erreur."""
        contenu, total, idx = b"", None, 1
        while True:
            try:
                rep = self.commande(1, 0x02, pp.read_sys_args(nom, pp.PSZ, idx))
            except EchecPanneau as e:
                if idx == 1 and e.code == "refus_panneau":
                    self.log("%s absent du panneau" % nom)
                    return None
                raise
            t, morceau = pp.parse_read_reply(rep)
            if total is None:
                total = t
                if total == 0:
                    return None
            contenu += morceau
            if len(contenu) >= total or not morceau:
                return contenu[:total]
            idx += 1

    def _ecrire_trames(self, trames, progression=None):
        for i, fr in enumerate(trames):
            alen = fr[14] * 4
            self.commande(fr[12], fr[13], fr[16:16 + alen], fr[16 + alen:])
            if progression:
                progression(i + 1, len(trames))

    def ecrire_fichier(self, chemin, contenu, progression=None):
        """Commande 2/0x08 par paquets de 768 octets. Les trames sont fabriquees par la
        fonction de reference, puis re-emises avec le numero de sequence de la session."""
        self._ecrire_trames(pp.write_file_frames(chemin, contenu, dst=self.dst), progression)

    def ecrire_fichier_systeme(self, nom, contenu):
        """Commande 2/0x02 (ex. SEQUENT.SYS)."""
        self._ecrire_trames(pp.write_sys_frames(nom, contenu, dst=self.dst))


# ---------------------------------------------------------------- operations completes
#
# `etape(code, etat, detail=None, progression=None)` : rappel de progression, etat parmi
# "en_cours", "ok", "ko". Codes d'etape : dalle, affichage, sauvegarde, image, affiche.
# L'etape "connexion" est geree par l'appelant (elle precede la creation du Panneau).

def _rien(*_a, **_k):
    pass


def verifier_dalle(p, etape=_rien):
    """Regle 7 : lire CONFIG.SYS, arreter si la dalle n'est pas 96 x 64 (rien n'est modifie)."""
    etape("dalle", "en_cours")
    config = p.lire_fichier_systeme("CONFIG.SYS")
    dims = pp.parse_config_dims(config)
    if dims is None:
        etape("dalle", "ko", "CONFIG.SYS illisible")
        raise EchecPanneau("config_illisible", "dimensions de la dalle inconnues : rien n'a ete envoye")
    if dims != (pp.W, pp.H):
        etape("dalle", "ko", "dalle %d x %d" % dims)
        raise EchecPanneau("dalle_incompatible", "dalle %d x %d : rien n'a ete envoye" % dims)
    etape("dalle", "ok", "96 x 64")
    return dims


def lire_affichage(p):
    """Lit SEQUENT.SYS -> (contenu brut ou None, liste des noms joues ou None si illisible)."""
    contenu = p.lire_fichier_systeme("SEQUENT.SYS")
    if not pp.is_sequent(contenu):
        return contenu, None
    return contenu, pp.parse_sequent(contenu)


def operation_test(p, etape=_rien):
    """Lecture seule : dalle et fichier actuellement joue. Aucune trame 2/0x.. n'est emise."""
    dims = verifier_dalle(p, etape)
    etape("affichage", "en_cours")
    contenu, noms = lire_affichage(p)
    if noms is None:
        etape("affichage", "ko", "liste de lecture illisible")
    else:
        etape("affichage", "ok", "affiche : " + (", ".join(noms) or "(liste vide)"))
    return {"dalle": list(dims), "noms": noms, "sequent": contenu if noms is not None else None}


def operation_envoi(p, fichier, sauvegarde_existante=None, sans_sauvegarde=False, etape=_rien):
    """Envoi complet, exactement la sequence des vecteurs :
    CONFIG.SYS (lecture) -> SEQUENT.SYS (lecture, sauvegarde) -> fichier-message (2/0x08)
    -> SEQUENT.SYS (2/0x02, l'image s'affiche).

    Regle de sauvegarde (LISEZ-MOI section 6) : on garde l'affichage d'AVANT notre PREMIER
    envoi. Si la liste lue contient deja PMVED.Nmg, la sauvegarde existante n'est pas
    ecrasee ; sinon (quelqu'un est repasse par Sigma), la nouvelle lecture la remplace.
    Rend {"nouvelle_sauvegarde": bytes|None, "noms_avant": [...]|None}."""
    verifier_dalle(p, etape)

    etape("sauvegarde", "en_cours")
    contenu, noms = lire_affichage(p)
    nouvelle = None
    if noms is None:
        if not sans_sauvegarde:
            etape("sauvegarde", "ko", "liste de lecture illisible")
            raise EchecPanneau("sauvegarde_impossible",
                               "affichage actuel illisible : rien n'a ete envoye")
        etape("sauvegarde", "ok", "envoi sans sauvegarde (demande explicite)")
    elif pp.NOM_MESSAGE in noms:
        if pp.is_sequent(sauvegarde_existante):
            etape("sauvegarde", "ok", "sauvegarde d'origine conservee : "
                  + ", ".join(pp.parse_sequent(sauvegarde_existante)))
        else:
            etape("sauvegarde", "ok", "affiche deja " + pp.NOM_MESSAGE + " : affichage d'origine inconnu")
    else:
        nouvelle = contenu
        etape("sauvegarde", "ok", "affichait : " + (", ".join(noms) or "(liste vide)"))

    etape("image", "en_cours", "%.1f Ko" % (len(fichier) / 1024.0), 0)
    p.ecrire_fichier(pp.CHEMIN_MESSAGE, fichier,
                     lambda i, n: etape("image", "en_cours", "paquet %d/%d" % (i, n), i / float(n)))
    etape("image", "ok", "%.1f Ko" % (len(fichier) / 1024.0), 1)

    etape("affiche", "en_cours")
    p.ecrire_fichier_systeme("SEQUENT.SYS", pp.sequent_sys([("D", "T", pp.NOM_MESSAGE)]))
    etape("affiche", "ok")
    return {"nouvelle_sauvegarde": nouvelle, "noms_avant": noms}


def operation_restaurer(p, sauvegarde, etape=_rien):
    """Reecrit la liste de lecture sauvegardee (2/0x02). Refuse ce qui ne commence pas par SQ."""
    if not pp.is_sequent(sauvegarde):
        raise EchecPanneau("sauvegarde_invalide", "la sauvegarde n'est pas une liste de lecture : refus")
    verifier_dalle(p, etape)
    noms = pp.parse_sequent(sauvegarde)
    etape("affiche", "en_cours")
    p.ecrire_fichier_systeme("SEQUENT.SYS", sauvegarde)
    etape("affiche", "ok", "remis : " + (", ".join(noms) or "(liste vide)"))
    return {"noms": noms}


# ---------------------------------------------------------------- DNS

RESOLVEURS = ("1.1.1.1", "8.8.8.8")
DELAI_DNS_S = 2.0


class DnsIndisponible(Exception):
    pass


def nom_dns(plaque):
    """dw330hc / DW-330-HC -> infoDW330HC.ddns.net"""
    return "info%s.ddns.net" % normaliser_plaque(plaque).upper()


def normaliser_plaque(plaque):
    return "".join(c for c in str(plaque or "") if c.isalnum()).lower()


def construire_requete_dns(nom, ident):
    entete = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)   # RD, 1 question
    q = b"".join(bytes([len(p)]) + p.encode("ascii") for p in nom.strip(".").split(".")) + b"\x00"
    return entete + q + struct.pack(">HH", 1, 1)                  # type A, classe IN


def _sauter_nom(paquet, i):
    while True:
        if i >= len(paquet):
            raise ValueError("nom tronque")
        n = paquet[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:
            return i + 2
        i += 1 + n


def analyser_reponse_dns(paquet, ident):
    """-> adresse IPv4 (str), None si NXDOMAIN ou aucune adresse A, ValueError si illisible."""
    if len(paquet) < 12:
        raise ValueError("reponse DNS trop courte")
    rid, drapeaux, qd, an, _, _ = struct.unpack_from(">HHHHHH", paquet, 0)
    if rid != ident or not drapeaux & 0x8000:
        raise ValueError("reponse DNS inattendue")
    rcode = drapeaux & 0x000F
    if rcode == 3:
        return None
    if rcode != 0:
        raise ValueError("erreur DNS rcode=%d" % rcode)
    i = 12
    for _ in range(qd):
        i = _sauter_nom(paquet, i) + 4
    for _ in range(an):
        i = _sauter_nom(paquet, i)
        typ, cls, _ttl, rdlen = struct.unpack_from(">HHIH", paquet, i)
        i += 10
        if typ == 1 and cls == 1 and rdlen == 4:
            return socket.inet_ntoa(paquet[i:i + 4])
        i += rdlen
    return None


def _interroger(serveur, nom):
    ident = random.randint(0, 0xFFFF)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(DELAI_DNS_S)
        s.sendto(construire_requete_dns(nom, ident), (serveur, 53))
        fin = time.monotonic() + DELAI_DNS_S
        while True:
            paquet, _ = s.recvfrom(4096)
            try:
                return analyser_reponse_dns(paquet, ident)
            except ValueError:
                if time.monotonic() > fin:
                    raise
    finally:
        s.close()


def resoudre_plaque(plaque):
    """Resolution de info<PLAQUE>.ddns.net SANS cache (l'IP 4G change) :
    resolveurs publics interroges directement, puis repli sur le DNS du systeme.
    Rend (ip, source) ; (None, source) si le nom n'existe pas (remorque eteinte) ;
    leve DnsIndisponible si aucun resolveur n'a pu repondre."""
    nom = nom_dns(plaque)
    for serveur in RESOLVEURS:
        try:
            return _interroger(serveur, nom), serveur
        except (OSError, ValueError):
            continue
    try:
        infos = socket.getaddrinfo(nom, None, socket.AF_INET, socket.SOCK_STREAM)
        return infos[0][4][0], "systeme"
    except socket.gaierror as e:
        if e.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5), 11001):
            return None, "systeme"
        raise DnsIndisponible(str(e))
    except OSError as e:
        raise DnsIndisponible(str(e))


def est_ipv4(texte):
    try:
        socket.inet_aton(texte)
    except (OSError, TypeError):
        return False
    return str(texte).count(".") == 3


def est_boucle_locale(ip):
    """127.x : le jumeau de remorque (docs/pmv/banc-essai/faux_panneau.py)."""
    return str(ip).startswith("127.")
