"""Campagne d'e-mails du test PC Organisation : gabarit, planification, fil d'envoi SMTP.

Un message par destinataire (jamais de liste visible), via le relais SMTP de
Cockpit (`pcorg_summary_mail.smtp_envoyer`). L'etat vit dans `envois` : un
redemarrage reprend les `en_file`. Un seul fil daemon par processus.
"""
import html as _html
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from formation import store, tentative

logger = logging.getLogger(__name__)

_PARIS = ZoneInfo("Europe/Paris")
_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
         "septembre", "octobre", "novembre", "décembre"]
CIBLES = ("non_invites", "relance_non_commences")


def _lien():
    base = (os.getenv("COCKPIT_PUBLIC_URL") or os.getenv("PUBLIC_BASE_URL")
            or "https://cockpit.lemans.org").strip().rstrip("/")
    return base + "/test-pc-organisation"


def _date_limite(fermeture):
    d = fermeture.astimezone(_PARIS)
    return "%s %d %s à %d h %02d" % (_JOURS[d.weekday()], d.day, _MOIS[d.month - 1], d.hour, d.minute)


_URL = re.compile(r"https?://[^\s<]+")


def _html_texte(texte):
    paragraphes = []
    for bloc in re.split(r"\n\s*\n", texte.strip()):
        esc = _html.escape(bloc)
        esc = _URL.sub(lambda m: '<a href="%s">%s</a>' % (m.group(0), m.group(0)), esc)
        paragraphes.append('<p style="margin:0 0 14px 0">%s</p>' % esc.replace("\n", "<br>\n"))
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;'
            'color:#1a1a1a">%s</div>' % "\n".join(paragraphes))


def rendre(gabarit, candidat, reglages):
    """(objet, texte, html) pour un candidat."""
    g = gabarit or store.GABARIT_DEFAUT
    valeurs = {
        "prenom": (candidat.get("prenom") or "").strip(),
        "lien": _lien(),
        "date_limite": _date_limite(reglages["fermeture"]),
        "contact": reglages.get("reply_to") or "",
    }

    def sub(modele):
        return re.sub(r"\{(prenom|lien|date_limite|contact)\}", lambda m: valeurs[m.group(1)], modele)

    objet = sub(g.get("objet", "")).replace("\n", " ").strip()
    texte = sub(g.get("corps", ""))
    return objet, texte, _html_texte(texte)


def _message(objet, texte, html, adresse, reglages):
    sender = (os.getenv("SMTP_FROM", "safe@lemans.org") or "").strip()
    domaine = sender.split("@")[-1] if "@" in sender else "lemans.org"
    m = MIMEMultipart("alternative")
    m["From"] = formataddr((reglages.get("expediteur_nom") or "", sender))
    m["To"] = adresse
    if reglages.get("reply_to"):
        m["Reply-To"] = reglages["reply_to"]
    m["Subject"] = Header(objet, "utf-8")
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=domaine)
    m.attach(MIMEText(texte, "plain", "utf-8"))
    m.attach(MIMEText(html, "html", "utf-8"))
    return m


def _smtp(message, destinataires):
    from pcorg_summary_mail import smtp_envoyer
    smtp_envoyer(message, destinataires)


def envoyer_essai(db, adresse, auteur):
    adresse = (adresse or "").strip()
    if not _ADRESSE.fullmatch(adresse):
        raise ValueError("adresse_invalide")
    reg = store.reglages(db)
    objet, texte, html = rendre(reg["gabarit"], {"prenom": "Camille"}, reg)
    _smtp(_message("[ESSAI] " + objet, texte, html, adresse, reg), [adresse])
    store.journal(db, "campagne_essai", auteur, adresse=adresse)


_ADRESSE = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


def _candidats_cibles(db, cible):
    """Candidats non fictifs concernes par la cible."""
    if cible not in CIBLES:
        raise ValueError("cible_inconnue")
    envois = db["envois"]
    out = []
    for c in db["candidats"].find({"campagne": store.CAMPAGNE, "fictif": {"$ne": True}}):
        cid = str(c["_id"])
        base = {"campagne": store.CAMPAGNE, "candidat_id": cid}
        if cible == "non_invites":
            if envois.find_one(dict(base, type="invitation"), {"_id": 1}):
                continue
        else:
            if not envois.find_one(dict(base, type="invitation", statut="envoye"), {"_id": 1}):
                continue
            if envois.find_one(dict(base, type="relance"), {"_id": 1}):
                continue
            if tentative.active(db, cid):
                continue
        out.append(c)
    return out


def compter(db, cible):
    return len(_candidats_cibles(db, cible))


def planifier(db, cible, auteur, now, confirmation_n=None):
    """Retourne {planifies, invalides}. Leve ValueError reply_to_vide, ou
    confirmation_differente (args : n recalcule) si confirmation_n ne correspond pas."""
    if not store.reglages(db).get("reply_to"):
        raise ValueError("reply_to_vide")
    typ = "invitation" if cible == "non_invites" else "relance"
    liste = _candidats_cibles(db, cible)
    if confirmation_n is not None and (isinstance(confirmation_n, bool) or confirmation_n != len(liste)):
        raise ValueError("confirmation_differente", len(liste))
    n = invalides = 0
    for c in liste:
        if not _ADRESSE.fullmatch((c.get("email") or "").strip()):
            invalides += 1
            continue
        try:
            db["envois"].insert_one({
                "campagne": store.CAMPAGNE, "candidat_id": str(c["_id"]), "type": typ,
                "email": c["email"].strip(), "statut": "en_file", "cree_le": now, "auteur": auteur})
            n += 1
        except DuplicateKeyError:
            pass
    store.journal(db, "campagne_planifier", auteur, cible=cible, n=n, invalides=invalides)
    return {"planifies": n, "invalides": invalides}


