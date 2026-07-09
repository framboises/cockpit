# -*- coding: utf-8 -*-
"""
Seed AUTONOME du Wiki des procédures PC Orga — données embarquées en dur.

A poser sur le serveur de production et lancer directement : aucune dépendance
à un fichier source ni à titan_dev. Remplit les collections du wiki dans `titan`
(par défaut) :
    - cockpit_wiki_categories : {key,label,color,order}
    - cockpit_wiki_procedures : {code,titre,dom,...,flow[],status,version,...}

Usage :
    python seed_wiki_prod.py                 # DRY-RUN : montre ce qui serait fait
    python seed_wiki_prod.py --apply         # écrit dans `titan`
    python seed_wiki_prod.py --apply --db titan_dev   # cible une autre base
    python seed_wiki_prod.py --apply --overwrite      # ré-écrase statut/version des fiches existantes

Idempotent : upsert par `code` (fiches) et `key` (catégories). Ne touche
qu'à ces deux collections. Par défaut, préserve created_*/version/status
des fiches déjà présentes (édition prod non écrasée).
"""
import os
import argparse
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING

CATEGORIES = [
  {
    "key": "secours",
    "label": "Secours",
    "color": "#C81E1E",
    "order": 0
  },
  {
    "key": "securite",
    "label": "Sécurité et sûreté",
    "color": "#B5670A",
    "order": 1
  },
  {
    "key": "technique",
    "label": "Technique",
    "color": "#1D4ED8",
    "order": 2
  },
  {
    "key": "flux",
    "label": "Flux et fourrière",
    "color": "#0F7A4F",
    "order": 3
  },
  {
    "key": "acces",
    "label": "Accès et information",
    "color": "#0E7490",
    "order": 4
  }
]

