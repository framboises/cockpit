# Recepteur Teltonika (TCP 5027) - deploiement

Regles et architecture : `docs/claude/balises.md`. Bibliotheque standard
uniquement : tourne avec `C:\Python313\python.exe`, sans le venv de Cockpit.

Le recepteur est deploye HORS du dossier de Cockpit, sous un compte local
sans droits (`svc-balises`) qui ne peut lire ni Cockpit ni son venv. Il ne
parle a Cockpit que par HTTP signe sur 127.0.0.1:4008.

## 1. Secret partage

Generer un secret (48 caracteres) et le poser a DEUX endroits :
- environnement de Cockpit : `BALISES_INGEST_SECRET` (variable du compte qui
  lance Cockpit), puis redemarrer Cockpit ;
- environnement du service (etape 4).

```powershell
-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
```

## 2. Compte et dossier

```powershell
net user svc-balises <mot_de_passe_long> /add /passwordchg:no /comment:"Recepteur balises GPS"
wmic useraccount where "name='svc-balises'" set PasswordExpires=false
New-Item -ItemType Directory E:\TITAN\balises, E:\TITAN\balises\data
Copy-Item E:\TITAN\production\cockpit\recepteur_balises\codec8.py, E:\TITAN\production\cockpit\recepteur_balises\recepteur.py E:\TITAN\balises\
icacls E:\TITAN\balises /grant "svc-balises:(OI)(CI)RX"
icacls E:\TITAN\balises\data /grant "svc-balises:(OI)(CI)M"
```

Politique locale : mot de passe 183 jours max -> `PasswordExpires=false`
obligatoire, sinon le service tombe au bout de 6 mois. `BUILTIN\Utilisateurs`
a deja RX sur `C:\Python313`.

## 3. Reseau

- Redirection du port TCP 5027 public -> 192.168.254.35:5027 (pare-feu de
  bordure, equipe reseau). Filtrage par IP impossible (CGNAT des operateurs) :
  la liste blanche des IMEI est le seul controle.
- Trend Micro Vulnerability Protection : autoriser l'entrant TCP 5027.

## 4. Service NSSM

```powershell
nssm install BalisesRecepteur C:\Python313\python.exe "-I -X utf8 E:\TITAN\balises\recepteur.py"
nssm set BalisesRecepteur AppDirectory E:\TITAN\balises
nssm set BalisesRecepteur ObjectName .\svc-balises <mot_de_passe>
nssm set BalisesRecepteur AppEnvironmentExtra BALISES_INGEST_SECRET=<secret>
nssm set BalisesRecepteur AppStdout E:\TITAN\balises\data\service.log
nssm set BalisesRecepteur AppStderr E:\TITAN\balises\data\service.log
nssm set BalisesRecepteur Start SERVICE_AUTO_START
nssm start BalisesRecepteur
```

`nssm set ... ObjectName` accorde le droit "ouvrir une session en tant que
service" au compte.

## 5. Verification

- `E:\TITAN\balises\data\logs\recepteur.log` : "liste blanche : N IMEI" puis
  "en ecoute sur 0.0.0.0:5027".
- Configuration > Balises GPS > Source Teltonika : "Dernier envoi du recepteur".
- Basculer UNE balise (Teltonika Configurator ou SMS : serveur = IP publique,
  port 5027, TCP), puis les autres. flespi peut rester actif pendant la bascule
  (aucun doublon), a desactiver ensuite.

## Mise a jour

Recopier `codec8.py` et `recepteur.py` dans `E:\TITAN\balises\` puis
`nssm restart BalisesRecepteur` (le spool garde ce qui n'a pas ete transmis).
