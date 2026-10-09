# -*- coding: utf-8 -*-
"""Reecriture des logigrammes du wiki (09/10/2026) : decisions OBJECTIVES.

Regles :
- une decision = une question a laquelle l'operateur (ou l'agent sur place)
  repond par ce qu'il VOIT ou ENTEND, pas par une appreciation
  (<< Respire-t-elle ? >> et non << Gravite vitale ? >>) ;
- chaque branche dit QUOI faire et QUI engager : jamais << poursuivre >> ;
- plus de deux reponses possibles : un embranchement (fork, 4 branches max) ;
- les verifications de suivi (<< Resolu ? >>, << Fait ? >>) deviennent une
  etape de suivi, pas une decision.
Usage : python reecriture_wiki.py            (controle, aucune ecriture)
        python reecriture_wiki.py --apply    (ecrit dans titan)
"""
import sys
from datetime import datetime, timezone
from pymongo import MongoClient


def S(t): return {"k": "start", "t": t}
def A(t): return {"k": "act", "t": t}
def E(t): return {"k": "engage", "t": t}
def W(t): return {"k": "watch", "t": t}
def END(t): return {"k": "end", "t": t}
def Q(t, y, n): return {"k": "ask", "t": t, "y": y, "n": n}
def F(t, *br): return {"k": "fork", "t": t, "branches": [{"label": l, "flow": f} for l, f in br]}


NEW = {}

# ----------------------------------------------------------------- SECOURS
NEW["P01"] = dict(flow=[
    S("Alerte secours"),
    A("Localiser : carroyage ou repère (n° de WC, tribune, aire)"),
    E("CMS sans délai (horodater l'appel)"),
    F("Que constate-t-on sur la personne ?",
      ("Ne répond pas ou ne respire pas", [E("Pompiers en plus du CMS"), E("Patrouille : sécuriser la zone")]),
      ("Saigne abondamment ou convulse", [E("Pompiers en plus du CMS"), E("Patrouille : sécuriser la zone")]),
      ("Consciente, répond", [A("CMS seul")])),
    A("Suivre jusqu'à la prise en charge, noter la destination (CMS, CHU, Pôle Santé Sud)"),
    END("Clôture sur confirmation de la prise en charge")],
    questions=["Répond-elle quand on lui parle ? Respire-t-elle ?",
               "Saigne-t-elle abondamment ou convulse-t-elle ?",
               "Où exactement (carroyage, n° de WC, tribune, repère le plus proche) ?",
               "Que s'est-il passé, et depuis combien de temps ?",
               "Y a-t-il un risque pour la zone (circulation, foule) ?"])

NEW["P02"] = dict(flow=[
    S("Signalement"),
    A("Signalement précis : prénom, âge, taille, tenue, dernière position et heure"),
    E("Appel général radio à tous les secteurs + recherche à la vidéo PC"),
    A("Convenir d'un point de rencontre avec les proches"),
    Q("Est-ce un enfant ou une personne vulnérable (PMR, désorientée) ?",
      "Fiche Secours, diffusion maintenue jusqu'à la découverte",
      "Adulte : reclasser la fiche en Sécurité"),
    E("À la découverte : patrouille, raccompagner au point de rencontre"),
    END("Confirmer la réunification, remercier les secteurs")])

NEW["P23"] = dict(flow=[
    S("Départ de feu signalé"),
    E("Pompiers sans attendre, même pour un début de feu (horodater)"),
    E("Patrouille : périmètre, éloigner tentes et véhicules proches"),
    W("Lever le doute (caméra ou agents sur place)"),
    Q("Y a-t-il un blessé ou une brûlure ?", "CMS", "Pas de CMS"),
    Q("Est-ce un véhicule ou une moto qui a brûlé ?",
      "Après extinction : enlèvement (fourrière / consigne)", "Pas d'enlèvement"),
    END("Extinction confirmée, Tango sécurise (rubalise, barrières), clôture")])

NEW["P24"] = dict(flow=[
    S("Personne alcoolisée signalée"),
    A("Localiser (carroyage), seule ou en groupe"),
    F("Que constate-t-on ?",
      ("Ne répond pas, convulse ou malaise", [E("CMS sans délai (voir P01)")]),
      ("Allongée mais répond", [E("CMS (dans le doute, toujours le CMS)")]),
      ("Debout, virulente ou en groupe", [E("Patrouille (renfort S3M / AENEAS si groupe)")]),
      ("Au volant ou accélérations dangereuses", [E("Patrouille + Police")])),
    Q("Insultes, refus ou agressivité envers les agents ?",
      "Identités, photo des billets, retrait ou annulation, Police si nécessaire",
      "Pas de retrait"),
    END("Clôture après prise en charge (noter si le CMS la garde en surveillance)")])