PROCEDURES = [
  {
    "code": "P01",
    "titre": "Secours à victime",
    "dom": "secours",
    "situation": "Malaise, blessure, personne inconsciente ou en détresse. Situation critique : la vitesse d'engagement du CMS et la précision de la localisation font la différence.",
    "questions": [
      "La personne est-elle consciente ? Respire-t-elle ?",
      "Où exactement (zone, carroyage, repère le plus proche) ?",
      "Que s'est-il passé, et depuis combien de temps ?",
      "Y a-t-il un risque pour la zone (circulation, foule) ?"
    ],
    "acteurs": "CMS (systématique), Pompiers si gravité, patrouille pour sécuriser la zone, Service ACO en relais.",
    "conduite": [
      "Qualifier l'état (conscience, respiration, âge) et localiser précisément.",
      "Engager le CMS sans délai, horodater l'appel et l'arrivée des secours.",
      "Faire sécuriser la zone par une patrouille si besoin.",
      "Suivre jusqu'à la prise en charge, noter la destination (CHU, Pôle Santé Sud)."
    ],
    "consigner": "Localisation exacte, horodatage (appel, arrivée, évacuation), identité si connue, destination.",
    "pieges": "Une localisation imprécise retarde les secours. Ne pas clôturer avant confirmation de la prise en charge.",
    "souscas": [
      "Malaise / perte de connaissance",
      "Blessure ou traumatisme",
      "Personne alcoolisée inconsciente",
      "Détresse ou crise d'angoisse"
    ],
    "details": [
      "Précise l'état d'emblée : conscient, inconscient mais respire, ne respire pas. C'est ce qui décide du niveau de moyens (CMS seul, ou CMS + Pompiers).",
      "Localise au carroyage ou au repère le plus proche (n° de WC, tribune, aire d'accueil) : sans localisation nette, le CMS perd du temps.",
      "Horodate tout : appel, arrivée des secours, départ, évacuation. Note la destination (CMS, CHU, Pôle Santé Sud) et la porte de sortie.",
      "Personne alcoolisée : le CMS la garde souvent en surveillance et fait raccompagner les accompagnants à leur parking. Ne clôture qu'après prise en charge confirmée.",
      "Mineur ou personne en détresse : récupère l'identité et le téléphone d'un proche ; la Police peut prendre le relais si besoin."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Alerte secours"
      },
      {
        "k": "act",
        "t": "Qualifier : conscient ? respire ? où ?"
      },
      {
        "k": "ask",
        "t": "Gravité vitale ?",
        "y": "CMS + Pompiers, sécuriser",
        "n": "CMS seul"
      },
      {
        "k": "act",
        "t": "Suivre jusqu'à la prise en charge"
      },
      {
        "k": "end",
        "t": "Clôture sur confirmation"
      }
    ]
  },
  {
    "code": "P02",
    "titre": "Enfant perdu ou recherche de personne",
    "dom": "secours",
    "situation": "Enfant ou personne égarée signalée au PC. La clé est la diffusion immédiate à tous les secteurs et un point de rencontre clair avec les proches.",
    "questions": [
      "Nom, âge, taille, couleur des vêtements ?",
      "Où et à quelle heure a-t-il été vu pour la dernière fois ?",
      "Où sont les parents en ce moment ?",
      "L'enfant a-t-il un téléphone ? Un point de rendez-vous convenu ?"
    ],
    "acteurs": "Diffusion radio à tout le personnel d'accueil, patrouille de sécurité, recherche vidéo au PC.",
    "conduite": [
      "Recueillir un signalement précis (nom, âge, taille, vêtements, dernière position).",
      "Diffuser à tous les secteurs par radio et lancer une recherche vidéo.",
      "Orienter les parents vers un point de rencontre convenu.",
      "À la découverte, faire raccompagner par une patrouille et confirmer la réunification."
    ],
    "consigner": "Signalement complet, point de rencontre des parents, heure retrouvé.",
    "pieges": "Le téléphone de la personne recherchée est souvent éteint. Bien noter le point de rencontre convenu.",
    "souscas": [
      "Enfant perdu (parents au point info)",
      "Personne recherchée par un proche",
      "Personne vulnérable (PMR, désorientée)"
    ],
    "details": [
      "Signalement complet dès la première ligne : prénom, âge, taille, tenue (couleur), sac, dernière position vue et heure.",
      "Diffuse un « appel général » par radio à tout le personnel d'accueil, et lance en parallèle une recherche à la vidéo PC.",
      "Les parents sont en général au Point Info (3887) ou dans une boutique : conviens d'un point de rencontre (souvent l'entrée Nord).",
      "Le téléphone de l'enfant ou de la personne recherchée est presque toujours éteint : ne compte pas dessus.",
      "À la découverte, fais raccompagner par une patrouille, confirme la réunification et remercie les secteurs. Une recherche de personne majeure se reclasse en Sécurité."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Signalement"
      },
      {
        "k": "act",
        "t": "Recueillir un signalement précis"
      },
      {
        "k": "engage",
        "t": "Diffusion radio tous secteurs + vidéo"
      },
      {
        "k": "ask",
        "t": "Retrouvée ?",
        "y": "Raccompagner + réunir",
        "n": "Maintenir diffusion + patrouille"
      },
      {
        "k": "end",
        "t": "Confirmer la réunification"
      }
    ]
  },
  {
    "code": "P03",
    "titre": "Intrusion ou accès forcé",
    "dom": "securite",
    "situation": "Personne ou véhicule en zone réglementée, portail forcé, poste d'accès non tenu. La levée de doute vidéo décide avant tout engagement de moyen.",
    "questions": [
      "Où précisément, à quel accès ou portail ?",
      "Combien de personnes, et que font-elles ?",
      "Sont-elles accréditées (vérifiable à la caméra) ?",
      "L'accès est-il forcé ou simplement resté ouvert ?"
    ],
    "acteurs": "Levée de doute vidéo d'abord, puis patrouille de sécurité, PS Nord selon la zone, Tango pour resécuriser.",
    "conduite": [
      "Lever le doute à la caméra : elle dit s'il faut engager et comment.",
      "Si la personne n'est pas autorisée, engager une patrouille (identité, retrait accréd, expulsion).",
      "Faire resécuriser l'accès par Tango (cadenas, Héras).",
      "Consigner l'ensemble à la vidéo."
    ],
    "consigner": "Identité, plaques, accréditation retirée (help desk), références et horaires caméra.",
    "pieges": "Doublons de fiche fréquents. Mission parfois à reprendre à la vacation suivante.",
    "souscas": [
      "Intrusion en zone piste",
      "Intrusion paddock / zone réglementée",
      "Accès forcé (portail, grillage)",
      "Poste d'accès non tenu (agent absent)"
    ],
    "details": [
      "Lève le doute à la caméra AVANT d'engager : note le n° de caméra et l'horodatage, tout reste consigné à la vidéo.",
      "Personne non autorisée : patrouille pour relevé d'identité + photo, retrait de l'accréditation (déposée au help desk), expulsion du site.",
      "Poste non tenu (agent absent) : vérifie à la caméra, envoie une patrouille le retrouver ; note s'il refuse de donner nom et société.",
      "Accès forcé / grillage ouvert : Tango resécurise (cadenas, chaîne, Héras). Si c'est récurrent (déjà colmaté la veille), remonte pour une solution de nuit (renfort, S3M).",
      "Vérifie les doublons (cf. fiche n°). Certaines missions se reprennent à la vacation suivante : passe clairement la consigne."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Signalement d'intrusion"
      },
      {
        "k": "act",
        "t": "Qualifier + localiser"
      },
      {
        "k": "watch",
        "t": "Lever le doute vidéo"
      },
      {
        "k": "ask",
        "t": "Personne autorisée ?",
        "y": "Régulariser / laisser",
        "n": "Patrouille : identité, expulsion"
      },
      {
        "k": "engage",
        "t": "Tango : resécuriser l'accès"
      },
      {
        "k": "end",
        "t": "Clôture + consigner la vidéo"
      }
    ]
  },
  {
    "code": "P04",
    "titre": "Acte de malveillance sur le dispositif",
    "dom": "securite",
    "situation": "Grillage ou barrière forcé, faille de sécurisation, dégradation volontaire du dispositif. L'enjeu est de colmater vite et de surveiller la reprise.",
    "questions": [
      "Quelle est la nature de la faille (grillage, barrière, portail) ?",
      "Où exactement, sur quel périmètre ?",
      "Des personnes sont-elles en train de l'exploiter ?",
      "La zone est-elle sensible (piste, paddock) ?"
    ],
    "acteurs": "Levée de doute vidéo, patrouille et S3M, Tango pour la sécurisation matérielle, Police si nécessaire.",
    "conduite": [
      "Lever le doute vidéo pour situer et qualifier la faille.",
      "Engager une patrouille pour constat.",
      "Faire sécuriser la faille par Tango (Héras, cadenas, colliers).",
      "Mettre en place des patrouilles régulières et confirmer la remise en état."
    ],
    "consigner": "Nature de la faille, mesures de sécurisation, patrouilles engagées, confirmation.",
    "pieges": "Doublons fréquents. Vérifier que la faille est réellement colmatée avant clôture.",
    "souscas": [
      "Barrière ou grillage forcé",
      "Dégradation volontaire du dispositif",
      "Objet ou comportement suspect signalé"
    ],
    "details": [
      "Lève le doute vidéo, puis patrouille + S3M pour constat ; Tango sécurise la faille (serflex, Héras, cadenas).",
      "Mets en place des patrouilles régulières S3M sur la zone tant que ce n'est pas consolidé.",
      "Récurrent : une faille colmatée peut ré-ouvrir. Si ça se répète, remonte à la coordination pour une solution de nuit.",
      "Doublon fréquent : vérifie qu'une fiche n'existe pas déjà (cf. fiche n°), joins une photo.",
      "Situation tendue (campement, hippodrome) : un coordo (AENEAS) va faire le point sur place ; applique la consigne de la coordination."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Faille signalée"
      },
      {
        "k": "watch",
        "t": "Lever le doute vidéo"
      },
      {
        "k": "engage",
        "t": "Patrouille + S3M : constat"
      },
      {
        "k": "engage",
        "t": "Tango : sécuriser (Héras, cadenas)"
      },
      {
        "k": "act",
        "t": "Patrouilles régulières de surveillance"
      },
      {
        "k": "end",
        "t": "Clôture sur colmatage confirmé"
      }
    ]
  },
  {
    "code": "P05",
    "titre": "Agression",
    "dom": "securite",
    "situation": "Agression d'une personne, souvent un personnel d'accueil ACO. Cas sensible : la traçabilité doit être irréprochable pour la suite judiciaire.",
    "questions": [
      "Y a-t-il un blessé, et dans quel état ?",
      "Qui est la victime (personnel ACO, client) ?",
      "L'agresseur est-il identifié, encore sur place ?",
      "La victime souhaite-t-elle porter plainte ?"
    ],
    "acteurs": "Patrouille de sécurité (priorité), Police, CMS si blessé, PCA.",
    "conduite": [
      "Engager une patrouille immédiatement, secours si blessé.",
      "Recueillir la victime au PC et l'accompagner au dépôt de plainte.",
      "Identifier l'agresseur, retirer accréditation ou annuler le billet.",
      "Articuler avec les forces de l'ordre."
    ],
    "consigner": "Identités victime et agresseur, récit, billet annulé, suite judiciaire, liens entre fiches.",
    "pieges": "Cas sensible : traçabilité irréprochable. Vérifier les liens avec d'autres fiches (même individu).",
    "souscas": [
      "Agression d'un personnel d'accueil ACO",
      "Agression entre clients",
      "Comportement à caractère sexuel"
    ],
    "details": [
      "Blessé ? Engage le CMS / Reflex sans délai (ex. agent percuté par une moto → ambulance + attelle).",
      "Recueille la victime au PC, note son identité et son récit ; propose le dépôt de plainte (en ligne ou commissariat du Mans).",
      "Identifie l'agresseur à la caméra (n° + timecode), fais relever l'identité par la patrouille, annule le billet (note l'heure) ou retire l'accréditation, raccompagne à l'extérieur.",
      "Articule avec la Police : elle demande souvent si la victime veut porter plainte — aie ses coordonnées prêtes.",
      "Vérifie les liens entre fiches (même individu, affaire connexe) et joins les pièces (photos, billet annulé)."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Agression signalée"
      },
      {
        "k": "act",
        "t": "Qualifier"
      },
      {
        "k": "ask",
        "t": "Blessé ?",
        "y": "CMS",
        "n": "poursuivre"
      },
      {
        "k": "engage",
        "t": "Patrouille + recueillir la victime"
      },
      {
        "k": "act",
        "t": "Plainte, identifier l'agresseur, retrait du billet"
      },
      {
        "k": "end",
        "t": "Clôture, traçabilité complète"
      }
    ]
  },
  {
    "code": "P06",
    "titre": "Altercation ou rixe",
    "dom": "securite",
    "situation": "Différend ou bagarre entre personnes, parfois de grande ampleur. Anticiper les renforts et signaler toute arme sans délai.",
    "questions": [
      "Combien de personnes sont impliquées ?",
      "Y a-t-il une arme ?",
      "Y a-t-il des blessés ?",
      "Où exactement, et la situation s'aggrave-t-elle ?"
    ],
    "acteurs": "Patrouille et renforts (coordo AENEAS), S3M, Police si présence d'arme.",
    "conduite": [
      "Engager une patrouille, renforts proportionnés à l'ampleur.",
      "Faire réaliser un point de situation par un coordinateur.",
      "Apaiser, puis maintenir une présence préventive.",
      "Signaler sans délai toute présence d'arme."
    ],
    "consigner": "Nombre de personnes, moyens engagés, présence d'arme, évolution, clôture.",
    "pieges": "Une altercation peut dégénérer : anticiper les renforts. Toujours signaler une arme.",
    "souscas": [
      "Altercation verbale",
      "Rixe / bagarre de groupe",
      "Présence d'arme signalée"
    ],
    "details": [
      "Engage une patrouille, renforts proportionnés (P2 + P3, appui S3M) ; pour une bagarre de groupe, fais monter un coordo (AENEAS) pour le point de situation.",
      "Signale immédiatement toute arme (une machette a déjà été signalée en trucker camp) : c'est prioritaire.",
      "Coup porté à un agent : appelle la Police pour constatation, note le lieu précis (passerelle, allée).",
      "Après apaisement, maintiens une présence préventive (passages de nuit).",
      "Souvent aucune des parties ne dépose plainte : note-le. Billet ou accréditation retirés si nécessaire."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Altercation signalée"
      },
      {
        "k": "act",
        "t": "Qualifier l'ampleur"
      },
      {
        "k": "ask",
        "t": "Arme signalée ?",
        "y": "Police + signaler",
        "n": "poursuivre"
      },
      {
        "k": "engage",
        "t": "Patrouille (+ renforts, coordo)"
      },
      {
        "k": "act",
        "t": "Point de situation, apaiser"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P07",
    "titre": "Vol",
    "dom": "securite",
    "situation": "Vol d'un bien ou d'un véhicule de service (golfette), vol en boutique. Toujours croiser le fichier fourrière avant de conclure au vol.",
    "questions": [
      "Qu'est-ce qui a été volé (description, numéro, plaque) ?",
      "Où et quand ?",
      "L'auteur est-il connu ou visible à la caméra ?",
      "A-t-on croisé le fichier fourrière (golfette, véhicule) ?"
    ],
    "acteurs": "Levée de doute vidéo, patrouille, diffusion à toutes les patrouilles, Police, help desk.",
    "conduite": [
      "Lever le doute vidéo pour tenter d'identifier l'objet ou l'auteur.",
      "Aviser l'ensemble des patrouilles et bloquer l'objet aux sorties.",
      "Conseiller le dépôt de plainte, croiser avec le fichier fourrière.",
      "Suivre et informer le propriétaire en cas de retrouvaille."
    ],
    "consigner": "Description de l'objet, plaque ou numéro, heure et lieu, dépôt de plainte, croisement fourrière.",
    "pieges": "Doublons fréquents pour un même objet. Toujours croiser le fichier fourrière avant de conclure au vol.",
    "souscas": [
      "Vol de véhicule de service (golfette)",
      "Vol en boutique",
      "Vol d'effets personnels"
    ],
    "details": [
      "Lève le doute vidéo : cherche l'objet ou l'auteur à la caméra (n° + heure), diffuse le signalement à toutes les patrouilles et fais bloquer aux sorties.",
      "Golfette ou véhicule de service : croise TOUJOURS le fichier fourrière avant de conclure au vol (souvent juste enlevé). Note le n° et l'emplacement.",
      "Boutique : l'agent de sécurité retient l'individu ; relève l'identité même en cas de refus de billet, raccompagne en sortie, fais vérifier l'achat.",
      "Conseille le dépôt de plainte et note le téléphone du plaignant pour le rappeler en cas de retrouvaille.",
      "Doublon fréquent pour un même objet (cf. fiche n°). Un objet est parfois retrouvé là où il a disparu : reclos proprement."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Vol signalé"
      },
      {
        "k": "act",
        "t": "Qualifier + décrire"
      },
      {
        "k": "watch",
        "t": "Lever le doute vidéo"
      },
      {
        "k": "engage",
        "t": "Aviser les patrouilles, bloquer les sorties"
      },
      {
        "k": "ask",
        "t": "Objet retrouvé ?",
        "y": "Informer le propriétaire",
        "n": "Plainte + croiser fourrière"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P08",
    "titre": "Dégradation",
    "dom": "securite",
    "situation": "Bien endommagé (portail heurté, mobilier cassé, tags). Distinguer l'accidentel du volontaire oriente toute la suite.",
    "questions": [
      "Qu'est-ce qui est endommagé, et où ?",
      "Accident ou acte volontaire ?",
      "Le responsable est-il identifié (plaque, vidéo) ?",
      "Y a-t-il un risque immédiat (sécurité, stabilité) ?"
    ],
    "acteurs": "Levée de doute vidéo, patrouille pour constat, Service technique, Police si acte pénal.",
    "conduite": [
      "Lever le doute vidéo (plaque, circonstances).",
      "Engager une patrouille pour constat et identité du responsable.",
      "Engager le service technique pour la remise en état.",
      "Informer l'ACO par mail, joindre les photos."
    ],
    "consigner": "Auteur, plaque, description, photos, référence vidéo, suite (constat, plainte).",
    "pieges": "Bien distinguer l'accidentel du volontaire. Un tag à caractère pénal relève des forces de l'ordre.",
    "souscas": [
      "Véhicule ayant heurté un ouvrage",
      "Mobilier ou matériel cassé",
      "Tags ou inscriptions"
    ],
    "details": [
      "Lève le doute vidéo pour la plaque et les circonstances (accidentel ou volontaire).",
      "Patrouille pour constat + identité du responsable ; service technique pour évaluer et remettre en état.",
      "Informe l'ACO par mail avec les photos en pièce jointe ; ajoute aussi les photos à la fiche.",
      "Acte volontaire ou à caractère pénal (tags, croix gammées) : contacte la Police, relève l'identité, l'ACO envisage un dépôt de plainte.",
      "Danger structurel (palissade, plot de grillage) : fais sécuriser d'abord (plots, parpaings, conteneur) avant réparation."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Bien endommagé"
      },
      {
        "k": "watch",
        "t": "Lever le doute vidéo (plaque)"
      },
      {
        "k": "ask",
        "t": "Volontaire / pénal ?",
        "y": "Police",
        "n": "poursuivre"
      },
      {
        "k": "engage",
        "t": "Patrouille : constat + identité"
      },
      {
        "k": "engage",
        "t": "Technique : remise en état"
      },
      {
        "k": "end",
        "t": "Mail ACO + photos, clôture"
      }
    ]
  },
  {
    "code": "P09",
    "titre": "Non-respect du règlement",
    "dom": "securite",
    "situation": "Véhicule sans titre, échappement non homologué, refus d'obtempérer. Le sans-titre bascule vite en fourrière, le refus en Police.",
    "questions": [
      "Quelle est l'infraction exacte (sans titre, échappement, refus) ?",
      "Plaque et localisation ?",
      "La personne coopère-t-elle ?",
      "Faut-il une saisie, une fourrière, ou la Police ?"
    ],
    "acteurs": "Levée de doute vidéo, patrouille et appui mobile, fourrière, Police si refus.",
    "conduite": [
      "Lever le doute vidéo, puis faire identifier par une patrouille ou l'appui mobile.",
      "Procéder à la saisie si l'équipement est non conforme.",
      "Mettre en fourrière si le véhicule est sans titre, gérer la relation propriétaire.",
      "Engager la Police en cas de refus d'obtempérer."
    ],
    "consigner": "Plaques, nature de l'infraction, saisie, mise en fourrière, intervention Police.",
    "pieges": "Le sans-titre bascule vite en fourrière. Les propriétaires sont souvent virulents.",
    "souscas": [
      "Véhicule sans titre",
      "Échappement ou véhicule non homologué",
      "Refus d'obtempérer",
      "Stationnement sur emplacement réservé"
    ],
    "details": [
      "Lève le doute vidéo, puis patrouille ou appui mobile pour identifier (note plaque + marque + couleur).",
      "Sans titre : bascule en fourrière (voir P19). Validation d'enlèvement souvent par la coordination ; le prestataire 3J stocke ; restitution contre paiement et facture.",
      "Échappement ou véhicule non homologué : patrouille + renfort pour la saisie (parfois une meuleuse est nécessaire) ; note « vigilance retour » pour le lendemain.",
      "Refus d'obtempérer / individus virulents : visuel caméra, préviens Reflex, engage la Police pour identification ; note le timecode caméra.",
      "Beaucoup de ces fiches se clôturent le lendemain : passe une consigne claire à la vacation suivante."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Infraction signalée"
      },
      {
        "k": "watch",
        "t": "Lever le doute vidéo"
      },
      {
        "k": "engage",
        "t": "Patrouille / appui mobile : identifier"
      },
      {
        "k": "ask",
        "t": "Sans titre ?",
        "y": "Fourrière",
        "n": "Saisie si non conforme"
      },
      {
        "k": "ask",
        "t": "Refus d'obtempérer ?",
        "y": "Police",
        "n": "poursuivre"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P10",
    "titre": "Fraude accréditation ou billet",
    "dom": "securite",
    "situation": "Revente de billet, fausse identité, titre scanné en double usage. L'individu quitte souvent les lieux à l'arrivée de la patrouille : agir vite.",
    "questions": [
      "Quel type de fraude (revente, fausse identité, double scan) ?",
      "L'individu est-il encore sur place ?",
      "Quel titre ou billet est concerné ?",
      "Le SERI doit-il être informé (double usage) ?"
    ],
    "acteurs": "Patrouille de sécurité, PCA, SERI pour le double usage.",
    "conduite": [
      "Engager une patrouille pour contrôle et prise de contact.",
      "Raccompagner vers une sortie si la fraude est avérée.",
      "Signaler un double usage de titre au SERI.",
      "Consigner l'identité et la suite."
    ],
    "consigner": "Identité, type de fraude, titre concerné, raccompagnement, signalement SERI.",
    "pieges": "L'individu quitte souvent les lieux à l'arrivée de la patrouille. Agir vite.",
    "souscas": [
      "Revente de billet",
      "Fausse identité / faux personnel",
      "Titre en double usage"
    ],
    "details": [
      "Patrouille pour contrôle et prise de contact ; l'individu quitte souvent les lieux à l'arrivée — agir vite (OPV en recherche à la caméra).",
      "Fraude avérée : raccompagne en sortie, retire ou annule le titre / l'accréditation, note l'identité.",
      "Forçage d'accès (bousculade du personnel) : renfort de patrouille ; refus de remise du titre → Police (délit d'intrusion en enceinte sportive).",
      "Double usage de titre (scan avant l'entrée) : signale au SERI et remonte à la coordination / PCA pour traitement.",
      "Badge d'un tiers utilisé : identifie le titulaire réel et retire les droits d'accès."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Fraude signalée"
      },
      {
        "k": "act",
        "t": "Qualifier"
      },
      {
        "k": "engage",
        "t": "Patrouille : contrôle + contact"
      },
      {
        "k": "ask",
        "t": "Fraude avérée ?",
        "y": "Raccompagner, billet retiré",
        "n": "poursuivre"
      },
      {
        "k": "act",
        "t": "Signaler le double usage au SERI"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P11",
    "titre": "Renfort de filtrage",
    "dom": "securite",
    "situation": "Congestion à une porte, besoin de renfort à la fouille. Penser à retirer les renforts dès le retour à la fluidité pour ne pas immobiliser des moyens.",
    "questions": [
      "Quelle porte, quel niveau d'affluence ?",
      "Le blocage vient-il de la fouille, du scan, du flux ?",
      "Combien de renfort faut-il ?",
      "Un cheminement alternatif est-il possible ?"
    ],
    "acteurs": "Patrouille de sécurité en renfort, appui flux, PCA, SERI si problème de PDA.",
    "conduite": [
      "Envoyer des patrouilles en renfort à la porte concernée.",
      "Réguler le flux piéton vers les passerelles et cheminements alternatifs.",
      "Ajuster en continu, retirer les renforts dès que l'accès redevient fluide.",
      "Coordonner en permanence avec le responsable de la porte."
    ],
    "consigner": "Porte concernée, renforts engagés et retirés, évolution de l'affluence.",
    "pieges": "Durée longue : retirer les renforts au bon moment pour ne pas immobiliser des moyens.",
    "souscas": [
      "Congestion à une porte piétonne",
      "Problème de PDA ou de scan à la fouille",
      "Renfort de nuit"
    ],
    "details": [
      "Envoie patrouilles ou appui flux en renfort à la porte (P1, P2, P3, P5) et coordonne avec le responsable de porte.",
      "Oriente le flux piéton vers les passerelles et cheminements alternatifs (ex. passerelle Panorama).",
      "Problème de PDA ou de scan à la fouille : appelle le SERI en parallèle (voir P14).",
      "Litiges billets : oriente vers le point info litiges ; sur autorisation PCA, certains cas peuvent être laissés passer.",
      "Dès le retour à la fluidité, lève le dispositif étape par étape (« levée du dispositif P3 » puis P2) pour ne pas immobiliser des moyens."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Congestion à une porte"
      },
      {
        "k": "act",
        "t": "Qualifier l'affluence"
      },
      {
        "k": "engage",
        "t": "Patrouilles en renfort"
      },
      {
        "k": "act",
        "t": "Réguler le flux vers les passerelles"
      },
      {
        "k": "ask",
        "t": "Redevenu fluide ?",
        "y": "Retirer les renforts",
        "n": "Maintenir"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P12",
    "titre": "Panne électrique",
    "dom": "technique",
    "situation": "Coupure ou disjonction (bungalow, borne, éclairage). La localisation précise est décisive : sans elle, le SERI ne trouve pas.",
    "questions": [
      "Quel équipement, quelle localisation précise (carroyage) ?",
      "Coupure totale ou partielle ?",
      "Le local est-il bien répertorié comme alimenté ?",
      "Y a-t-il un impact sur un poste sensible (contrôle d'accès) ?"
    ],
    "acteurs": "Service Élec., SERI, Service technique.",
    "conduite": [
      "Localiser précisément (zone, carroyage, repère).",
      "Engager le service électrique ou le SERI.",
      "Relancer régulièrement tant que la panne persiste.",
      "Transmettre à l'équipe de nuit si non résolu, confirmer le rétablissement."
    ],
    "consigner": "Localisation exacte, service engagé, relances, confirmation de rétablissement.",
    "pieges": "Localisation imprécise : le SERI ne trouve pas. Certains bungalows ne sont pas répertoriés comme alimentés.",
    "souscas": [
      "Coupure sur un bungalow ou un local",
      "Borne ou branchement hors service",
      "Éclairage défaillant"
    ],
    "details": [
      "Localise précisément (carroyage type AK15, n° de borne ou de bungalow) : sans ça, Élec / SERI ne trouve pas. Certains locaux ne sont pas répertoriés comme alimentés.",
      "Engage Élec ou SERI ; distingue panne informatique et panne électrique (le SERI renvoie parfois vers l'Élec).",
      "Relance régulièrement et note chaque relance ; la nuit, passe par l'astreinte électrique et transmets à l'équipe de nuit si non résolu.",
      "Impact sur un poste sensible (contrôle d'accès, éclairage piste) : traite en priorité.",
      "Confirme le rétablissement auprès du demandeur (chef de zone) avant de clôturer."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Coupure signalée"
      },
      {
        "k": "act",
        "t": "Localiser précisément (carroyage)"
      },
      {
        "k": "engage",
        "t": "Service Élec. / SERI"
      },
      {
        "k": "ask",
        "t": "Résolu ?",
        "y": "Confirmer au demandeur",
        "n": "Relancer / équipe de nuit"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P13",
    "titre": "Panne sanitaire ou fuite d'eau",
    "dom": "technique",
    "situation": "WC hors service, fuite, loquet cassé, odeur. La discipline de relance et de clôture y est décisive : les interventions traînent souvent.",
    "questions": [
      "Quelle est la référence exacte du sanitaire ?",
      "Nature du problème (WC HS, fuite, loquet, odeur) ?",
      "Où précisément ?",
      "Faut-il un prestataire externe ?"
    ],
    "acteurs": "Service technique (systématique), SERI si électrique, prestataires (DRON, Passenaud).",
    "conduite": [
      "Recueillir la référence exacte du sanitaire.",
      "Contacter le service technique.",
      "Relancer, souvent plusieurs fois, jusqu'à intervention.",
      "Confirmer la réparation auprès du chef de zone."
    ],
    "consigner": "Référence du sanitaire, localisation, relances, prestataire, confirmation.",
    "pieges": "Délais très longs. Relances multiples nécessaires. Souvent un prestataire externe intervient.",
    "souscas": [
      "WC hors service",
      "Fuite d'eau",
      "Serrure ou loquet cassé",
      "Point d'eau défaillant"
    ],
    "details": [
      "Recueille la référence exacte du sanitaire (n° WC, réf. type « 12681 SANIDUO », prestataire DRON) : le technique ne se déplace pas sans le numéro précis.",
      "Contacte le service technique ; les prestataires DRON ou Passenaud interviennent souvent.",
      "C'est le poste où l'on relance le plus : note chaque relance et re-contacte le demandeur (chef de zone) pour faire le point.",
      "Fuite à fort débit ou risque : fais couper ou bloquer d'abord (patrouille), puis intervention technique.",
      "Confirme la réparation auprès du chef de zone avant clôture ; passe la consigne si ça déborde sur la nuit."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Panne sanitaire"
      },
      {
        "k": "act",
        "t": "Recueillir la référence du sanitaire"
      },
      {
        "k": "engage",
        "t": "Service technique"
      },
      {
        "k": "ask",
        "t": "Intervenu ?",
        "y": "Confirmer au chef de zone",
        "n": "Relancer (+ prestataire)"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P14",
    "titre": "Panne de contrôle d'accès (tripode, scanner, informatique)",
    "dom": "technique",
    "situation": "Tripode en défaut, scanner ou PDA lent, message d'accès non autorisé. Souvent réglé par un redémarrage à distance du SERI, avec passage en sortie manuelle en attendant.",
    "questions": [
      "Quel matériel, quel numéro (tripode, scan) ?",
      "Quelle porte ?",
      "Panne totale ou intermittente ?",
      "Un redémarrage à distance est-il possible ?"
    ],
    "acteurs": "SERI (systématique), Service Élec.",
    "conduite": [
      "Identifier le matériel (numéro de tripode ou de scan).",
      "Contacter le SERI ; il peut souvent redémarrer à distance.",
      "Faire basculer la porte en mode sortie manuelle en attendant.",
      "Si besoin, faire venir un technicien, puis confirmer le rétablissement."
    ],
    "consigner": "Numéro du matériel, porte, action SERI, solution de contournement, confirmation.",
    "pieges": "Demande parfois non prise en compte par le SERI : vérifier. Une pièce à livrer peut allonger le délai.",
    "souscas": [
      "Tripode en défaut",
      "Scanner ou PDA lent",
      "Message « accès non autorisé »",
      "Manque de consommables (stickers)"
    ],
    "details": [
      "Identifie le matériel par son numéro (tripode 2.11 ou A24, scan 70-69) et la porte concernée.",
      "Contacte le SERI : il redémarre souvent à distance. Si ça ne tient pas, fais passer la porte en mode sortie manuelle par le responsable de zone en attendant le technicien.",
      "Maintenance sous contrat SKI DATA : le SERI ou le technique peut faire appel à eux.",
      "Pannes récurrentes (fonctionne une heure puis s'arrête) : sur consigne PCA, un tripode peut être condamné jusqu'à la fin du week-end.",
      "Vérifie qu'une fiche n'existe pas déjà pour le même matériel (doublon) ; confirme le rétablissement au chef de poste."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Matériel en défaut"
      },
      {
        "k": "act",
        "t": "Identifier (n° tripode / scan)"
      },
      {
        "k": "engage",
        "t": "SERI"
      },
      {
        "k": "ask",
        "t": "Redémarrage distant OK ?",
        "y": "Confirmer",
        "n": "Mode sortie manuelle + technicien"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P15",
    "titre": "Logistique et matériel",
    "dom": "technique",
    "situation": "Besoin ou incident matériel (trou dans un grillage, plaque d'égout, déplacement de matériel). Catégorie fourre-tout : bien choisir entre Tango et technique.",
    "questions": [
      "Quel est le besoin ou l'incident exact ?",
      "Où précisément ?",
      "Est-ce une réparation ou un déplacement de matériel ?",
      "Y a-t-il un danger immédiat (plaque, branche) ?"
    ],
    "acteurs": "Service technique (systématique), Tango, Logistique.",
    "conduite": [
      "Qualifier le besoin et le localiser.",
      "Confier à Tango (déplacement, pose, sécurisation) ou au technique (réparation).",
      "Joindre des photos, relancer.",
      "Confirmer la réalisation."
    ],
    "consigner": "Nature du besoin, service ou équipe engagé, photos, relances, confirmation.",
    "pieges": "Catégorie fourre-tout : bien choisir entre Tango et technique. Certains arbitrages remontent à la hiérarchie.",
    "souscas": [
      "Trou dans un grillage",
      "Danger sur cheminement (plaque, branche)",
      "Déplacement ou pose de matériel"
    ],
    "details": [
      "Qualifie et localise (carroyage) ; joins des photos (souvent reçues par WhatsApp de la patrouille).",
      "Choisis le bon acteur : Tango (déplacement, pose, sécurisation) ou service technique (réparation) — parfois les deux.",
      "Danger immédiat (plaque d'égout soulevée, trou, palissade instable) : fais sécuriser d'abord (Tango, plots, conteneur), répare ensuite.",
      "Relance et note ; envoie un mail au technique / staff avec photos pour les interventions différées.",
      "Certaines décisions remontent (ouvrir une palissade, réparer) : demande l'accord (Direction de Course / responsable) et note-le."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Besoin / incident matériel"
      },
      {
        "k": "act",
        "t": "Qualifier + localiser"
      },
      {
        "k": "ask",
        "t": "Réparation ou déplacement ?",
        "y": "Tango (pose / déplacement)",
        "n": "Technique (réparation)"
      },
      {
        "k": "act",
        "t": "Photos, relance"
      },
      {
        "k": "end",
        "t": "Confirmer, clôture"
      }
    ]
  },
  {
    "code": "P16",
    "titre": "Barriérage et balisage",
    "dom": "technique",
    "situation": "Pose ou retrait de plots béton, Héras, barrières ; ouverture ou fermeture d'accès. À synchroniser avec les horaires d'ouverture et de fermeture.",
    "questions": [
      "Que faut-il poser ou retirer, et où ?",
      "Est-ce lié à une ouverture ou fermeture d'accès ?",
      "À quel horaire l'accès doit-il changer ?",
      "Faut-il un cadenas ou une sécurisation ?"
    ],
    "acteurs": "Tango (systématique), Logistique, PS pour cadenas.",
    "conduite": [
      "Formuler la demande à Tango (pose ou retrait).",
      "Coordonner avec l'ouverture ou la fermeture de l'accès concerné.",
      "Faire poser un cadenas par PS si nécessaire.",
      "Confirmer la réalisation."
    ],
    "consigner": "Type de balisage, lieu, équipe Tango, coordination d'accès, confirmation.",
    "pieges": "Bien synchroniser avec les horaires d'ouverture et de fermeture de l'accès.",
    "souscas": [
      "Pose de plots béton ou Héras",
      "Retrait de balisage",
      "Ouverture ou fermeture d'un accès"
    ],
    "details": [
      "Demande à Tango la pose ou le retrait (plots béton, Héras, Vauban) ; précise le lieu et la quantité.",
      "Synchronise avec l'ouverture / fermeture de l'accès (une porte activée seulement le week-end).",
      "Fais poser cadenas + chaîne par PS sur les Héras si l'accès doit rester fermé.",
      "Météo : vent et averses font tomber les Vauban et les Héras — prévois un renforcement (plots, jambes de force) et re-contrôle après.",
      "Confirme la réalisation ; note si Tango est « pris sur traçage » et doit passer plus tard."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Demande de balisage"
      },
      {
        "k": "act",
        "t": "Qualifier + localiser"
      },
      {
        "k": "engage",
        "t": "Tango : pose / retrait"
      },
      {
        "k": "act",
        "t": "Coordonner ouverture / fermeture d'accès"
      },
      {
        "k": "engage",
        "t": "PS : cadenas si besoin"
      },
      {
        "k": "end",
        "t": "Confirmer, clôture"
      }
    ]
  },
  {
    "code": "P17",
    "titre": "Signalétique",
    "dom": "technique",
    "situation": "Panneau ou affichage à poser, reposer ou remplacer (objets interdits, direction, pancarte arrachée). Ces missions passent après les urgents : relancer sans les oublier.",
    "questions": [
      "Quel panneau, quel emplacement ?",
      "Pose, repose ou remplacement ?",
      "Est-ce un affichage réglementaire (objets interdits) ?",
      "Le matériel est-il disponible ?"
    ],
    "acteurs": "Tango (systématique), Service ACO ou technique.",
    "conduite": [
      "Identifier le panneau et son emplacement.",
      "Confier la pose ou la repose à Tango.",
      "Relancer si l'affichage n'est pas remis.",
      "Confirmer la réalisation."
    ],
    "consigner": "Panneau concerné, emplacement, équipe, relances, confirmation.",
    "pieges": "Ces missions passent après les urgents : relancer sans les oublier.",
    "souscas": [
      "Panneau d'objets interdits à poser",
      "Signalétique directionnelle",
      "Affichage arraché à remettre"
    ],
    "details": [
      "Identifie le panneau et son emplacement précis (carroyage), et qui a demandé (resp. tribune, club ACO, Teranga).",
      "Confie à Tango la pose ou la repose ; c'est souvent basse priorité (« les urgents d'abord ») : il faut relancer.",
      "Affichage réglementaire (objets interdits aux portes) : vérifie que toutes les portes concernées sont couvertes.",
      "Signalétique tombée sur une voie et qui gêne la circulation : traite plus vite, c'est un risque.",
      "Note chaque relance ; confirme la pose effective (parfois faite en fin de mission en cours de Tango)."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Panneau à traiter"
      },
      {
        "k": "act",
        "t": "Identifier le panneau + l'emplacement"
      },
      {
        "k": "engage",
        "t": "Tango : pose / repose"
      },
      {
        "k": "ask",
        "t": "Fait ?",
        "y": "Confirmer",
        "n": "Relancer"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P18",
    "titre": "Circulation et congestion",
    "dom": "flux",
    "situation": "Congestion de véhicules ou de piétons, besoin de fluidifier un accès. Rester vigilant même quand le responsable dit ne pas avoir besoin : la situation évolue vite.",
    "questions": [
      "Où est le point de blocage ?",
      "Véhicules ou piétons ?",
      "Un véhicule ou un obstacle bloque-t-il ?",
      "L'affluence est-elle montante ou descendante ?"
    ],
    "acteurs": "Responsable de secteur et Service ACO, patrouille et appui flux, fourrière si véhicule bloquant.",
    "conduite": [
      "Évaluer le point de blocage.",
      "Engager l'appui flux ou une patrouille pour réguler.",
      "Envoyer un renfort à la porte, fourrière si un véhicule bloque.",
      "Ajuster et lever le dispositif dès le retour à la normale."
    ],
    "consigner": "Point de blocage, moyens engagés, renforts, retour à la fluidité.",
    "pieges": "Rester vigilant même quand le responsable dit ne pas avoir besoin : la situation évolue vite.",
    "souscas": [
      "Congestion véhicules (parking, souterrain)",
      "Congestion piétons (porte, passerelle)",
      "Engin ou obstacle sur voie"
    ],
    "details": [
      "Évalue le point de blocage ; engage appui flux ou patrouille pour réguler ; envoie un renfort à la porte si besoin.",
      "Véhicule qui bloque : lève le doute (personne à bord ?), identifie la plaque, missionne l'appui flux pour vérifier, fourrière si nécessaire (voir P19).",
      "Reste vigilant même si le responsable dit « pas besoin » : rappelle plus tard, l'affluence évolue vite.",
      "Engins ou chariots abandonnés (clé sur le contact) : Tango récupère ; note l'identité du conducteur potentiel et les références caméra.",
      "Dès le retour à la normale, lève le dispositif ; note l'heure de fin."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Congestion signalée"
      },
      {
        "k": "act",
        "t": "Évaluer le point de blocage"
      },
      {
        "k": "engage",
        "t": "Appui flux / patrouille : réguler"
      },
      {
        "k": "ask",
        "t": "Véhicule bloquant ?",
        "y": "Fourrière",
        "n": "poursuivre"
      },
      {
        "k": "act",
        "t": "Ajuster, lever au retour à la normale"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P19",
    "titre": "Stationnement gênant et fourrière",
    "dom": "flux",
    "situation": "Véhicule gênant, sans titre, ou à enlever. Sur la voie publique, c'est la Police et non la fourrière ACO ; le suivi fourrière se tient dans un fichier séparé.",
    "questions": [
      "Y a-t-il une personne dans le véhicule ?",
      "Plaque et titre de stationnement ?",
      "Quel modèle et gabarit de véhicule ? (le dépanneur dimensionne son engin en conséquence)",
      "Est-on sur site ou sur voie publique ?",
      "Le véhicule est-il sans titre (fourrière) ?"
    ],
    "acteurs": "Appui flux et fourrière (prestataire d'enlèvement), patrouille, Police si voie publique.",
    "conduite": [
      "Lever le doute (présence d'une personne dans le véhicule ?).",
      "Identifier la plaque et le titre, faire vérifier par l'appui flux moto.",
      "Engager la fourrière si le véhicule est sans titre ; horodater enlèvement et dépôt.",
      "Gérer la relation propriétaire et la restitution contre paiement."
    ],
    "consigner": "Plaque, modèle et gabarit du véhicule, titre, vérification, enlèvement et dépôt horodatés, suivi fourrière, restitution.",
    "pieges": "Sur la voie publique, c'est la Police, pas la fourrière ACO. Le suivi fourrière se tient dans un fichier séparé.",
    "souscas": [
      "Véhicule gênant sur site (sans titre)",
      "Véhicule sur voie publique (Police)",
      "Restitution au propriétaire"
    ],
    "details": [
      "Lève le doute d'abord : personne à bord ? L'appui flux ou l'unité mobile va vérifier sur place avant de missionner la fourrière.",
      "Identifie la plaque ET le titre de stationnement (souvent le véhicule a un titre, mais pour un autre parking).",
      "Donne le modèle et le gabarit au dépanneur (3J) : il dimensionne son engin en conséquence — une berline, un van 9 places ou un poids lourd, ce n'est pas la même dépanneuse.",
      "Voie publique : c'est la Police (Nationale ou Municipale), pas la fourrière ACO — passe par la coordination. Sur site : fourrière 3J.",
      "Enlèvement 3J : validation souvent par la coordination ; horodate arrivée 3J, enlèvement et dépôt en fourrière ; priorise les emplacements PMR et les espaces circuit.",
      "Restitution contre paiement (avec facture) ; tiens le fichier « Suivi fourrière » à jour. Les propriétaires sont souvent virulents : reste factuel."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Véhicule gênant"
      },
      {
        "k": "act",
        "t": "Qualifier + localiser"
      },
      {
        "k": "watch",
        "t": "Lever le doute (personne à bord ?)"
      },
      {
        "k": "act",
        "t": "Identifier la plaque + le titre"
      },
      {
        "k": "ask",
        "t": "Voie publique ?",
        "y": "Police",
        "n": "Sans titre → fourrière"
      },
      {
        "k": "act",
        "t": "Restitution contre paiement + fichier"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P20",
    "titre": "Ouverture, fermeture et cadencement d'un accès",
    "dom": "acces",
    "situation": "Ouvrir, fermer ou cadencer un accès (porte, portail, portillon, sanitaire, lieu) selon la séquence du site. C'est le cœur du métier de conduite : le PC cadence le site en temps réel, sur ordre du PCA ou selon le planning.",
    "questions": [
      "Quel accès exactement (porte, portail, n°, caméra associée) ?",
      "Ouverture ou fermeture, et à partir de quand ?",
      "Est-ce sur ordre (PCA, Direction) ou selon le planning ?",
      "Qui exécute sur place (PS Nord, chef de zone, agent) ?",
      "Y a-t-il un impact flux à anticiper (renfort, escargot de fouille) ?"
    ],
    "acteurs": "PS Nord, chef de zone ou agent de contrôle pour l'exécution ; PCA et Direction pour l'ordre ; appui flux si impact.",
    "conduite": [
      "Confirme l'accès, le sens (ouvrir / fermer) et l'horaire ; note l'ordre (PCA, Direction) le cas échéant.",
      "Missionne l'exécutant sur place (PS Nord, chef de zone, agent) et fais confirmer l'exécution.",
      "Contrôle à la caméra (note le n°) l'état réel de l'accès.",
      "Anticipe l'impact flux (renfort, ouverture d'un passage) et lève le dispositif au bon moment."
    ],
    "consigner": "Accès concerné, sens et horaire, ordre reçu, exécutant, n° de caméra de contrôle, confirmation d'exécution.",
    "pieges": "Une porte peut se ré-ouvrir seule (vent) ou rester ouverte : contrôle à la caméra plutôt que de te fier au verbal. Passe la consigne d'ouverture ou de fermeture à la vacation suivante.",
    "souscas": [
      "Ouverture des portes (début d'épreuve)",
      "Fermeture / condamnation d'un accès",
      "Cadencement sur ordre PCA",
      "Ratissage et fermeture des aires d'accueil"
    ],
    "details": [
      "Le cadencement se fait souvent sur ordre du PCA (« ouverture portail 2, CAM 9 ») ou de la Direction : note l'ordre et l'exécutant.",
      "Contrôle systématiquement à la caméra l'état réel (ouvert / fermé) : le verbal ne suffit pas, une porte forcée par le vent se ré-ouvre.",
      "Ouverture des portes en début d'épreuve : vérifie qu'agents de contrôle, PDA et tripodes sont opérationnels avant de valider « porte opérationnelle ».",
      "Ratissage et fermeture des aires d'accueil (fin de nuit) : fais fermer les portails par PS Nord, vérifie zone par zone (campements, sanitaires) et note les portails contrôlés.",
      "Passe clairement la consigne d'ouverture ou de fermeture entre vacations : c'est une source fréquente de trous."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Ordre / horaire d'accès"
      },
      {
        "k": "act",
        "t": "Confirmer accès + sens + horaire"
      },
      {
        "k": "engage",
        "t": "PS Nord / chef de zone : exécuter"
      },
      {
        "k": "watch",
        "t": "Contrôler à la caméra (n°)"
      },
      {
        "k": "ask",
        "t": "Impact flux ?",
        "y": "Renfort appui flux",
        "n": "poursuivre"
      },
      {
        "k": "end",
        "t": "Confirmer, clôture"
      }
    ]
  },
  {
    "code": "P21",
    "titre": "Demande d'accès, d'autorisation ou de clé",
    "dom": "acces",
    "situation": "Un intervenant demande un accès, une autorisation de circuler ou de stationner, une remise de clé, ou signale un problème de clé ou de badge. Le PC arbitre, prévient les postes concernés et trace.",
    "questions": [
      "Qui demande, pour quel besoin, et où ?",
      "La personne ou le véhicule est-il accrédité (titre, badge) ?",
      "Qui doit valider (PCA, Direction, chef de zone) ?",
      "Quels postes prévenir (PS Nord, chef de poste, contrôle) ?",
      "Pour une clé : quel local, qui la détient, qui la récupère ?"
    ],
    "acteurs": "PCA / Direction pour la validation ; chef de poste, PS Nord, contrôle d'accès pour l'exécution ; OPV pour la surveillance.",
    "conduite": [
      "Qualifie la demande (accès, autorisation, clé) et vérifie l'accréditation.",
      "Fais valider par l'échelon compétent (PCA, Direction) si nécessaire.",
      "Préviens les postes concernés (chef de poste, PS Nord, contrôle) et fais surveiller à l'OPV si besoin.",
      "Trace l'autorisation (véhicule, plaque, durée) et clôture à la fin du besoin."
    ],
    "consigner": "Demandeur, objet, accréditation, validateur, postes prévenus, plaque et durée pour un véhicule, remise et retour de clé.",
    "pieges": "Une clé partie avec un agent en fin de poste bloque le local suivant : identifie qui l'a et fais-la récupérer. N'autorise pas un accès non accrédité sans validation.",
    "souscas": [
      "Autorisation d'accès ou de circulation",
      "Autorisation de stationnement temporaire (taxi, navette)",
      "Remise ou récupération de clé",
      "Problème de badge / accréditation"
    ],
    "details": [
      "Autorisation de stationnement (taxi, navette) : préviens le chef de poste concerné et PS Nord, fais surveiller à l'OPV, note la plaque et suis jusqu'au départ.",
      "Vérifie toujours l'accréditation avant d'ouvrir un accès ; sans titre valide, remonte pour validation (PCA / Direction).",
      "Clé : note quel local, qui la détient et qui la récupère ; une clé égarée bloque tout un local (ex. clé du bungalow contrôleurs partie avec les agents).",
      "Mise en place de dispositif (jalonnement, navettes) : note les consignes (quels véhicules autorisés, postes positionnés).",
      "Trace le validateur : en cas de contrôle ultérieur, on doit savoir qui a autorisé."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Demande reçue"
      },
      {
        "k": "act",
        "t": "Qualifier + vérifier l'accréditation"
      },
      {
        "k": "ask",
        "t": "Validation requise ?",
        "y": "PCA / Direction",
        "n": "poursuivre"
      },
      {
        "k": "engage",
        "t": "Prévenir les postes (chef de poste, PS Nord)"
      },
      {
        "k": "act",
        "t": "Tracer (plaque, durée, surveillance OPV)"
      },
      {
        "k": "end",
        "t": "Clôture en fin de besoin"
      }
    ]
  },
  {
    "code": "P22",
    "titre": "Escorte, convoi ou mouvement encadré",
    "dom": "acces",
    "situation": "Encadrer un mouvement sur le site : parade, convoi exceptionnel, bus, navette, véhicules officiels ou de police. Le PC cadence les accès, engage l'appui flux et bloque ou rouvre les points de passage.",
    "questions": [
      "Quel mouvement (parade, convoi, bus, navette), combien de véhicules ?",
      "Itinéraire : entrée, points de passage, sortie ?",
      "Horaire de départ et de retour ?",
      "Qui accompagne (appui flux, PS Nord, référent) ?",
      "Quels accès faut-il ouvrir ou bloquer, et à quel moment ?"
    ],
    "acteurs": "Appui flux (accompagnement), PS Nord (ouverture des palissades et portails), Direction de Course pour la piste, référent du mouvement.",
    "conduite": [
      "Recueille l'itinéraire, l'horaire et le nombre de véhicules ; identifie le référent.",
      "Engage l'appui flux pour l'accompagnement et préviens les postes de chaque point de passage.",
      "Fais ouvrir les accès au bon moment (PS Nord pour les palissades, accord Direction pour la piste), suis à la caméra.",
      "Referme derrière le convoi et lève le dispositif au retour."
    ],
    "consigner": "Nature et composition du mouvement, itinéraire, horaires de départ, de passage et de retour, accompagnants, accès ouverts et fermés.",
    "pieges": "Bloque bien la voie AVANT le passage (PM ou appui flux) et referme derrière. Un convoi exceptionnel peut rester bloqué (tunnel, gabarit) : anticipe l'itinéraire.",
    "souscas": [
      "Parade (motos, camions décorés)",
      "Convoi exceptionnel sur piste",
      "Bus ou navettes (commissaires, invités)",
      "Véhicules officiels ou police escortés"
    ],
    "details": [
      "Parade : bloque la voie (RD) avec la PM ou l'appui flux AVANT le départ, suis le passage point par point (tertre rouge, ligne droite) et lève le dispositif dès le passage.",
      "Convoi exceptionnel sur piste : PS Nord ouvre la palissade (Tertre Rouge), le camion est escorté, PS Nord referme derrière ; surveille les points de gabarit (tunnels).",
      "Bus ou navettes non accrédités : un référent les réceptionne et accompagne ; préviens les responsables de secteur traversés et l'appui mobile.",
      "Note les horaires précis (départ, passages, retour) : ces missions se coordonnent souvent à l'avance par mail.",
      "Prévois l'appui mobile assez tôt : il peut ne commencer qu'à une certaine heure — anticipe pour ne pas retarder le mouvement."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Mouvement à encadrer"
      },
      {
        "k": "act",
        "t": "Itinéraire, horaire, nb de véhicules"
      },
      {
        "k": "engage",
        "t": "Appui flux : accompagner + prévenir les postes"
      },
      {
        "k": "act",
        "t": "Ouvrir les accès au bon moment (PS Nord, Direction)"
      },
      {
        "k": "watch",
        "t": "Suivre à la caméra"
      },
      {
        "k": "end",
        "t": "Refermer, lever le dispositif au retour"
      }
    ]
  },
  {
    "code": "P23",
    "titre": "Incendie ou départ de feu",
    "dom": "secours",
    "situation": "Départ de feu, incendie (véhicule, moto, tente, poubelle), fumée ou brûlure. Rare mais critique : déclenchement des pompiers immédiat, périmètre et levée de doute.",
    "questions": [
      "Que brûle exactement, et où précisément (carroyage) ?",
      "Y a-t-il des personnes, ou une propagation possible (véhicules, tentes, arbres) ?",
      "Un visuel caméra est-il possible ?",
      "Y a-t-il un blessé ou une brûlure ?"
    ],
    "acteurs": "Pompiers (déclenchement immédiat), CMS si blessé, patrouille de sécurité pour le périmètre, Tango pour sécuriser après extinction.",
    "conduite": [
      "Déclenche les pompiers sans attendre ; horodate le déclenchement et l'arrivée.",
      "Engage une patrouille pour établir un périmètre et fais sécuriser les abords (véhicules, tentes).",
      "Lève le doute (caméra ou agents sur place) : c'est parfois un fumigène, ou déjà éteint.",
      "CMS si brûlure ou blessé ; après extinction, fais sécuriser la zone (Tango : rubalise, barrières) et gère la suite (enlèvement du véhicule)."
    ],
    "consigner": "Nature du feu, localisation exacte, horodatage (déclenchement, arrivée, extinction, départ pompiers), blessé éventuel, mesures de sécurisation.",
    "pieges": "Ne minimise jamais un « début d'incendie » : déclenche les pompiers d'emblée, quitte à lever le doute ensuite. Attention à la propagation (voitures, tentes, arbres proches).",
    "souscas": [
      "Véhicule ou moto en feu",
      "Feu de tente / poubelle / camping",
      "Fumée ou fumigène (levée de doute)",
      "Brûlure (secours)"
    ],
    "details": [
      "Déclenchement pompiers immédiat, même sur « début d'incendie » : note l'heure de déclenchement et d'arrivée (ex. 17h → arrivée 17h14).",
      "Fais établir un périmètre par une patrouille et sécuriser les abords : le personnel d'accueil éloigne les tentes et véhicules proches.",
      "Lève le doute : sans visu caméra, envoie une patrouille ; c'est parfois un fumigène (les pompiers font la levée de doute et repartent).",
      "Véhicule ou moto en feu : après extinction, déclenche l'enlèvement (fourrière / consigne) et gère la restitution au propriétaire.",
      "Vérifie les doublons (plusieurs signalements du même feu) et joins le carroyage précis."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Départ de feu signalé"
      },
      {
        "k": "act",
        "t": "Qualifier : quoi, où, propagation ?"
      },
      {
        "k": "engage",
        "t": "Déclencher les pompiers (horodater)"
      },
      {
        "k": "engage",
        "t": "Patrouille : périmètre + sécuriser les abords"
      },
      {
        "k": "watch",
        "t": "Lever le doute (caméra / agents)"
      },
      {
        "k": "ask",
        "t": "Blessé ?",
        "y": "CMS",
        "n": "poursuivre"
      },
      {
        "k": "end",
        "t": "Extinction, sécuriser (Tango), clôture"
      }
    ]
  },
  {
    "code": "P24",
    "titre": "Personne en état d'ivresse ou sous stupéfiants",
    "dom": "secours",
    "situation": "Personne alcoolisée ou sous stupéfiants : allongée, agitée, inconsciente, ou virulente. Entre secours et sécurité selon l'état et le comportement.",
    "questions": [
      "La personne est-elle consciente ? Allongée, agitée, inconsciente mais respire ?",
      "Est-elle seule ou en groupe ? Comportement virulent ?",
      "Où précisément (carroyage) ?",
      "Y a-t-il un risque (voiture, insultes, agressivité envers les agents) ?"
    ],
    "acteurs": "CMS (prise en charge médicale), patrouille de sécurité (sécurisation, groupe virulent), Police en cas de trouble.",
    "conduite": [
      "Qualifie l'état ; si inconsciente ou en malaise, engage le CMS sans délai (voir P01).",
      "Engage une patrouille pour sécuriser, surtout si groupe virulent ou comportement à risque.",
      "Le CMS garde souvent la personne en surveillance et fait raccompagner les accompagnants à leur parking.",
      "Si insultes, refus ou agressivité : relève l'identité, retire ou annule le billet, engage la Police si nécessaire."
    ],
    "consigner": "État, localisation, prise en charge CMS, comportement, billet ou accréditation retiré, identité si trouble, suite (Police).",
    "pieges": "Une « simple » ivresse peut cacher un vrai malaise (convulsions, inconscience) : ne sous-estime pas, engage le CMS. L'état peut évoluer très vite.",
    "souscas": [
      "Personne alcoolisée allongée / inconsciente",
      "Groupe alcoolisé virulent",
      "Ivresse au volant",
      "Sous stupéfiants"
    ],
    "details": [
      "Ne sous-estime jamais : un cas classé « ivresse » peut être un vrai malaise (spasmes, convulsions, inconscience). Dans le doute, engage le CMS et suis jusqu'à la prise en charge.",
      "Le CMS garde souvent la personne en surveillance et fait raccompagner les accompagnants à leur parking : note-le.",
      "Groupe alcoolisé virulent : envoie une patrouille (voire renfort S3M / AENEAS), relève les identités, photographie les billets, retire ou annule ceux des fauteurs.",
      "Comportement raciste ou insultant envers les agents : consigne précisément, conserve les preuves (photos des billets), remonte pour suite éventuelle.",
      "Ivresse au volant ou accélérations dangereuses : traite comme un risque immédiat, engage patrouille et éventuellement la Police."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Personne alcoolisée signalée"
      },
      {
        "k": "act",
        "t": "Qualifier l'état + le comportement"
      },
      {
        "k": "ask",
        "t": "Inconsciente / malaise ?",
        "y": "CMS sans délai",
        "n": "Patrouille : sécuriser"
      },
      {
        "k": "act",
        "t": "Surveillance CMS, raccompagner les accompagnants"
      },
      {
        "k": "ask",
        "t": "Trouble / virulence ?",
        "y": "Identité, billet retiré, Police",
        "n": "poursuivre"
      },
      {
        "k": "end",
        "t": "Clôture après prise en charge"
      }
    ]
  },
  {
    "code": "P25",
    "titre": "Accident de circulation sur site",
    "dom": "secours",
    "situation": "Accident sur le site : collision de véhicules, piéton ou agent renversé ou percuté par une moto ou une voiture. Souvent secours, constat et relevé d'identités.",
    "questions": [
      "Y a-t-il des blessés, combien, dans quel état ?",
      "Que s'est-il passé (véhicules impliqués, piéton) et où précisément ?",
      "Les véhicules gênent-ils la circulation ou la piste ?",
      "Un visuel caméra est-il possible (plaque du fautif) ?"
    ],
    "acteurs": "CMS / Reflex (blessés), patrouille de sécurité (constat, identités), Police pour constatation, Tango ou appui flux pour dégager, Direction si impact piste.",
    "conduite": [
      "Engage le CMS / Reflex pour les blessés (ambulance, attelle) ; horodate.",
      "Fais relever les identités et coordonnées (fautif et victime) par la patrouille ; lève le doute vidéo pour la plaque.",
      "Fais dégager les véhicules gênants (poussés hors voie, ou enlèvement) et rétablis la circulation.",
      "Articule avec la Police pour la constatation ; note si la victime veut porter plainte."
    ],
    "consigner": "Nombre et état des blessés, véhicules impliqués + plaques, identités et coordonnées, prise en charge CMS / CHU, constatation Police, plainte.",
    "pieges": "Le nombre de blessés annoncé évolue souvent (le CMS recompte) : reste factuel et mets à jour. Récupère la plaque du fautif à la caméra tant qu'il est là.",
    "souscas": [
      "Collision de véhicules",
      "Piéton renversé",
      "Agent percuté par une moto",
      "Accident hors enceinte (voie publique)"
    ],
    "details": [
      "Engage le CMS / Reflex sans délai pour les blessés (ambulance, attelle) ; le nombre de blessés annoncé change souvent — mets à jour au fil des retours.",
      "Récupère la plaque du fautif à la caméra et les coordonnées des deux parties : la Police et l'assurance en auront besoin (photos en pièce jointe).",
      "Agent percuté par une moto : engage l'ambulance, raccompagne l'auteur (souvent agressif), et lie à la fiche de prise en charge sanitaire.",
      "Dégage les véhicules gênants (poussés hors voie ou enlèvement) pour rétablir la circulation ; la moto est parfois mise en consigne avec sa clé.",
      "Accident hors enceinte (voie publique) : c'est la Police qui constate ; transmets les coordonnées du chauffeur et le retour d'info."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Accident signalé"
      },
      {
        "k": "act",
        "t": "Qualifier : blessés ? véhicules ? où ?"
      },
      {
        "k": "engage",
        "t": "CMS / Reflex : prise en charge des blessés"
      },
      {
        "k": "engage",
        "t": "Patrouille : identités + lever le doute vidéo (plaque)"
      },
      {
        "k": "ask",
        "t": "Véhicule gênant ?",
        "y": "Dégager / enlever",
        "n": "poursuivre"
      },
      {
        "k": "act",
        "t": "Constatation Police, suite (plainte)"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P26",
    "titre": "Colis ou objet suspect",
    "dom": "securite",
    "situation": "Colis, sac, valise ou bagage abandonné signalé. Sûreté : levée de doute, périmètre, et selon le cas brigade cynophile. Jusqu'à preuve du contraire, on le traite comme suspect.",
    "questions": [
      "Où exactement se trouve l'objet (carroyage, repère) ?",
      "Depuis quand, et y a-t-il un propriétaire à proximité ?",
      "Un visuel caméra est-il possible ?",
      "Faut-il un périmètre ou une évacuation du public autour ?"
    ],
    "acteurs": "Patrouille de sécurité (levée de doute, périmètre), appui mobile (évacuation du public), brigade cynophile si doute confirmé, PCA / PC Sûreté pour l'articulation.",
    "conduite": [
      "Localise précisément et lève le doute (caméra + patrouille sur place).",
      "Si doute persistant : établis un périmètre de sécurité et fais évacuer le public autour (appui mobile).",
      "Demande la brigade cynophile si nécessaire et articule avec le PC Sûreté.",
      "À la levée de doute négative (souvent un oubli), lève le périmètre et restitue l'objet au propriétaire retrouvé."
    ],
    "consigner": "Localisation, description de l'objet, heure, levée de doute (caméra, patrouille, cynophile), périmètre, propriétaire retrouvé.",
    "pieges": "Ne fais pas déplacer l'objet par un tiers (un responsable l'a parfois déjà bougé : note-le). Traite-le comme suspect jusqu'à la levée de doute, même si ça ressemble à un oubli.",
    "souscas": [
      "Sac ou valise abandonné",
      "Colis suspect (périmètre)",
      "Bagage sans propriétaire",
      "Levée de doute négative (oubli)"
    ],
    "details": [
      "Traite-le comme suspect jusqu'à la levée de doute : localise, visu caméra, patrouille sur place ; la plupart se révèlent être des oublis (canettes, effets personnels).",
      "Doute persistant : périmètre de sécurité + évacuation du public par l'appui mobile + demande de brigade cynophile ; articule avec le PC Sûreté / PCA.",
      "Évite de faire manipuler l'objet ; si un responsable l'a déjà déplacé (hors zone publique), note-le précisément.",
      "Rappelle le témoin ou l'appelant pour des précisions de localisation (la patrouille ne trouve souvent rien du premier coup).",
      "À la levée de doute négative, lève le périmètre et, si un propriétaire se présente, restitue après vérification d'identité."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Objet abandonné signalé"
      },
      {
        "k": "act",
        "t": "Localiser précisément"
      },
      {
        "k": "watch",
        "t": "Lever le doute (caméra + patrouille)"
      },
      {
        "k": "ask",
        "t": "Doute confirmé ?",
        "y": "Périmètre + évacuation + cynophile",
        "n": "Restituer au propriétaire"
      },
      {
        "k": "act",
        "t": "Articuler avec le PC Sûreté"
      },
      {
        "k": "end",
        "t": "Lever le périmètre, clôture"
      }
    ]
  },
  {
    "code": "P27",
    "titre": "Évacuation d'une zone",
    "dom": "securite",
    "situation": "Faire évacuer une zone : fin de séquence (concert, welcome), tribune, aire d'accueil, ou zone à sécuriser. Coordonner les moyens et aiguiller le flux vers les sorties.",
    "questions": [
      "Quelle zone évacuer, et pour quelle raison (fin de séquence, sécurité) ?",
      "Combien de personnes environ ?",
      "Vers quelles sorties les aiguiller ?",
      "Quels moyens engager (motos appui flux, patrouilles) ?"
    ],
    "acteurs": "Appui flux (moto) et patrouilles (aiguillage), chef de zone, PS Nord (fermeture derrière), Direction / PCA pour l'ordre.",
    "conduite": [
      "Confirme la zone, la raison et l'heure ; estime le nombre de personnes.",
      "Positionne les moyens (motos, patrouilles) aux points d'aiguillage vers les sorties.",
      "Fais évacuer progressivement, zone par zone ; gère les retardataires (boutiques, personnes cherchant leur véhicule).",
      "Une fois vidée, fais fermer derrière (PS Nord, portails) et lève le dispositif."
    ],
    "consigner": "Zone, raison, heure de début et de fin, moyens positionnés, sorties utilisées, points de blocage.",
    "pieges": "Il reste toujours des retardataires (boutiques sans clé pour fermer, personnes voulant récupérer un véhicule à l'autre bout) : anticipe les passerelles ou portails encore ouverts et leurs horaires.",
    "souscas": [
      "Fin de séquence (concert / welcome)",
      "Évacuation d'une tribune",
      "Évacuation d'une aire d'accueil",
      "Évacuation de sécurité (périmètre)"
    ],
    "details": [
      "Positionne des motos appui flux aux points clés pour aiguiller le flux vers les sorties (ex. fin de concert Welcome : moto allée des paddocks, moto tunnel nord).",
      "Évacue progressivement et note l'avancement (zone A vidée, puis esplanade, puis boutiques) : il reste toujours des retardataires.",
      "Gère les cas particuliers : boutique sans clé pour fermer, personnes devant récupérer un véhicule à l'opposé → oriente vers la passerelle ou le portail encore ouvert et note son horaire de fermeture.",
      "Une fois la zone vidée, fais fermer les accès derrière (PS Nord) et remets la rubalise si elle a été arrachée.",
      "Beaucoup de fiches « évacuation » concernent en fait des véhicules à évacuer : distingue bien évacuation de public et enlèvement de véhicule (voir P19)."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Évacuation demandée"
      },
      {
        "k": "act",
        "t": "Zone, raison, nombre, sorties"
      },
      {
        "k": "engage",
        "t": "Positionner motos + patrouilles (aiguillage)"
      },
      {
        "k": "act",
        "t": "Évacuer progressivement, gérer les retardataires"
      },
      {
        "k": "engage",
        "t": "PS Nord : fermer derrière"
      },
      {
        "k": "end",
        "t": "Zone vidée, lever le dispositif"
      }
    ]
  },
  {
    "code": "P28",
    "titre": "Drone ou survol non autorisé",
    "dom": "securite",
    "situation": "Vol de drone au-dessus du site ou survol non autorisé. Sûreté : localiser le télépilote, articuler avec la coordination et la Police. À distinguer d'un vol de drones autorisé ou programmé.",
    "questions": [
      "Où survole le drone, et depuis où semble-t-il piloté ?",
      "Est-ce un vol autorisé ou programmé (spectacle, média) ou non ?",
      "Peut-on localiser le télépilote (patrouille, interception du signal) ?",
      "Faut-il aviser la Direction ou la Police ?"
    ],
    "acteurs": "Patrouille de sécurité (localisation du télépilote), coordination jour (Ludo ACO), Police, S3M en appui.",
    "conduite": [
      "Vérifie d'abord s'il s'agit d'un vol autorisé ou programmé (essais, spectacle) : si oui, simple suivi.",
      "Sinon, engage une patrouille pour localiser le point de pilotage ou d'atterrissage.",
      "Avise la coordination (Ludo ACO) et la Police ; transmets l'adresse ou la position si identifiée.",
      "Consigne les éléments (zone survolée, lieu de pilotage, suite Police)."
    ],
    "consigner": "Zone survolée, lieu de pilotage ou d'atterrissage, caractère autorisé ou non, avis coordination / Police, suite.",
    "pieges": "Ne confonds pas un survol illicite avec un vol de drones programmé (tests, spectacle) : vérifie avant d'engager. Localiser le télépilote est difficile — recoupe les retours patrouille.",
    "souscas": [
      "Survol non autorisé du site",
      "Drone posé en zone privée",
      "Suspicion de captation non autorisée",
      "Vol de drones programmé (à distinguer)"
    ],
    "details": [
      "Commence par vérifier si c'est un vol autorisé ou programmé (essais, spectacle de 200 drones) : si oui, note et suis, n'engage pas de moyens.",
      "Survol non autorisé : engage une patrouille pour localiser le point de pilotage ou d'atterrissage (parfois posé dans des habitations privées voisines) — difficile à identifier précisément.",
      "Avise la coordination jour (Ludo ACO) et la Police ; transmets l'adresse ou la position si la patrouille l'obtient.",
      "L'interception de l'émetteur par une patrouille est parfois possible : note-le.",
      "Consigne la zone survolée et les éléments transmis : sujet sensible, la traçabilité compte."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Drone / survol signalé"
      },
      {
        "k": "ask",
        "t": "Vol autorisé / programmé ?",
        "y": "Simple suivi",
        "n": "Patrouille : localiser le télépilote"
      },
      {
        "k": "act",
        "t": "Aviser coordination (Ludo) + Police"
      },
      {
        "k": "act",
        "t": "Transmettre la position, consigner"
      },
      {
        "k": "end",
        "t": "Clôture"
      }
    ]
  },
  {
    "code": "P29",
    "titre": "Nuisances sonores ou tapage",
    "dom": "securite",
    "situation": "Tapage, bruit ou nuisance sonore signalés (campement bruyant, cris, sono, alarmes déclenchées). Souvent de nuit, dans les campings et aires d'accueil.",
    "questions": [
      "Quelle est la nuisance et où précisément ?",
      "Qui signale (chef de poste, riverain, agent) ?",
      "Récidive (déjà signalé, mêmes individus) ?",
      "Y a-t-il un comportement associé (ivresse, agressivité) ?"
    ],
    "acteurs": "Patrouille de sécurité (ratissage, rappel à l'ordre), chef de poste ou de zone, S3M en appui.",
    "conduite": [
      "Localise la nuisance et envoie une patrouille faire un rappel à l'ordre.",
      "Fais ratisser le périmètre si la source est imprécise (cri, bruit).",
      "En cas de récidive, remonte d'un cran (identification, retrait de billet si comportement associé).",
      "Consigne et, la nuit, passe la consigne de vigilance à la vacation suivante."
    ],
    "consigner": "Nature de la nuisance, localisation, demandeur, récidive, mesures (rappel à l'ordre, identification), suite.",
    "pieges": "Ça reprend souvent après le départ de la patrouille (mêmes individus déjà interpellés) : note la récidive et la vigilance. Un « cri suspect » peut n'être que des voisins qui parlent fort — lève le doute avant d'escalader.",
    "souscas": [
      "Tapage nocturne (campement)",
      "Sono / balance de spectacle",
      "Cri ou bruit suspect (levée de doute)",
      "Récidive après rappel"
    ],
    "details": [
      "Envoie une patrouille faire un rappel à l'ordre ; si la source est imprécise (cri, bruit), fais ratisser le périmètre.",
      "Récidive fréquente : ça reprend après le départ de la patrouille (souvent les mêmes personnes déjà interpellées) — note-le et passe la vigilance à la vacation suivante.",
      "Cri ou bruit « suspect » : lève le doute avant d'escalader (souvent des voisins qui parlent fort dans les tentes).",
      "Comportement associé (ivresse, insultes, dégradations) : bascule vers la procédure adaptée (P24 ivresse, P06 altercation), relève l'identité, retire le billet si nécessaire.",
      "Sono ou alarmes de riverains (balance de spectacle) : informe le responsable pour ajuster."
    ],
    "flow": [
      {
        "k": "start",
        "t": "Nuisance sonore signalée"
      },
      {
        "k": "act",
        "t": "Localiser + qui signale"
      },
      {
        "k": "engage",
        "t": "Patrouille : rappel à l'ordre / ratissage"
      },
      {
        "k": "ask",
        "t": "Récidive / comportement associé ?",
        "y": "Identifier, billet retiré si besoin",
        "n": "poursuivre"
      },
      {
        "k": "end",
        "t": "Consigner, vigilance à passer"
      }
    ]
  }
]


