"""Normalisation des e-mails et mobiles, identique a l'import et a la connexion."""
import re


def normaliser_email(s):
    if s is None:
        return None
    e = re.sub(r"\s+", "", str(s)).lower()
    if "@" not in e or e.startswith("@") or e.endswith("@"):
        return None
    return e


def normaliser_tel(s):
    if s is None:
        return None
    if isinstance(s, float) and s == int(s):
        s = int(s)
    d = re.sub(r"\D", "", str(s))
    if d.startswith("0033"):
        d = d[4:]
        d = "0" + d
    elif d.startswith("33") and len(d) == 11:
        d = "0" + d[2:]
    if len(d) == 9 and d[0] in "67":
        d = "0" + d  # zero de tete perdu par une cellule numerique Excel
    if len(d) == 10 and d.startswith("0"):
        return d
    return None
