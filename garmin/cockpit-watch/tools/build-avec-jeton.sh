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
import sys, pathlib
tmp, jeton = sys.argv[1], sys.argv[2]
p = pathlib.Path(tmp) / "resources" / "properties" / "properties.xml"
s = p.read_text(encoding="utf-8")
avant = '<property id="token" type="string"></property>'
if avant not in s:
    raise SystemExit("propriete token introuvable ou deja renseignee dans properties.xml")
p.write_text(s.replace(avant,
    '<property id="token" type="string">%s</property>' % jeton, 1), encoding="utf-8")
PYEOF

mkdir -p "$PROJET/bin"
"$SDK/bin/monkeyc" -o "$PROJET/bin/$NOM.prg" -f "$TMP/monkey.jungle" \
    -y "$CLE" -d "$DEVICE" $DRAPEAU

echo
echo "  bin/$NOM.prg construit ($MODE), jeton compile dedans"
echo
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
