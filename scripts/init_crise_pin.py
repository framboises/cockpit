#!/usr/bin/env python3
"""
init_crise_pin.py — Initialise ou met a jour le PIN d'un exercice de crise.

Usage interactif :
    python scripts/init_crise_pin.py

Le script :
  1. Demande l'identifiant d'exercice (ex: gpmotos2026)
  2. Verifie que le dossier cockpit/crise/<exercise_id>/ existe
  3. Demande un PIN a 8 chiffres (saisie masquee, double confirmation)
  4. Hashe le PIN avec werkzeug pbkdf2:sha256:600000
  5. Upsert dans la collection MongoDB `crise_config`

Aucun PIN n'est jamais loggue ni affiche en clair.
"""

import os
import re
import sys
import getpass
from datetime import datetime, timezone

# Permet d'executer le script depuis n'importe quel cwd
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COCKPIT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, COCKPIT_DIR)

from werkzeug.security import generate_password_hash
from pymongo import MongoClient


CRISE_ROOT = os.path.join(COCKPIT_DIR, "crise")
EXERCISE_ID_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")
PIN_RE = re.compile(r"^\d{8}$")
HASH_METHOD = "pbkdf2:sha256:600000"


def _bold(s):
    return "\033[1m" + s + "\033[0m"


def _err(s):
    return "\033[31m" + s + "\033[0m"


def _ok(s):
    return "\033[32m" + s + "\033[0m"


def _dim(s):
    return "\033[2m" + s + "\033[0m"


def _prompt_exercise_id():
    while True:
        # Liste les exercices existants pour suggestion
        if os.path.isdir(CRISE_ROOT):
            sub = sorted(
                d for d in os.listdir(CRISE_ROOT)
                if os.path.isdir(os.path.join(CRISE_ROOT, d)) and not d.startswith(".")
                and d not in ("assets",)
            )
            if sub:
                print(_dim("Exercices détectés : " + ", ".join(sub)))

        eid = input("Identifiant d'exercice (ex: gpmotos2026) : ").strip().lower()
        if not EXERCISE_ID_RE.match(eid):
            print(_err("  ✗ Identifiant invalide (lettres/chiffres/_- uniquement, max 64)"))
            continue
        folder = os.path.join(CRISE_ROOT, eid)
        if not os.path.isdir(folder):
            ans = input(
                _err("  ⚠ Dossier introuvable : " + folder + "\n")
                + "  Continuer quand même ? (y/N) : "
            ).strip().lower()
            if ans != "y":
                continue
        return eid


def _prompt_pin():
    while True:
        pin = getpass.getpass("PIN (8 chiffres) : ")
        if not PIN_RE.match(pin):
            print(_err("  ✗ Le PIN doit faire exactement 8 chiffres (0-9)"))
            continue
        confirm = getpass.getpass("Confirmer le PIN : ")
        if pin != confirm:
            print(_err("  ✗ Les deux saisies ne correspondent pas"))
            continue
        # Mise en garde sur les PIN faibles
        if pin in {"00000000", "12345678", "11111111", "87654321", "00000001"}:
            ans = input(_err("  ⚠ PIN trivialement devinable. Continuer quand même ? (y/N) : ")).strip().lower()
            if ans != "y":
                continue
        if len(set(pin)) == 1:
            ans = input(_err("  ⚠ PIN à un seul chiffre répété. Continuer quand même ? (y/N) : ")).strip().lower()
            if ans != "y":
                continue
        return pin


def _connect_mongo():
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    dev_mode = os.getenv("TITAN_ENV", "dev") != "prod"
    db_name = "titan_dev" if dev_mode else "titan"
    print(_dim("MongoDB : " + mongo_uri + " · base : " + db_name))
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=4000)
    # Test connexion
    client.admin.command("ping")
    return client[db_name]


def main():
    print(_bold("═══════════════════════════════════════════════════════════"))
    print(_bold("  Initialisation du PIN — Exercice de crise"))
    print(_bold("═══════════════════════════════════════════════════════════"))
    print()

    try:
        db = _connect_mongo()
    except Exception as exc:
        print(_err("✗ Impossible de se connecter à MongoDB : " + str(exc)))
        sys.exit(2)

    exercise_id = _prompt_exercise_id()

    existing = db["crise_config"].find_one({"exercise_id": exercise_id})
    if existing:
        print()
        print(_err("⚠ Un PIN existe déjà pour cet exercice"))
        print("  Créé le      : " + str(existing.get("created_at", "?")))
        print("  Mis à jour   : " + str(existing.get("updated_at", "?")))
        print("  Version      : " + str(existing.get("pin_version", 1)))
        ans = input("Remplacer ? (y/N) : ").strip().lower()
        if ans != "y":
            print("Abandon.")
            sys.exit(0)

    print()
    pin = _prompt_pin()

    pin_hash = generate_password_hash(pin, method=HASH_METHOD)
    # Effacement defensif (le GC peut traîner)
    pin = "0" * 8
    del pin

    now = datetime.now(timezone.utc)
    new_version = (existing or {}).get("pin_version", 0) + 1
    db["crise_config"].update_one(
        {"exercise_id": exercise_id},
        {
            "$set": {
                "exercise_id": exercise_id,
                "pin_hash": pin_hash,
                "updated_at": now,
                "pin_version": new_version,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )

    # Purge defensive des tentatives precedentes pour cet exercice
    purged = db["crise_auth_attempts"].delete_many({"exercise_id": exercise_id})
    print()
    print(_ok("✓ PIN enregistré pour l'exercice « " + exercise_id + " »"))
    print("  Hash         : " + pin_hash[:24] + "…")
    print("  Version      : " + str(new_version))
    if purged.deleted_count:
        print(_dim("  Historique des tentatives purgé : " + str(purged.deleted_count) + " entrée(s)"))

    print()
    print(_dim("Rappel : ne JAMAIS commit le PIN, ne JAMAIS le mettre en .env."))
    print(_dim("Le PIN n'est partagé qu'avec les animateurs autorisés."))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("Abandon (Ctrl-C).")
        sys.exit(130)