NEW["P25"] = dict(flow=[
    S("Accident signalé"),
    A("Localiser, véhicules impliqués, piéton ?"),
    Q("Y a-t-il un blessé ?", "CMS / Reflex sans délai (ambulance), horodater", "Pas de secours"),
    Q("L'accident est-il sur la voie publique (hors enceinte) ?",
      "Police constate : transmettre les coordonnées du chauffeur",
      "Patrouille : identités des deux parties + plaque à la caméra"),
    Q("Les véhicules gênent-ils la circulation ou la piste ?",
      "Pousser hors voie ou enlèvement ; Direction si la piste est touchée", "Laisser en place pour le constat"),
    A("Constatation Police, noter si la victime veut porter plainte"),
    END("Clôture (nombre de blessés mis à jour)")])

NEW["P39"] = dict(flow=[
    S("Véhicule de secours annoncé"),
    E("Prévenir la porte, faire libérer le passage (barrières, flux piéton)"),
    Q("Une sortie de public est-elle en cours ?",
      "La porte dégage un couloir, l'appui flux facilite le passage", "Passage direct"),
    Q("Le trajet passe-t-il par la piste ?", "Accord Direction de course", "Pas de Direction de course"),
    W("Suivre à la caméra, noter les heures d'entrée et de sortie"),
    END("Lier à la fiche victime (P01), clôture")])

NEW["P43"] = dict(flow=[
    S("Fuite signalée"),
    F("Qu'est-ce qui fuit ?",
      ("Gaz", [E("Pompiers"), A("Éloigner le public, aucune flamme")]),
      ("Carburant ou huile", [A("Baliser (risque de chute)"), E("Technique / Tango : absorbant")]),
      ("Eau d'une bouche d'incendie", [E("Technique / SERI : refermer, vérifier qu'elle reste utilisable")]),
      ("Bidon suspect (siphonnage)", [W("Lever le doute à la caméra (voir P07)")])),
    E("Patrouille : périmètre"),
    END("Fuite arrêtée et vérifiée, lever le périmètre")])

# ---------------------------------------------------------------- SECURITE
NEW["P03"] = dict(flow=[
    S("Signalement d'intrusion"),
    A("Localiser : accès ou portail, nombre de personnes"),
    W("Lever le doute vidéo (n° de caméra + heure)"),
    F("Que montre la caméra ?",
      ("Personne accréditée", [A("Régulariser, laisser")]),
      ("Personne sans accréditation", [E("Patrouille : identité + photo, retrait de l'accréditation (help desk), expulsion")]),
      ("Poste d'accès sans agent", [E("Patrouille : retrouver l'agent (noter nom et société)")]),
      ("Accès ouvert ou forcé, sans personne", [A("Pas d'interpellation")])),
    E("Tango : resécuriser l'accès (cadenas, chaîne, Héras)"),
    Q("Le même accès a-t-il déjà été colmaté avant ?",
      "Remonter à la coordination : solution de nuit (renfort, S3M)",
      "Passer la consigne à la vacation suivante"),
    END("Clôture + consigner la vidéo")])

NEW["P04"] = dict(flow=[
    S("Faille signalée"),
    W("Lever le doute vidéo"),
    Q("Des personnes passent-elles par la faille en ce moment ?",
      "Patrouille + S3M tout de suite (voir P03)", "Patrouille + S3M : constat"),
    E("Tango : sécuriser (Héras, cadenas, serflex)"),
    Q("La faille avait-elle déjà été colmatée ?",
      "Remonter à la coordination : solution de nuit",
      "Patrouilles régulières S3M jusqu'à consolidation"),
    END("Clôture sur colmatage confirmé")])

NEW["P05"] = dict(flow=[
    S("Agression signalée"),
    E("Patrouille immédiatement"),
    Q("Y a-t-il un blessé ?", "CMS / Reflex sans délai", "Pas de secours"),
    Q("L'agresseur est-il encore sur place ?",
      "Patrouille : identité, billet annulé ou accréditation retirée, raccompagner dehors",
      "Le rechercher à la caméra (n° + timecode)"),
    A("Recueillir la victime au PC : identité, récit"),
    Q("La victime veut-elle porter plainte ?",
      "Coordonnées prêtes pour la Police ; plainte en ligne ou commissariat du Mans",
      "Le noter dans la fiche"),
    END("Clôture, liens entre fiches, traçabilité complète")])

NEW["P06"] = dict(flow=[
    S("Altercation signalée"),
    Q("Une arme est-elle vue ou signalée ?", "Police sans délai, prévenir la patrouille", "Pas de Police à ce stade"),
    F("Combien de personnes, et comment ?",
      ("2 ou 3, verbal", [E("Patrouille : apaiser")]),
      ("Coups échangés, ou groupe", [E("Patrouille + renforts (P2 + P3, appui S3M)"), E("Coordo AENEAS : point de situation")])),
    Q("Y a-t-il un blessé ?", "CMS (voir P01)", "Pas de secours"),
    Q("Un agent a-t-il reçu un coup ?", "Police pour constatation, noter le lieu précis", "Pas de constatation"),
    A("Après apaisement : présence préventive (passages de nuit)"),
    END("Clôture (noter si une plainte est déposée ou non)")],
    acteurs="Patrouille et renforts (coordo AENEAS), S3M, Police si présence d'arme ou coup porté à un agent, CMS si blessé.")

