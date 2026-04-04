# WAHA - Deploiement production (Windows Server 2022 Datacenter)

## Pre-requis

- **Docker Desktop pour Windows** installe et fonctionnel
  - Telecharger depuis https://www.docker.com/products/docker-desktop/
  - Activer WSL2 ou Hyper-V lors de l'installation
  - Redemarrer le serveur apres installation

## 1. Fichier docker-compose.yml (production)

Creer ou modifier `docker-compose.yml` dans le dossier cockpit :

```yaml
services:
  waha:
    image: devlikeapro/waha:latest
    container_name: cockpit-waha
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - WHATSAPP_DEFAULT_ENGINE=WEBJS
      - WHATSAPP_RESTART_ALL_SESSIONS=True
      - WAHA_SESSION=default
      - WAHA_DASHBOARD_ENABLED=true
      - WAHA_DASHBOARD_USERNAME=admin
      - WAHA_DASHBOARD_PASSWORD=CHANGER_CE_MOT_DE_PASSE
      - WAHA_API_KEY=GENERER_UNE_CLE_SECRETE
    volumes:
      - waha_sessions:/app/.sessions
      - waha_data:/app/.media

volumes:
  waha_sessions:
  waha_data:
```

### Differences avec le dev

| Parametre | Dev (macOS) | Prod (Windows Server) |
|-----------|-------------|----------------------|
| `image` | `devlikeapro/waha:arm` | `devlikeapro/waha:latest` |
| `WAHA_DASHBOARD_PASSWORD` | `changeme` | Un vrai mot de passe |
| `WAHA_API_KEY` | `cockpitwaha2026devkey` | Une cle secrete generee |

## 2. Generer une cle API securisee

Dans PowerShell :

```powershell
[guid]::NewGuid().ToString("N")
```

Copier le resultat et le mettre dans `WAHA_API_KEY` du docker-compose.

## 3. Lancer WAHA

```powershell
cd C:\chemin\vers\cockpit
docker compose up -d
```

Verifier que le conteneur tourne :

```powershell
docker ps
docker logs cockpit-waha --tail 20
```

## 4. Connecter la session WhatsApp

1. Ouvrir le dashboard WAHA : `http://localhost:3000/dashboard`
2. Se connecter avec :
   - URL : `http://localhost:3000`
   - API Key : la cle generee a l'etape 2
3. La session `default` doit apparaitre
4. Si elle est en STOPPED, la demarrer
5. Scanner le QR code avec le telephone WhatsApp dedie
   - Sur le telephone : Parametres > Appareils lies > Lier un appareil

## 5. Configurer Cockpit

Dans l'admin Cockpit (page Centrale d'Alerte, section WhatsApp) :

1. **URL WAHA** : `http://localhost:3000` (WAHA tourne sur le meme serveur)
2. **API Key WAHA** : la meme cle que dans le docker-compose
3. Cliquer **Enregistrer**
4. Verifier que le statut affiche "Connecte"
5. Cliquer **Synchroniser** pour charger les groupes
6. Activer les groupes souhaites
7. Envoyer un **message test** pour valider

## 6. Securite

- **Ne pas exposer le port 3000** sur Internet (pare-feu Windows)
  - WAHA doit etre accessible uniquement depuis localhost (Cockpit)
- Changer le mot de passe dashboard (`WAHA_DASHBOARD_PASSWORD`)
- Utiliser une cle API forte (`WAHA_API_KEY`)
- Le telephone connecte doit etre dedie a cet usage (pas un telephone personnel)

## 7. Maintenance

### Redemarrer WAHA

```powershell
docker compose restart waha
```

### Mettre a jour WAHA

```powershell
docker compose pull waha
docker compose up -d
```

### Voir les logs

```powershell
docker logs cockpit-waha --tail 50 -f
```

### Si la session se deconnecte

La session peut se deconnecter si :
- Le telephone n'a plus Internet
- L'appli WhatsApp est fermee/mise a jour sur le telephone
- Le telephone a ete eteint trop longtemps

Pour reconnecter :
1. Aller sur le dashboard WAHA
2. Supprimer la session existante
3. Recreer la session `default`
4. Scanner a nouveau le QR code

### Sauvegarder les sessions

Les sessions sont dans des volumes Docker. Pour les sauvegarder :

```powershell
docker run --rm -v cockpit_waha_sessions:/data -v C:\backup:/backup alpine tar czf /backup/waha_sessions.tar.gz -C /data .
```

## 8. Depannage

| Probleme | Solution |
|----------|----------|
| WAHA injoignable | Verifier `docker ps`, relancer `docker compose up -d` |
| 401 Unauthorized | Verifier que la cle API dans Cockpit correspond a celle du docker-compose |
| QR code ne s'affiche pas | Redemarrer la session dans le dashboard WAHA |
| Messages non envoyes | Verifier le circuit breaker et les rate limits dans l'admin Cockpit |
| Session deconnectee | Verifier le telephone, rescanner le QR code |