def _fixer_arret(db, valeur, auteur, action):
    db["reglages"].update_one({"_id": store.CAMPAGNE}, {"$set": {"campagne_arretee": valeur}}, upsert=True)
    store.journal(db, action, auteur)


def arreter(db, auteur):
    """Arret persistant : survit a un redemarrage ; les en_file restent en file."""
    _fixer_arret(db, True, auteur, "campagne_arreter")
    fil.arreter()


def reprendre(db, auteur):
    _fixer_arret(db, False, auteur, "campagne_reprendre")
    if db["envois"].count_documents({"campagne": store.CAMPAGNE, "statut": "en_file"}):
        fil.demarrer(db)


def lancer(db, cible, confirmation_n, auteur, now):
    """Planifie (confirmation explicite obligatoire), leve l'arret et demarre le fil."""
    res = planifier(db, cible, auteur, now, confirmation_n=confirmation_n)
    if store.reglages(db).get("campagne_arretee"):
        _fixer_arret(db, False, auteur, "campagne_reprendre")
    fil.demarrer(db)
    return res


def renvoyer_echecs(db, auteur):
    r = db["envois"].update_many({"campagne": store.CAMPAGNE, "statut": "echec"},
                                 {"$set": {"statut": "en_file"}, "$unset": {"erreur": ""}})
    store.journal(db, "campagne_renvoyer_echecs", auteur, n=r.modified_count)
    return r.modified_count


class FilEnvoi:
    def __init__(self, envoyer=None, sleep=None):
        self._envoyer = envoyer or _smtp
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._thread = None
        self._db = None
        self._verrou = threading.Lock()

    def traiter_un(self, db):
        """Traite un envoi en file. Retourne False s'il n'en reste aucun."""
        e = db["envois"].find_one_and_update(
            {"campagne": store.CAMPAGNE, "statut": "en_file"},
            {"$set": {"statut": "en_cours", "debut": datetime.now(timezone.utc)}},
            sort=[("cree_le", 1), ("_id", 1)])
        if e is None:
            return False
        try:
            try:
                oid = ObjectId(e["candidat_id"])
            except Exception:
                oid = None
            cand = db["candidats"].find_one({"_id": oid}) if oid else None
            if cand is None:
                raise ValueError("candidat_introuvable")
            if cand.get("fictif"):
                db["envois"].update_one({"_id": e["_id"]}, {"$set": {"statut": "annule"}})
                return True
            reg = store.reglages(db)
            if not reg.get("reply_to"):
                logger.error("formation campagne arretee : reply_to vide")
                db["envois"].update_one({"_id": e["_id"]}, {"$set": {"statut": "en_file"}})
                _fixer_arret(db, True, "systeme", "campagne_arret_reply_to")
                self._stop.set()
                return True
            objet, texte, html = rendre(reg["gabarit"], cand, reg)
            adresse = e["email"]
            self._envoyer(_message(objet, texte, html, adresse, reg), [adresse])
        except Exception as exc:
            logger.warning("formation envoi echec: %s", exc)
            db["envois"].update_one({"_id": e["_id"]}, {"$set": {
                "statut": "echec", "erreur": str(exc)[:300]}})
        else:
            db["envois"].update_one({"_id": e["_id"]}, {"$set": {
                "statut": "envoye", "envoye_le": datetime.now(timezone.utc)}})
        return True

    def traiter_tout(self, db):
        """Boucle jusqu'a epuisement ou arret ; pause debit_envoi_s entre deux messages."""
        while not self._stop.is_set():
            try:
                reg = store.reglages(db)
                if reg.get("campagne_arretee"):
                    break
                if not self.traiter_un(db):
                    break
                if self._stop.is_set():
                    break
                self._sleep(float(reg.get("debit_envoi_s") or 0))
            except Exception:
                logger.exception("formation envoi : erreur inattendue, fil arrete")
                break

    def demarrer(self, db):
        with self._verrou:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._db = db
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self.traiter_tout, args=(db,), name="formation-envoi", daemon=True)
            self._thread.start()
            return True

    def arreter(self):
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=15)

    def etat(self):
        db = self._db if self._db is not None else store.get_db()
        n = {s: db["envois"].count_documents({"campagne": store.CAMPAGNE, "statut": s})
             for s in ("en_file", "en_cours", "envoye", "echec")}
        return {"en_file": n["en_file"] + n["en_cours"], "envoyes": n["envoye"],
                "echecs": n["echec"],
                "arretee": bool(store.reglages(db).get("campagne_arretee")),
                "actif": self._thread is not None and self._thread.is_alive()}


fil = FilEnvoi()


def reprendre_au_demarrage(db, now=None):
    """en_cours orphelins (debut de plus de 5 min) -> en_file ; le fil repart s'il reste
    des en_file, sauf campagne arretee."""
    now = now or datetime.now(timezone.utc)
    db["envois"].update_many(
        {"campagne": store.CAMPAGNE, "statut": "en_cours",
         "debut": {"$lt": now - timedelta(minutes=5)}},
        {"$set": {"statut": "en_file"}})
    if store.reglages(db).get("campagne_arretee"):
        return
    if db["envois"].count_documents({"campagne": store.CAMPAGNE, "statut": "en_file"}):
        fil.demarrer(db)