NEW["P07"] = dict(flow=[
    S("Vol signalé"),
    A("Décrire : objet, numéro ou plaque, lieu, heure"),
    F("Qu'est-ce qui a disparu ?",
      ("Golfette ou véhicule", [A("Croiser le fichier fourrière AVANT de conclure au vol")]),
      ("Article en boutique, auteur retenu", [A("Identité même s'il refuse, vérifier l'achat, raccompagner en sortie")]),
      ("Effets personnels", [W("Chercher l'objet ou l'auteur à la caméra (n° + heure)")])),
    E("Signalement à toutes les patrouilles, blocage aux sorties"),
    A("Conseiller le dépôt de plainte, noter le téléphone du plaignant"),
    END("Clôture (si retrouvé : informer le propriétaire)")])

NEW["P08"] = dict(flow=[
    S("Bien endommagé"),
    W("Lever le doute vidéo (plaque, circonstances)"),
    Q("Y a-t-il un danger immédiat (palissade ou grillage instable, débris) ?",
      "Sécuriser d'abord (plots, parpaings, conteneur)", "Pas de sécurisation préalable"),
    Q("La caméra ou un témoin montre-t-il un geste volontaire, ou est-ce un tag ?",
      "Police, relever l'identité ; l'ACO envisage la plainte",
      "Accident : constat amiable, identité du responsable"),
    E("Patrouille : constat + identité"),
    E("Technique : remise en état"),
    END("Mail ACO avec photos, clôture")])

NEW["P09"] = dict(flow=[
    S("Infraction signalée"),
    W("Lever le doute vidéo"),
    E("Patrouille / appui mobile : identifier (plaque, marque, couleur)"),
    F("Quelle infraction ?",
      ("Véhicule sans titre", [A("Fourrière (voir P19), validation par la coordination")]),
      ("Échappement ou véhicule non homologué", [E("Patrouille + renfort : saisie"), A("Noter « vigilance retour » pour le lendemain")]),
      ("Stationnement sur emplacement réservé", [A("Voir P19")])),
    Q("La personne refuse-t-elle d'obtempérer ?",
      "Visuel caméra (timecode), prévenir Reflex, Police pour identification", "Pas de Police"),
    END("Clôture, ou consigne claire à la vacation suivante")])

NEW["P10"] = dict(flow=[
    S("Fraude signalée"),
    E("Patrouille : contrôle et contact, vite (OPV en recherche à la caméra)"),
    F("Quelle fraude ?",
      ("Revente ou fausse identité constatée", [A("Raccompagner en sortie, retirer ou annuler le titre, noter l'identité")]),
      ("Même titre scanné deux fois", [A("Signaler au SERI, remonter à la coordination / PCA")]),
      ("Badge d'un tiers", [A("Identifier le titulaire réel, retirer ses droits d'accès")]),
      ("Forçage d'accès", [E("Renfort de patrouille")])),
    Q("Refuse-t-il de rendre son titre ?", "Police (intrusion en enceinte sportive)", "Pas de Police"),
    END("Clôture")])

NEW["P11"] = dict(flow=[
    S("Congestion à une porte"),
    A("Porte concernée, longueur de file"),
    F("D'où vient le blocage ?",
      ("Fouille ou affluence", [E("Patrouilles ou appui flux en renfort"), A("Orienter vers passerelles et cheminements alternatifs")]),
      ("Scan ou PDA", [E("SERI en parallèle (voir P14)")]),
      ("Litiges billets", [A("Orienter vers le point info litiges (passage sur autorisation PCA seulement)")])),
    A("Suivi : file résorbée, lever le dispositif étape par étape (P3 puis P2)"),
    END("Clôture")])

NEW["P26"] = dict(flow=[
    S("Objet abandonné signalé"),
    A("Localiser précisément ; ne pas faire manipuler l'objet"),
    W("Lever le doute : caméra + patrouille sur place"),
    F("Résultat de la levée de doute ?",
      ("Propriétaire retrouvé", [A("Restituer après vérification d'identité")]),
      ("Contenu vu, sans danger (oubli)", [A("Lever le doute, consigner")]),
      ("Aucun propriétaire, contenu inconnu", [E("Périmètre + évacuation du public (appui mobile)"), E("Brigade cynophile"), A("Articuler avec le PC Sûreté / PCA")])),
    END("Lever le périmètre, clôture")])

NEW["P27"] = dict(flow=[
    S("Évacuation demandée"),
    A("Zone, raison, heure, nombre de personnes, sorties"),
    F("Pour quelle raison ?",
      ("Fin de séquence (concert, welcome)", [E("Motos appui flux aux points d'aiguillage")]),
      ("Sécurité (périmètre)", [A("Sur ordre Direction / PCA"), E("Motos appui flux + patrouilles aux points d'aiguillage")]),
      ("Ce sont des véhicules à sortir", [A("Voir P19 (enlèvement)")])),
    A("Évacuer zone par zone, gérer les retardataires (passerelle ou portail encore ouvert)"),
    E("PS Nord : fermer derrière, remettre la rubalise"),
    END("Zone vidée, lever le dispositif")])