def build_doc(p, now):
    return {
        "code": p["code"], "titre": p["titre"], "dom": p["dom"],
        "situation": p.get("situation", ""), "questions": p.get("questions", []),
        "acteurs": p.get("acteurs", ""), "conduite": p.get("conduite", []),
        "consigner": p.get("consigner", ""), "pieges": p.get("pieges", ""),
        "souscas": p.get("souscas", []), "details": p.get("details", []),
        "flow": p.get("flow", []),
        "status": "published", "version": 1,
        "created_at": now, "updated_at": now,
        "created_by": "seed", "updated_by": "seed",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mongo", default=os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    ap.add_argument("--db", default="titan", help="base cible (defaut: titan)")
    ap.add_argument("--apply", action="store_true", help="execute reellement (sinon dry-run)")
    ap.add_argument("--overwrite", action="store_true",
                    help="ecrase aussi statut/version/created_* des fiches existantes")
    args = ap.parse_args()

    db = MongoClient(args.mongo)[args.db]
    col_cat = db["cockpit_wiki_categories"]
    col_pro = db["cockpit_wiki_procedures"]
    now = datetime.now(timezone.utc)

    print("Base cible :", args.db)
    print("Mode       :", "APPLY" if args.apply else "DRY-RUN (aucune ecriture)",
          "+ OVERWRITE" if args.overwrite else "")
    print("-" * 60)

    if args.apply:
        col_pro.create_index("code", unique=True)
        col_pro.create_index([("dom", ASCENDING)])
        col_pro.create_index([("status", ASCENDING)])
        col_cat.create_index("key", unique=True)

    # categories
    for c in CATEGORIES:
        if args.apply:
            col_cat.update_one({"key": c["key"]},
                               {"$set": {"label": c["label"], "color": c["color"], "order": c["order"]}},
                               upsert=True)
    print("categories :", len(CATEGORIES), "(", ", ".join(c["key"] for c in CATEGORIES), ")")

    # procedures
    n_new = n_upd = 0
    for p in PROCEDURES:
        doc = build_doc(p, now)
        existing = col_pro.find_one({"code": doc["code"]})
        if existing:
            patch = dict(doc)
            if not args.overwrite:
                for f in ("created_at", "created_by", "version", "status"):
                    patch.pop(f, None)
            patch["updated_at"] = now
            if args.apply:
                col_pro.update_one({"code": doc["code"]}, {"$set": patch})
            n_upd += 1
        else:
            if args.apply:
                col_pro.insert_one(doc)
            n_new += 1

    total = col_pro.count_documents({}) if args.apply else "?"
    print("procedures :", n_new, "crees,", n_upd, "mis a jour  (total base :", total, ")")
    if not args.apply:
        print("-" * 60)
        print("DRY-RUN termine. Relance avec --apply pour ecrire dans", args.db + ".")


if __name__ == "__main__":
    main()
