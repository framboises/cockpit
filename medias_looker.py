"""
Photos des portails (couche 3P) versees dans Looker : dossier partage hors de static/ (Q24 de la refonte de Looker,
docs/refonte/Q24_PHOTOS_PORTAILS.md du depot looker).

- DOSSIER : LOOKER_MEDIAS_DOSSIER (meme variable que Looker v2), sinon ../looker_medias a cote de cockpit
  (E:\\TITAN\\production\\looker_medias en production). Rempli par scripts/migrer_medias.py de Looker.
- ANCIEN : ../looker/static/img/media (Looker v1), lu en repli tant que la copie n'est pas faite ; a retirer
  une fois la bascule de Looker terminee et le dossier de la v1 vide.
- Seules les PHOTOS DES PORTAILS sont servies (contre-audit V6 de Looker) : extension jpg, jpeg, png ou webp, ET
  nom present dans le champ « Photos » de la couche 3p (cache 10 min). Le dossier contient aussi les medias des
  categories restreintes de Looker et d'anciens envois (.svg, .html) : jamais servis par Cockpit.
- reponse_photo : nosniff, Content-Disposition inline avec le nom, Cache-Control private (jamais un cache partage).
Lecture seule : Cockpit n'ecrit jamais dans ces dossiers.
"""
import os
import time

from flask import abort, send_from_directory

_ICI = os.path.dirname(os.path.abspath(__file__))
DOSSIER = os.environ.get("LOOKER_MEDIAS_DOSSIER", "").strip() or os.path.join(_ICI, "..", "looker_medias")
ANCIEN = os.path.join(_ICI, "..", "looker", "static", "img", "media")
VARIANTES = ("original", "thumbnails")
EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
CACHE_S = 600
CACHE_NAVIGATEUR = "private, max-age=3600"

_noms = {"le": 0.0, "noms": frozenset()}


def nom_permis(nom):
    """Nom simple (sans dossier ni fichier cache) d'une image jpg, jpeg, png ou webp."""
    return (bool(nom) and nom == os.path.basename(nom) and not nom.startswith(".")
            and os.path.splitext(nom)[1].lower() in EXTENSIONS)


def photos_3p(db):
    """Noms cites dans le champ « Photos » de la couche 3p (une FeatureCollection), en cache 10 min."""
    if time.time() - _noms["le"] < CACHE_S:
        return _noms["noms"]
    doc = db["3p"].find_one({}, {"features.properties.Photos": 1}) or {}
    noms = set()
    for f in doc.get("features") or []:
        valeur = str(((f or {}).get("properties") or {}).get("Photos") or "").strip()
        if valeur:
            noms.add(valeur)
    _noms.update(le=time.time(), noms=frozenset(noms))
    return _noms["noms"]


def dossier_de(variante, nom, db=None):
    """
    Dossier (nouveau d'abord, ancien en repli) qui contient variante/nom, sinon None. Avec db : le nom doit aussi
    etre une photo de la couche 3p.
    """
    if variante not in VARIANTES or not nom_permis(nom):
        return None
    if db is not None and nom not in photos_3p(db):
        return None
    for racine in (DOSSIER, ANCIEN):
        dossier = os.path.join(racine, variante)
        if os.path.isfile(os.path.join(dossier, nom)):
            return dossier
    return None


def reponse_photo(db, nom, variantes=("original", "thumbnails")):
    """Photo d'un portail (premiere variante trouvee), en-tetes durcis ; 404 sinon."""
    dossier = None
    for variante in variantes:
        dossier = dossier_de(variante, nom, db)
        if dossier is not None:
            break
    if dossier is None:
        abort(404)
    resp = send_from_directory(dossier, nom)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = CACHE_NAVIGATEUR
    resp.headers["Content-Disposition"] = "inline"
    return resp