NEW["P28"] = dict(flow=[
    S("Drone / survol signalé"),
    Q("Le vol est-il au planning (essais, spectacle, média) ?",
      "Noter et suivre, aucun moyen engagé",
      "Patrouille : localiser le télépilote ou le point d'atterrissage"),
    A("Aviser la coordination jour + la Police"),
    A("Transmettre l'adresse ou la position, consigner"),
    END("Clôture")])

NEW["P29"] = dict(flow=[
    S("Nuisance sonore signalée"),
    A("Localiser + qui signale"),
    F("La source est-elle identifiée ?",
      ("Oui (campement, groupe)", [E("Patrouille : rappel à l'ordre")]),
      ("Non (cri, bruit)", [E("Patrouille : ratisser, lever le doute avant d'escalader")]),
      ("Sono ou balance de spectacle", [A("Informer le responsable pour ajuster")])),
    Q("Les mêmes personnes ont-elles déjà été rappelées à l'ordre ?",
      "Identifier, retrait du billet si nécessaire", "Rappel à l'ordre simple"),
    Q("Ivresse, insultes ou dégradations ?", "Basculer en P24 ou P06", "Rester en P29"),
    END("Consigner, vigilance à passer à la vacation suivante")])

NEW["P34"] = dict(flow=[
    S("Alarme / demande"),
    Q("Une prestation est-elle prévue dans le bâtiment (ménage, traiteur) ?",
      "Tracer le demandeur, prévoir la remise sous alarme", "Lever le doute"),
    W("Caméra + patrouille ou agent SSIAP"),
    F("Que constate-t-on ?",
      ("Fumée, odeur de brûlé ou défaut SSI", [A("P23 : pompiers")]),
      ("Présence d'une personne", [A("Traiter comme une intrusion (P03)")]),
      ("Rien d'anormal", [A("Consigner, remettre sous alarme")])),
    END("Bâtiment remis sous alarme, clôture")])

NEW["P36"] = dict(flow=[
    S("Animal / nid signalé"),
    A("Localiser, décrire (race, couleur, collier)"),
    F("Quel cas ?",
      ("Piqûre ou morsure", [E("CMS sans délai (voir P01)")]),
      ("Chien enfermé dans un véhicule", [A("Retrouver le propriétaire en priorité (plaque, annonce)"), A("Police si l'animal est en danger")]),
      ("Nid près du public", [E("Patrouille : baliser"), E("Technique : destruction")]),
      ("Chien perdu, trouvé ou en zone interdite", [A("Diffuser la description, garder au poste le plus proche")])),
    END("Restitution ou nid traité, clôture")])

NEW["P38"] = dict(flow=[
    S("Arme / objet dangereux"),
    Q("L'arme est-elle brandie, ou y a-t-il une menace ?", "Police immédiatement + P06", "Vérifier la qualité du porteur"),
    F("Qui porte l'objet ?",
      ("Policier ou militaire (carte présentée)", [A("Consigne du PCA, tracer nom et service")]),
      ("Professionnel (traiteur, outils)", [A("Vérifier l'employeur et la destination, tracer l'autorisation")]),
      ("Autre", [A("Objet à la consigne de la porte, ou accès refusé")])),
    END("Noter la décision et qui l'a prise, clôture")])

NEW["P41"] = dict(flow=[
    S("Conduite dangereuse"),
    W("Plaque + trajectoire à la caméra"),
    E("Patrouille : intercepter, identifier"),
    Q("Le conducteur refuse-t-il, ou semble-t-il alcoolisé ?", "Police (voir P24)", "Rappel + retrait du titre si besoin"),
    Q("Y a-t-il un blessé ?", "P25 Accident de circulation", "Pas de secours"),
    Q("Le même véhicule a-t-il déjà été signalé ?", "Retrait du titre, sortie du site", "Noter le véhicule"),
    END("Clôture")])

NEW["P42"] = dict(flow=[
    S("Feu signalé"),
    Q("Le feu s'étend-il, ou est-il près de tentes ou de véhicules ?",
      "P23 : pompiers immédiatement", "Patrouille : éteindre + rappel du règlement"),
    Q("Refus ou récidive ?", "Identité, retrait éventuel du titre de camping", "Rappel simple"),
    END("Extinction vérifiée, vigilance à passer, clôture")])

NEW["P44"] = dict(flow=[
    S("Engin en zone public"),
    Q("Y a-t-il du public dans le rayon d'action de l'engin ?",
      "Faire arrêter le mouvement tout de suite", "Rappel de la consigne horaire"),
    Q("Quelqu'un est-il monté dessus sans pouvoir redescendre seul ?",
      "Périmètre au sol + pompiers, ne pas le laisser seul", "Pas de pompiers"),
    E("Responsable de la société : rappel de consigne (horaires, zones)"),
    END("Clôture (dégât causé : voir P08)")])

