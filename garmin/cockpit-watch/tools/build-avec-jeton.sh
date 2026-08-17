#!/bin/bash
#
# Construit un .prg avec le jeton compile dedans.
#
# POURQUOI CE SCRIPT EXISTE
#
# Une app Connect IQ chargee par sideload ne peut PAS voir ses reglages
# modifies depuis Garmin Connect Mobile ni Garmin Express : apres sideload,
# l'app n'a tout simplement pas de bouton de reglages. C'est une limite de la
# plateforme, pas de cette app.
#
#   "It's not possible to set app settings of a side loaded app from GCM or GE."
#   https://forums.garmin.com/developer/connect-iq/f/discussion/429848
#
# Le brief du projet demandait le jeton dans les Properties, saisi depuis les
# reglages Connect IQ, ET une distribution par sideload. Les deux sont
# incompatibles. La parade retenue est celle que recommande le meme fil pour
# des valeurs statiques : compiler la valeur comme defaut de la propriete.
#
# Consequence : changer de jeton impose un rebuild et un re-sideload. C'est
# deux minutes, et le jeton reste revocable en un clic depuis /watch-admin.
#
# LE JETON N'ENTRE JAMAIS DANS LE DEPOT : la copie de travail vit dans un
# dossier temporaire supprime en sortie, et le .prg produit va dans bin/, que
# .gitignore exclut.
#
# Usage :
#   tools/build-avec-jeton.sh <JETON> [debug|release] [nom-de-sortie]
#
#   defaut : release, sortie bin/cockpit.prg
#
# Exemples :
#   tools/build-avec-jeton.sh AbC123...            # prod, pour la montre
#   tools/build-avec-jeton.sh AbC123... debug test # pour le simulateur

set -e

JETON="$1"
MODE="${2:-release}"
NOM="${3:-cockpit}"

if [ -z "$JETON" ]; then
    echo "usage: $0 <JETON> [debug|release] [nom-de-sortie]"
    echo
    echo "Emets un jeton depuis https://cockpit.lemans.org/watch-admin (admin),"
    echo "puis passe-le en argument. Il ne sera pas ecrit dans le depot."
    exit 1
fi

SDK="${CIQ_SDK:-$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2}"
CLE="${CIQ_KEY:-$HOME/.garmin_keys/developer_key.der}"
PROJET="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE=fenix8solar51mm

[ -d "$SDK" ] || { echo "SDK introuvable : $SDK  (surcharger avec CIQ_SDK=...)"; exit 1; }
[ -f "$CLE" ] || { echo "cle developpeur introuvable : $CLE  (surcharger avec CIQ_KEY=...)"; exit 1; }

DRAPEAU=""
[ "$MODE" = "release" ] && DRAPEAU="-r"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cp -R "$PROJET/manifest.xml" "$PROJET/monkey.jungle" "$PROJET/resources" \
      "$PROJET/resources-fre" "$PROJET/source" "$TMP/"

python3 - "$TMP" "$JETON" <<'PYEOF'
import sys, pathlib, re
from xml.sax.saxutils import escape
tmp, jeton = sys.argv[1], sys.argv[2]

# Un jeton mal forme produisait auparavant un .prg qui COMPILE et une montre
# qui recoit 401 sans que rien ne le dise. Trois gardes, toutes silencieuses
# jusqu'ici :
if not jeton.strip():
    raise SystemExit("ERREUR : jeton vide")
if jeton != jeton.strip():
    raise SystemExit(
        "ERREUR : le jeton porte des espaces ou un retour a la ligne en "
        "bordure.\n  Le serveur les retire (watch_api.py:192 fait .strip()), "
        "mais pas la montre :\n  elle enverrait un jeton different de celui "
        "qui a ete emis.")
# Les jetons sont en base64 url-safe (secrets.token_urlsafe) : ni espace, ni
# caractere XML special. Tout le reste est une faute de copie.
if not re.fullmatch(r"[A-Za-z0-9_-]+", jeton):
    raise SystemExit(
        "ERREUR : caractere inattendu dans le jeton.\n"
        "  Attendu : base64 url-safe (lettres, chiffres, - et _).\n"
        "  Un jeton copie depuis un terminal peut porter une coupure de "
        "ligne invisible.")

p = pathlib.Path(tmp) / "resources" / "properties" / "properties.xml"
s = p.read_text(encoding="utf-8")
avant = '<property id="token" type="string"></property>'
if avant not in s:
    raise SystemExit("propriete token introuvable ou deja renseignee dans properties.xml")
# escape() par principe : la garde ci-dessus l'exclut deja, mais un XML
# construit par concatenation sans echappement est une faute qui finit
# toujours par se payer.
p.write_text(s.replace(avant,
    '<property id="token" type="string">%s</property>' % escape(jeton), 1),
    encoding="utf-8")
print("  jeton valide (%d caracteres)" % len(jeton))
PYEOF

mkdir -p "$PROJET/bin"
"$SDK/bin/monkeyc" -o "$PROJET/bin/$NOM.prg" -f "$TMP/monkey.jungle" \
    -y "$CLE" -d "$DEVICE" $DRAPEAU

echo
echo "  bin/$NOM.prg construit ($MODE), jeton compile dedans"
echo

# Controle bout en bout AVANT le sideload : le jeton qui vient d'etre
# compile est-il accepte par le serveur ? Sans ce controle, un jeton
# revoque ou mal copie ne se voyait qu'une fois la montre chargee, sous la
# forme d'un ecran fige sur un cache -- c'est exactement ce qui s'est
# produit, et il a fallu une demi-journee pour le nommer.
HOTE="${WATCH_HOST:-cockpit.lemans.org}"
if command -v curl >/dev/null 2>&1; then
    CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
        -H "Authorization: Bearer $JETON" \
        "https://$HOTE/api/v1/watch/state" 2>/dev/null || echo "000")
    case "$CODE" in
        200) echo "  Jeton verifie sur $HOTE : ACCEPTE" ;;
        401) echo "  /!\\  Jeton REFUSE par $HOTE (401)."
             echo "        Il a ete revoque, ou ce n'est pas celui qui a ete emis."
             echo "        Emets-en un nouveau depuis /watch-admin et relance." ;;
        429) echo "  Jeton accepte, mais quota atteint (429) -- reessaie dans 5 min." ;;
        000) echo "  (serveur $HOTE injoignable d'ici : verification sautee)" ;;
        *)   echo "  /!\\  Reponse inattendue de $HOTE : HTTP $CODE" ;;
    esac
    echo
fi
if [ "$MODE" = "release" ]; then
    echo "  Sideload :"
    echo "    cp $PROJET/bin/$NOM.prg /Volumes/GARMIN/GARMIN/APPS/"
    echo "    diskutil eject /Volumes/GARMIN"
else
    echo "  Simulateur :"
    echo "    \"\$SDK/bin/connectiq\" &"
    echo "    \"\$SDK/bin/monkeydo\" $PROJET/bin/$NOM.prg $DEVICE"
fi
echo
echo "  Ce binaire contient un secret : ne le partage pas, et supprime-le"
echo "  quand tu n'en as plus besoin (rm $PROJET/bin/$NOM.prg*)."
