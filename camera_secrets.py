# camera_secrets.py -- chiffrement des mots de passe des cameras
#
# Les mots de passe des profils d'identifiants (cam_credential_profiles) sont
# stockes chiffres en AES-256-GCM. La cle maitre (32 octets, base64) est lue
# dans la variable d'environnement COCKPIT_CAMERA_KEY : sans elle, la base ne
# contient rien d'exploitable. Format stocke : "v1:<nonce b64>:<chiffre b64>".
#
# Perte de la cle = mots de passe a ressaisir (ils ne sont recuperables que
# depuis la cle). Ne jamais journaliser ni renvoyer au navigateur un mot de
# passe dechiffre.
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_KEY = "COCKPIT_CAMERA_KEY"
_PREFIX = "v1"
# Donnees associees : un chiffre ne peut pas etre deplace vers un autre usage
_AAD = b"cockpit-camera-credential"


class CameraKeyMissing(RuntimeError):
    """Cle maitre absente ou invalide."""


def _key():
    raw = os.getenv(ENV_KEY, "").strip()
    if not raw:
        raise CameraKeyMissing("Variable %s absente : mots de passe cameras indisponibles" % ENV_KEY)
    try:
        key = base64.b64decode(raw)
    except Exception:
        raise CameraKeyMissing("Variable %s invalide (base64 attendu)" % ENV_KEY)
    if len(key) != 32:
        raise CameraKeyMissing("Variable %s invalide (32 octets attendus)" % ENV_KEY)
    return key


def available():
    try:
        _key()
        return True
    except CameraKeyMissing:
        return False


def encrypt(plain):
    """Mot de passe en clair -> chaine stockable."""
    nonce = os.urandom(12)
    data = AESGCM(_key()).encrypt(nonce, (plain or "").encode("utf-8"), _AAD)
    return "%s:%s:%s" % (_PREFIX, base64.b64encode(nonce).decode(), base64.b64encode(data).decode())


def decrypt(stored):
    """Chaine stockee -> mot de passe en clair ("" si rien de stocke)."""
    if not stored:
        return ""
    prefix, nonce_b64, data_b64 = stored.split(":", 2)
    if prefix != _PREFIX:
        raise ValueError("Format de secret inconnu")
    return AESGCM(_key()).decrypt(base64.b64decode(nonce_b64), base64.b64decode(data_b64), _AAD).decode("utf-8")