NEW["P46"] = dict(flow=[
    S("Heure de mise en place"),
    A("Demander le point au prestataire"),
    Q("Le point a-t-il été transmis à l'heure ?", "Le consigner", "Relancer : « non transmis » n'est pas « en place »"),
    Q("Tous les postes sont-ils tenus ?", "Consigner l'effectif complet",
      "Vérifier (caméra, patrouille), demander un remplacement, noter heure et poste"),
    A("Suivre les fins de service et la remise du matériel (radios, clés, véhicules)"),
    END("Clôture")])

NEW["P48"] = dict(flow=[
    S("Séquence spectacle"),
    E("Fouille de la zone (cynophile), noter début et fin"),
    Q("La zone est-elle fouillée et vide ?", "Ouvrir au public", "Ne pas ouvrir"),
    Q("Y a-t-il un feu d'artifice ?",
      "Faire confirmer le périmètre de tir vide JUSTE avant le tir", "Pas de périmètre de tir"),
    W("Surveiller densité et secours"),
    END("Enchaîner sur le plan de sortie (P32)")])

NEW["P50"] = dict(flow=[
    S("Alerte météo"),
    A("Relayer à tous les secteurs la consigne affichée sur le mur météo"),
    F("Quel phénomène ?",
      ("Vent fort", [E("Tango : renforcer ou coucher barrières, tonnelles, bâches")]),
      ("Orage", [E("Patrouilles : prévention, mise à l'abri (campings, paddock)")]),
      ("Fortes pluies", [A("Contrôler les tunnels piétons, baliser les aires en herbe (P40)")]),
      ("Chaleur", [A("Points d'eau aux files d'attente, prévenir le CMS")])),
    END("Après l'épisode : contrôle des dégâts et des installations tombées")])

# --------------------------------------------------------------- TECHNIQUE
NEW["P12"] = dict(flow=[
    S("Coupure signalée"),
    A("Localiser précisément (carroyage, n° de borne ou de bungalow)"),
    Q("Un poste sensible est-il touché (contrôle d'accès, éclairage piste) ?",
      "Traiter en priorité", "Traitement normal"),
    F("Quelle panne ?",
      ("Courant, disjoncteur, éclairage", [E("Service Élec. (la nuit : astreinte électrique)")]),
      ("Réseau ou équipement informatique", [E("SERI")])),
    A("Suivi : relancer et noter chaque relance ; la nuit, transmettre à l'équipe de nuit"),
    END("Rétablissement confirmé au chef de zone, clôture")])

NEW["P13"] = dict(flow=[
    S("Panne sanitaire"),
    A("Référence exacte du sanitaire (n° WC, réf., prestataire)"),
    Q("L'eau coule-t-elle fort, ou y a-t-il un risque (inondation, électricité) ?",
      "Patrouille : faire couper ou bloquer d'abord", "Pas de coupure"),
    E("Service technique (prestataires DRON, Passenaud)"),
    A("Suivi : relancer, noter chaque relance, refaire le point avec le chef de zone"),
    END("Réparation confirmée au chef de zone, clôture")])

NEW["P14"] = dict(flow=[
    S("Matériel en défaut"),
    A("Identifier : n° de tripode ou de scan, porte"),
    E("SERI : redémarrage à distance"),
    Q("Le matériel fonctionne-t-il après le redémarrage ?",
      "Confirmer au chef de poste",
      "Porte en sortie manuelle (responsable de zone) + technicien"),
    Q("La panne revient-elle (marche puis s'arrête) ?",
      "Sur consigne PCA : condamner le tripode jusqu'à la fin du week-end", "Clôture sur rétablissement"),
    END("Clôture")])

NEW["P15"] = dict(flow=[
    S("Besoin / incident matériel"),
    A("Qualifier, localiser (carroyage), photos"),
    Q("Y a-t-il un danger immédiat (plaque soulevée, trou, palissade instable) ?",
      "Sécuriser d'abord (Tango, plots, conteneur)", "Pas d'urgence"),
    F("Que faut-il faire ?",
      ("Déplacer, poser ou sécuriser", [E("Tango")]),
      ("Réparer", [E("Service technique")]),
      ("Ouvrir une palissade ou modifier un ouvrage", [A("Accord Direction de Course / responsable, le noter")])),
    A("Suivi : relancer ; mail au technique avec photos si différé"),
    END("Confirmer la réalisation, clôture")])

NEW["P16"] = dict(flow=[
    S("Demande de balisage"),
    A("Quoi (plots, Héras, Vauban), où, combien"),
    E("Tango : pose / retrait"),
    Q("Est-ce lié à l'ouverture ou la fermeture d'un accès ?",
      "Caler sur l'horaire de l'accès (voir P20)", "Pas de synchronisation"),
    Q("L'accès doit-il rester fermé ?", "PS : cadenas + chaîne sur les Héras", "Pas de cadenas"),
    Q("Vent ou averses annoncés ?", "Renforcer (plots, jambes de force), recontrôler après", "Pas de renfort"),
    END("Confirmer la réalisation, clôture")])

