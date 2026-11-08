#!/usr/bin/env python3
"""Installe l'application COCKPIT Field sur les tablettes branchees en USB.

Usage :
    python3 install_tablettes.py            # installe sur les tablettes branchees, puis s'arrete
    python3 install_tablettes.py --watch    # reste ouvert : chaque tablette branchee est installee
    python3 install_tablettes.py --apk chemin/vers/app.apk
    python3 install_tablettes.py --forcer           # reinstalle meme si deja a jour
    python3 install_tablettes.py --remplacer-debug   # desinstalle une build de debug (perd l'appairage)

Prealable sur chaque tablette : mode developpeur + debogage USB actives, puis
accepter "Autoriser le debogage USB" au branchement.

APK utilise par defaut : la derniere build de production
(android/app/build/outputs/apk/release/app-release.apk), sinon le plus recent
~/Desktop/COCKPIT-Field-*.apk.
"""
import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

PACKAGE = "org.lemans.cockpit.field"
ICI = os.path.dirname(os.path.abspath(__file__))
APK_BUILD = os.path.join(ICI, "android", "app", "build", "outputs", "apk", "release", "app-release.apk")


def trouver_adb():
    adb = shutil.which("adb")
    if adb:
        return adb
    for p in (os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"), "/opt/homebrew/bin/adb"):
        if os.path.exists(p):
            return p
    sys.exit("adb introuvable : installe-le avec 'brew install android-platform-tools'.")


def trouver_apk(chemin):
    if chemin:
        if not os.path.exists(chemin):
            sys.exit("APK introuvable : %s" % chemin)
        return chemin
    candidats = [APK_BUILD] + glob.glob(os.path.expanduser("~/Desktop/COCKPIT-Field-*.apk"))
    candidats = [c for c in candidats if os.path.exists(c)]
    if not candidats:
        sys.exit("Aucun APK : lance 'npm run build:release' ou passe --apk.")
    return max(candidats, key=os.path.getmtime)


def version_apk(apk):
    """versionCode / versionName de l'APK, via aapt2 du SDK s'il est la."""
    outils = sorted(glob.glob(os.path.expanduser("~/Library/Android/sdk/build-tools/*/aapt2")))
    if not outils:
        return None, None
    try:
        out = subprocess.run([outils[-1], "dump", "badging", apk], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None, None
    code = re.search(r"versionCode='(\d+)'", out)
    nom = re.search(r"versionName='([^']*)'", out)
    return (int(code.group(1)) if code else None), (nom.group(1) if nom else None)


def adb_cmd(adb, serie, *args, timeout=60):
    cmd = [adb] + (["-s", serie] if serie else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def lister(adb):
    """{serie: etat} des appareils vus par adb (device, unauthorized, offline...)."""
    _, out = adb_cmd(adb, None, "devices", "-l")
    vus = {}
    for ligne in out.splitlines()[1:]:
        parts = ligne.split()
        if len(parts) >= 2:
            modele = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "")
            vus[parts[0]] = (parts[1], modele)
    return vus


def version_installee(adb, serie):
    _, out = adb_cmd(adb, serie, "shell", "dumpsys", "package", PACKAGE)
    code = re.search(r"versionCode=(\d+)", out)
    nom = re.search(r"versionName=(\S+)", out)
    if not code:
        return None, None
    return int(code.group(1)), (nom.group(1) if nom else "?")


def installer(adb, serie, modele, apk, apk_code, remplacer_debug, forcer=False):
    nom = "%s (%s)" % (modele or "tablette", serie)
    _, android = adb_cmd(adb, serie, "shell", "getprop", "ro.build.version.release")
    code_inst, nom_inst = version_installee(adb, serie)
    if code_inst is not None:
        print("  %s : Android %s, Field %s deja installee (versionCode %d)" % (nom, android, nom_inst, code_inst))
        if apk_code is not None and code_inst >= apk_code and not forcer:
            print("  -> deja a jour, rien a faire (--forcer pour reinstaller).")
            return False
    else:
        print("  %s : Android %s, Field absente" % (nom, android))

    print("  -> installation...")
    rc, out = adb_cmd(adb, serie, "install", "-r", apk, timeout=300)
    if rc != 0 and "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
        if not remplacer_debug:
            print("  !! Une build signee autrement (debug) est installee. Relance avec --remplacer-debug")
            print("     pour la desinstaller (la tablette devra etre reappairee).")
            return False
        print("  -> desinstallation de la build de debug (appairage perdu)...")
        adb_cmd(adb, serie, "uninstall", PACKAGE)
        rc, out = adb_cmd(adb, serie, "install", apk, timeout=300)
    if rc != 0:
        print("  !! Echec : %s" % out.splitlines()[-1] if out else "  !! Echec")
        return False

    code_inst, nom_inst = version_installee(adb, serie)
    print("  OK : Field %s (versionCode %s) installee." % (nom_inst, code_inst))
    adb_cmd(adb, serie, "shell", "monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
    print("  -> application lancee : appairer la tablette avec un code Cockpit.")
    return True


def main():
    ap = argparse.ArgumentParser(description="Installe COCKPIT Field sur les tablettes USB.")
    ap.add_argument("--apk", help="APK a installer (defaut : derniere build de production)")
    ap.add_argument("--watch", action="store_true", help="rester ouvert et installer chaque tablette branchee")
    ap.add_argument("--remplacer-debug", action="store_true",
                    help="desinstaller une build de debug avant d'installer (perd l'appairage)")
    ap.add_argument("--forcer", action="store_true", help="reinstaller meme si la tablette est a jour")
    args = ap.parse_args()

    adb = trouver_adb()
    apk = trouver_apk(args.apk)
    apk_code, apk_nom = version_apk(apk)
    with open(apk, "rb") as f:
        empreinte = hashlib.sha256(f.read()).hexdigest()
    print("APK : %s" % apk)
    print("      version %s (versionCode %s), SHA-256 %s...%s" % (apk_nom or "?", apk_code or "?", empreinte[:8], empreinte[-6:]))

    traites, signales = set(), set()
    if args.watch:
        print("En attente de tablettes (Ctrl+C pour arreter)...")
    while True:
        vus = lister(adb)
        for serie, (etat, modele) in vus.items():
            if serie in traites:
                continue
            if etat == "device":
                print("\nTablette detectee : %s %s" % (modele, serie))
                try:
                    installer(adb, serie, modele, apk, apk_code, args.remplacer_debug, args.forcer)
                except subprocess.TimeoutExpired:
                    print("  !! adb ne repond plus (cable ?)")
                traites.add(serie)
            elif etat == "unauthorized" and serie not in signales:
                print("\n%s : accepte 'Autoriser le debogage USB' sur la tablette." % serie)
                signales.add(serie)
        # Une tablette debranchee pourra etre retraitee si on la rebranche
        traites &= set(vus)
        signales &= set(vus)
        if not args.watch:
            if not vus:
                print("Aucune tablette detectee : verifie le cable et le debogage USB.")
            elif any(e == "unauthorized" for e, _ in vus.values()):
                print("Relance le script apres avoir accepte l'autorisation.")
            break
        try:
            time.sleep(2)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrete.")