NEW["P17"] = dict(flow=[
    S("Panneau à traiter"),
    A("Identifier le panneau, l'emplacement (carroyage), le demandeur"),
    Q("Le panneau tombé gêne-t-il la circulation ?", "Priorité (risque)", "Basse priorité, à relancer"),
    Q("Est-ce un affichage réglementaire (objets interdits aux portes) ?",
      "Vérifier que toutes les portes concernées sont couvertes", "Panneau seul"),
    E("Tango : pose / repose"),
    A("Suivi : relancer, noter chaque relance"),
    END("Pose effective confirmée, clôture")])

NEW["P35"] = dict(flow=[
    S("Moyen de communication en panne"),
    A("Identifier le moyen et la zone"),
    F("Quel moyen ?",
      ("Radios ou talkies", [A("Moyen de repli tout de suite (portable du responsable, autre canal), le diffuser")]),
      ("Téléphone fixe d'un point", [A("Noter le numéro de repli dans la fiche")]),
      ("Caméras d'une zone", [E("Renforcer les passages de patrouille le temps de la panne")]),
      ("PDA ou tripode", [A("Voir P14")])),
    E("SERI"),
    A("Suivi : relancer jusqu'au rétablissement"),
    END("Rétablissement confirmé aux postes, clôture")])

NEW["P45"] = dict(flow=[
    S("Personne enfermée"),
    A("Garder le contact, vérifier son état"),
    Q("Malaise, panique ou chaleur ?", "CMS (voir P01)", "Pas de CMS"),
    F("Où est-elle bloquée ?",
      ("Ascenseur", [E("Technique / ascensoriste")]),
      ("Local ou zone fermée", [E("Détenteur de la clé ou du code (clé partie : voir P21)")])),
    END("Libérée, noter la cause (ronde de fermeture)")])

# -------------------------------------------------------------------- FLUX
NEW["P18"] = dict(flow=[
    S("Congestion signalée"),
    A("Localiser le point de blocage"),
    F("Qu'est-ce qui bloque ?",
      ("Affluence de véhicules", [E("Appui flux / patrouille : réguler")]),
      ("Affluence de piétons à une porte", [A("Voir P11 Renfort de filtrage")]),
      ("Véhicule arrêté sur la voie", [W("Personne à bord ?"), A("Plaque, appui flux vérifie, fourrière si besoin (P19)")]),
      ("Engin ou chariot abandonné", [E("Tango récupère (noter les références caméra)")])),
    A("Rappeler plus tard même si le responsable dit « pas besoin »"),
    END("Retour à la normale : lever le dispositif, noter l'heure")])

NEW["P19"] = dict(flow=[
    S("Véhicule gênant"),
    A("Localiser"),
    W("Personne à bord ? (appui flux ou unité mobile vérifie sur place)"),
    A("Plaque, titre, modèle et gabarit"),
    Q("Le véhicule est-il sur la voie publique ?",
      "Police (Nationale ou Municipale) via la coordination, pas la fourrière ACO",
      "Sur site : fourrière ACO possible"),
    Q("A-t-il un titre de stationnement pour CE parking ?",
      "Avis de la coordination avant tout enlèvement",
      "Fourrière 3J sur validation de la coordination (donner modèle + gabarit)"),
    A("Horodater arrivée 3J, enlèvement, dépôt ; priorité PMR et espaces circuit"),
    A("Restitution contre paiement + fichier « Suivi fourrière »"),
    END("Clôture")])

NEW["P31"] = dict(flow=[
    S("Site proche de la saturation"),
    A("Faire confirmer le complet par le responsable, noter l'heure"),
    Q("Le site de délestage prévu est-il ouvert, éclairé et tenu ?",
      "Délester, prévenir le site receveur", "Ne pas y envoyer : demander un autre site à la coordination"),
    A("Prévenir les postes amont, adapter la signalétique (panneaux, PMV)"),
    Q("La file remonte-t-elle sur la voie publique (D338, rond-point) ?", "Police municipale", "Surveiller la file"),
    Q("Les places PMR sont-elles complètes ?", "Noter où les PMR sont renvoyés", "Pas de renvoi PMR"),
    END("Consigner, suivre la réouverture")])

NEW["P32"] = dict(flow=[
    S("Fin de séquence approche"),
    A("Vérifier le dispositif, annoncer « prêt » au PCA avec l'heure"),
    Q("Le PCA a-t-il donné le GO ?", "Activer, noter l'heure", "Attendre, dispositif prêt"),
    W("Contrôler chaque portail du plan et les saturations à la caméra"),
    Q("Un portail du plan n'est-il pas dans l'état prévu ?", "Faire corriger tout de suite, le noter", "Plan conforme"),
    END("Parkings vides : lever, noter l'heure de fin")])

NEW["P40"] = dict(flow=[
    S("Véhicule bloqué"),
    A("Localiser, gabarit, conducteur présent ?"),
    Q("Bloque-t-il une voie ou un accès de secours ?",
      "Priorité : appui flux dévie la circulation", "Non prioritaire : traiter quand un moyen est libre"),
    F("Quel véhicule ?",
      ("Camion ou poids lourd", [E("Dépanneur adapté (donner type et poids)")]),
      ("Voiture", [E("Tango, ou pousser hors voie avant le dépanneur")])),
    Q("Le sol est-il détrempé ?", "Baliser les zones basses, prévenir les responsables d'aires", "Pas de balisage"),
    END("Dégagé, clôture")])

NEW["P49"] = dict(flow=[
    S("Installation hors zone"),
    A("Localiser, compter, vérifier le titre"),
    Q("Bloque-t-il un accès, un portail ou une voie de secours ?",
      "Faire déplacer en priorité", "Orienter vers l'aire prévue"),
    Q("Refuse-t-il de se déplacer ?", "Identité, puis fourrière (P19) si la gêne le justifie", "Pas de fourrière"),
    Q("Afflux avant l'ouverture de l'aire ?", "Proposer à la coordination d'avancer l'ouverture", "Pas de changement"),
    END("Clôture")])

# ------------------------------------------------------------------- ACCES
NEW["P20"] = dict(flow=[
    S("Ordre / horaire d'accès"),
    A("Confirmer accès, sens, horaire et donneur d'ordre (PCA, Direction, planning)"),
    Q("Est-ce une ouverture au public ?",
      "Vérifier agents, PDA, tripodes (et éclairage la nuit) avant de valider",
      "Fermeture : vérifier zone par zone (aires : ratissage)"),
    E("PS Nord / chef de zone : exécuter"),
    W("Contrôler à la caméra (noter le n°)"),
    Q("La caméra montre-t-elle l'accès dans l'état demandé ?", "Confirmer l'exécution", "Renvoyer l'exécutant, ne pas valider"),
    Q("Une file ou une affluence est-elle attendue à cet accès ?", "Renfort appui flux (voir P11)", "Pas de renfort"),
    END("Consigne passée à la vacation suivante, clôture")])

NEW["P21"] = dict(flow=[
    S("Demande reçue"),
    A("Qualifier : accès, autorisation de stationner, clé, badge"),
    Q("La personne ou le véhicule a-t-il une accréditation valable pour cet accès ?",
      "Exécuter, prévenir les postes",
      "Validation PCA / Direction obligatoire avant d'ouvrir"),
    Q("Est-ce une clé ?", "Noter le local, qui la détient, qui la récupère", "Pas de clé"),
    E("Prévenir les postes (chef de poste, PS Nord), surveillance OPV si besoin"),
    A("Tracer : validateur, plaque, durée"),
    END("Clôture en fin de besoin")])

NEW["P22"] = dict(flow=[
    S("Mouvement à encadrer"),
    A("Itinéraire, horaires, nombre de véhicules, référent"),
    E("Appui flux : accompagner, prévenir les postes de chaque passage"),
    Q("Le mouvement emprunte-t-il la route (RD) ?", "Bloquer la voie AVANT le départ (PM ou appui flux)", "Pas de blocage de route"),
    Q("Passe-t-il sur la piste ?", "Accord Direction de Course, PS Nord ouvre la palissade", "PS Nord ouvre les portails au passage"),
    Q("Le convoi est-il hors gabarit (tunnels) ?", "Valider l'itinéraire avant le départ", "Itinéraire normal"),
    W("Suivre à la caméra"),
    END("Refermer derrière, lever le dispositif au retour")])

NEW["P30"] = dict(flow=[
    S("Jalon prévu ou ordonné"),
    E("Poste concerné : exécuter"),
    Q("Le poste confirme-t-il que c'est fait ET opérationnel (agents, PDA, éclairage) ?",
      "Fiche avec l'heure effective en tête", "Écrire ce qui est constaté, relancer"),
    Q("L'heure effective diffère-t-elle du prévu ?", "Noter l'écart et sa cause", "Pas d'écart"),
    A("Message radio à tous les secteurs si jalon majeur"),
    END("Clôture immédiate")])

NEW["P33"] = dict(flow=[
    S("Difficulté PMR signalée"),
    A("Localiser, qualifier le besoin"),
    Q("La personne a-t-elle chuté ou fait un malaise ?", "P01 Secours à victime", "Pas de secours"),
    F("Quel besoin ?",
      ("Accès bloqué", [A("Ouvrir un passage proche, ne pas renvoyer vers une autre porte")]),
      ("Place PMR occupée ou complète", [A("Rediriger vers des places PMR libres ; occupée sans macaron : P19")]),
      ("Sans macaron ou sans titre", [A("Vérifier au help desk, arbitrer et tracer (P21)")]),
      ("Matériel en panne (scooter, fauteuil)", [E("Patrouille ou appui mobile : accompagner")])),
    END("Confirmer, clôture")])

NEW["P37"] = dict(flow=[
    S("Objet trouvé / perdu"),
    F("Quel cas ?",
      ("Objet trouvé", [A("Décrire, noter le lieu de conservation")]),
      ("Objet perdu", [A("Rechercher dans la main courante (nom, objet)")]),
      ("Véhicule introuvable", [A("Fourrière d'abord (P19), puis titre de stationnement")])),
    A("Rapprocher avec les signalements existants"),
    Q("Le propriétaire est-il identifié ?", "Restituer contre pièce d'identité", "Conserver, garder la fiche ouverte"),
    END("Clôture à la restitution")])

NEW["P47"] = dict(flow=[
    S("Riverain"),
    F("Quel cas ?",
      ("Bloqué, veut passer", [A("Titre riverain valide : faire passer, prévenir le poste ; titre inconnu : vérifier avec la coordination")]),
      ("Plainte (bruit, klaxons, stationnement)", [A("Tracer précisément, remonter à la coordination")]),
      ("A forcé le passage", [A("Plaque + heure, informer la coordination ; Police si danger")])),
    END("Clôture")])

# --------------------------------------------------------- OUTIL COCKPIT (C)
NEW["C01"] = dict(flow=[
    S("Événement signalé"),
    A("Clic droit sur la carte > catégorie"),
    A("Description, source, canal"),
    Q("L'encart « fiches similaires » apparaît-il ?", "Les ouvrir, compléter la fiche existante", "Continuer la création"),
    A("Détails : sous-classification, urgence, action prise"),
    END("Enregistrer")])

NEW["C02"] = dict(flow=[
    S("Qualifier la fiche"),
    F("Que constate-t-on ?",
      ("Une vie en jeu maintenant", [A("EU")]),
      ("Grave, moyens tout de suite", [A("UA")]),
      ("À traiter, sans risque immédiat", [A("UR")]),
      ("Personne concernée sans atteinte (témoin)", [A("IMP")])),
    END("Réévaluer si la situation change (Editer)")])

NEW["C04"] = dict(flow=[
    S("Affaire terminée ?"),
    Q("La prise en charge ou la réparation est-elle confirmée par le terrain ?",
      "Clore + bilan", "Laisser ouverte, relancer"),
    Q("L'affaire reprend-elle après clôture ?", "Rouvrir + motif", "Laisser close"),
    END("Fiche à jour")])

NEW["C10"] = dict(flow=[
    S("Message de session"),
    Q("Une saisie est-elle en cours ?", "Copier le texte avant de recharger", "Recharger directement"),
    A("Recharger, se reconnecter"),
    END("Vérifier la fiche dans « En cours »")])


# ---------------------------------------------------------------- controles
KINDS = {"start", "act", "watch", "engage", "ask", "end", "fork"}
VIDES = ("poursuivre", "suite", "")


def controle(code, flow, depth=0):
    err = []
    for n in flow:
        if n["k"] not in KINDS or not n.get("t"):
            err.append("%s noeud invalide %r" % (code, n))
        if n["k"] == "ask":
            for b in ("y", "n"):
                if (n.get(b) or "").strip().lower() in VIDES:
                    err.append("%s branche vide : %s" % (code, n["t"]))
            if not n["t"].endswith("?"):
                err.append("%s decision sans question : %s" % (code, n["t"]))
        if n["k"] == "fork":
            if not 2 <= len(n["branches"]) <= 4:
                err.append("%s fork %d branches" % (code, len(n["branches"])))
            if not n["t"].endswith("?"):
                err.append("%s fork sans question : %s" % (code, n["t"]))
            for b in n["branches"]:
                if not b["label"] or not b["flow"]:
                    err.append("%s branche vide dans %s" % (code, n["t"]))
                err += controle(code, b["flow"], depth + 1)
    return err


def main():
    apply = "--apply" in sys.argv
    col = MongoClient()["titan"]["cockpit_wiki_procedures"]
    existants = {d["code"]: d for d in col.find({}, {"_id": 0, "code": 1, "flow": 1, "version": 1})}
    err = []
    for code, ch in NEW.items():
        if code not in existants:
            err.append("%s absent" % code)
        err += controle(code, ch["flow"])
    # Fiches non reecrites : encore un << poursuivre >> ?
    for code, d in existants.items():
        if code not in NEW:
            err += ["(non reecrite) " + e for e in controle(code, d.get("flow") or [])]
    print("%d fiches reecrites sur %d" % (len(NEW), len(existants)))
    for e in err:
        print("  !", e)
    if err or not apply:
        print("aucune ecriture" + (" (erreurs)" if err else " (relancer avec --apply)"))
        return
    now = datetime.now(timezone.utc)
    for code, ch in NEW.items():
        patch = dict(ch)
        patch.update({"updated_at": now, "updated_by": "reecriture-decisions-2026-10-09",
                      "version": int(existants[code].get("version") or 1) + 1})
        col.update_one({"code": code}, {"$set": patch})
    print("ecrit :", ", ".join(sorted(NEW)))


if __name__ == "__main__":
    main()
