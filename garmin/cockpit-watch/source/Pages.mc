using Toybox.Math;
using Toybox.Graphics;

// Ce module n'existe que pour l'app : AUCUNE annotation (:glance) ni
// (:background) ici. C'est ce qui garantit que les quatre blocs (mc/tr/me/st)
// et le code qui les lit ne sont jamais compiles dans la glance ni le
// service de fond, cf. Api.toPagesDict et Cache.savePages/loadPages.
module Pages {

    // Les mots du verdict trafic sont ceux du mur (circulation.html:494) :
    // le poignet et l'ecran doivent dire la meme chose du meme etat.
    function verdictMot(vd) {
        if (vd == null) { return "--"; }
        if (vd >= 3) { return "CRITIQUE"; }
        if (vd == 2) { return "TENSION"; }
        if (vd == 1) { return "VIGILANCE"; }
        return "FLUIDE";
    }

    // Un seul bloc peut valoir null (source tombee cote serveur), et pg
    // lui-meme peut valoir null (aucun cache pages disponible). Les quatre
    // accesseurs ci-dessous ne font que nommer la cle : ils ne restructurent
    // rien, les vues qui consommeront ces blocs n'existent pas encore.
    function bloc(pg, cle) {
        if (pg == null) { return null; }
        return pg[cle];
    }

    function mainCourante(pg) { return bloc(pg, "mc"); }
    function trafic(pg) { return bloc(pg, "tr"); }
    function meteo(pg) { return bloc(pg, "me"); }
    function frequentation(pg) { return bloc(pg, "st"); }
    function guidage(pg) { return bloc(pg, "gd"); }

    // Vigilance Meteo-France (`vg`, echelle 0-3 de meteo_etat.ORDRE_COULEURS
    // -- vert/jaune/orange/rouge), DISTINCTE de la consigne (`cn`/`cl`, seuils
    // du mur) et du verdict trafic (`vd`, mots du mur circulation) : les
    // trois echelles partagent 0-3 mais ne se melangent jamais. `null` quand
    // le niveau est inconnu OU vert -- dans les deux cas, rien a nommer, la
    // regle commune a toute l'app est de rester muet plutot que d'afficher
    // un calme qui n'est pas confirme (inconnu) ou qui ne merite aucune
    // mention (vert). Le mot d'abord, la couleur en renfort (couleurNiveau
    // dans MeteoView/CockpitView) : jamais la couleur seule, un daltonien en
    // plein soleil ne la distinguerait pas.
    function vigilanceMot(vg) {
        if (vg == null || vg < 1) { return null; }
        if (vg >= 3) { return "VIGILANCE ROUGE"; }
        if (vg == 2) { return "VIGILANCE ORANGE"; }
        return "VIGILANCE JAUNE";
    }

    // Forme compacte pour la bande de voyants (page 1, CockpitView) : elle
    // partage sa ligne avec << MC n >> et le verdict trafic, pas de place
    // pour la forme longue. Meme echelle, memes seuils que vigilanceMot.
    function vigilanceMotCourt(vg) {
        if (vg == null || vg < 1) { return null; }
        if (vg >= 3) { return "VIG ROUGE"; }
        if (vg == 2) { return "VIG ORANGE"; }
        return "VIG JAUNE";
    }

    // Au-dela, la montre n'a pas parle au serveur depuis assez longtemps
    // pour que ce soit la cause la plus probable de tout age eleve. Cale
    // sur le rythme du service de fond (5 min) : deux cycles manques.
    const SEUIL_HORS_LIGNE_S = 660;

    // Pied de page : "maj 30 s", ou "hors ligne 3 h" quand c'est la MONTRE
    // qui n'a pas joint le serveur.
    //
    // ⚠️ DEUX PANNES DIFFERENTES, ET LE MEME AGE LES AFFICHAIT PAREIL.
    // `t` date la DONNEE cote serveur, `rx` la derniere REPONSE recue par
    // la montre. Cas reel : le serveur repondait a 4 SECONDES de fraicheur
    // et la page affichait "maj 3 h" -- c'etait le lien BLE, pas le
    // collecteur Waze, et rien a l'ecran ne permettait de le savoir.
    //
    // Les deux appellent des gestes opposes : rapprocher le telephone, ou
    // aller voir le collecteur. Meme distinction que `mr` en mode past.
    //
    // Le lien prime quand les deux sont vieux : tant que la montre ne parle
    // pas au serveur, l'age de `t` qu'elle affiche est celui d'un cache,
    // pas une mesure.
    //
    // Publique pour rester testable en VALEUR : afficher le mauvais des
    // deux ages ne leve rien, et envoie chercher la panne au mauvais
    // endroit.
    function libelleFraicheur(st, ageDonnee, nowSec) {
        var ageReponse = State.responseAgeSec(st, nowSec);
        if (ageReponse != null && ageReponse > SEUIL_HORS_LIGNE_S) {
            // La CAUSE quand on la connait, l'age sinon : un jeton refuse
            // et un telephone hors de portee demandent deux gestes
            // differents, et attendre ne resout que le second.
            var mot = Api.motErreur(Api.derniereErreur());
            if (mot != null) {
                // L'EMPREINTE du jeton employe accompagne les deux erreurs
                // qui le concernent. Sans elle, il etait impossible de
                // savoir si la montre envoyait le jeton qu'on venait de
                // compiler ou un reliquat de reglage -- c'est exactement ce
                // qui a coute cinq reconstructions.
                var code = Api.derniereErreur();
                if (code == 401 || code == Api.ERR_SANS_JETON) {
                    return mot + " " + Jeton.empreinte();
                }
                return mot + " " + Fmt.age(ageReponse);
            }
            return "hors ligne " + Fmt.age(ageReponse);
        }
        return "maj " + Fmt.age(ageDonnee);
    }

    // --- Indicateur de pagination ---------------------------------------
    //
    // Une rangee de petits losanges en haut a droite : PLEIN pour la
    // sous-page courante, CONTOUR SEUL pour les autres. C'est la seule
    // chose qui dise qu'il Y A d'autres pages -- sans elle, un utilisateur
    // qui n'a jamais appuye sur START ne peut pas deviner qu'il en existe.
    //
    // En haut a DROITE et non centre : l'entete de page ("AXES 5-8 / 18",
    // "PROCHAIN") occupe deja toute la corde a son ordonnee. Les losanges
    // vivent donc au-dessus, sur une bande ou rien d'autre ne se dessine.
    //
    // Le losange est incline (parallelogramme) plutot que rond : sur un MIP
    // sans anti-aliasing, un petit cercle rend un moignon de six pixels
    // baveux, une forme a angles vifs reste nette.

    const PAGINATION_Y = 14;        // ordonnee du sommet des losanges
    const PAGINATION_H = 9;         // hauteur
    const PAGINATION_L = 6;         // largeur de la base
    const PAGINATION_BIAIS = 3;     // decalage horizontal du sommet
    const PAGINATION_PAS = 12;      // entraxe

    // Au-dela, la rangee deborde de la corde disponible et les losanges
    // deviennent indistinguables. Un livret plus long affiche alors son
    // compteur de pied ("7/9") et rien ici -- un indicateur illisible vaut
    // moins qu'une absence d'indicateur.
    const PAGINATION_MAX = 8;

    // Les losanges sont-ils dessinables pour ce nombre de pages ?
    // Publique pour rester testable en VALEUR : une rangee silencieusement
    // absente ne leve aucune exception.
    function paginationVisible(total) {
        return total != null && total > 1 && total <= PAGINATION_MAX;
    }

    // Abscisse du bord GAUCHE du losange d'index `i`, la rangee etant
    // calee a droite de la corde disponible a son ordonnee.
    function paginationX(dc, total, i) {
        var largeur = (total - 1) * PAGINATION_PAS + PAGINATION_L
                      + PAGINATION_BIAIS;
        // La corde est mesuree a la BASE des losanges (le bord le plus
        // eloigne du centre pour un bloc de la moitie haute, cf.
        // largeurUtile), moins 10 px de marge interne.
        var corde = largeurUtile(dc, PAGINATION_Y, PAGINATION_H) - 20;
        var xDroite = dc.getWidth() / 2.0 + corde / 2.0;
        return xDroite - largeur + i * PAGINATION_PAS;
    }

    // Dessine la rangee. `courante` est un index 0-base.
    function dessinerPagination(dc, courante, total) {
        if (!paginationVisible(total)) {
            return;
        }
        var yH = PAGINATION_Y;
        var yB = PAGINATION_Y + PAGINATION_H;
        for (var i = 0; i < total; i += 1) {
            var x = paginationX(dc, total, i);
            // Parallelogramme penche vers la droite : base en bas a gauche,
            // sommet decale de PAGINATION_BIAIS.
            var pts = [[x + PAGINATION_BIAIS, yH],
                       [x + PAGINATION_BIAIS + PAGINATION_L, yH],
                       [x + PAGINATION_L, yB],
                       [x, yB]];
            if (i == courante) {
                dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
                dc.fillPolygon(pts);
            } else {
                // Contour seul. Gris moyen et non blanc : la page courante
                // doit rester la plus lumineuse de la rangee, sinon la
                // distinction plein/vide ne se voit pas d'un coup d'oeil.
                dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
                dc.setPenWidth(1);
                for (var k = 0; k < 4; k += 1) {
                    var a = pts[k];
                    var b = pts[(k + 1) % 4];
                    dc.drawLine(a[0], a[1], b[0], b[1]);
                }
            }
        }
    }

    // Largeur utile du cadran rond a la hauteur d'un BLOC, pas d'un point.
    // Un texte est ancre par le HAUT (jamais TEXT_JUSTIFY_VCENTER dans cette
    // app) et occupe [y, y + hauteur] : sur un cadran rond, la corde
    // disponible RETRECIT en s'eloignant du centre (140 sur un ecran de
    // 280), donc c'est le bord du bloc le PLUS ELOIGNE du centre qui
    // contraint, jamais l'ancre seule.
    //
    // Chaque vue appelait auparavant largeurUtile(dc, y) -- la corde a
    // L'ANCRE, pas au bord contraignant -- et SURESTIMAIT donc la place
    // disponible chaque fois que le bloc s'etendait vers le centre plutot
    // que vers le bord (ex : une ligne juste sous le centre, dont la BASE,
    // pas le sommet, est la plus proche du bord oppose). Exactement le
    // meme defaut que la reprise verticale de cette tache a corrige sur
    // l'axe des ordonnees -- ici sur l'axe des largeurs. Ecarts mesures
    // avant correction, au pire cas de chaque vue : FrequentationView
    // -35,9 px, MeteoView -17,7 et -10,4 px, TraficView -15,2 px (cf.
    // rapport de tache).
    //
    // Centralisee ici (auparavant dupliquee, identique, dans les six vues)
    // pour n'avoir qu'UNE formule a corriger si le rayon ou la convention
    // d'ancrage changent un jour.
    function largeurUtile(dc, y, hauteur) {
        var r = dc.getWidth() / 2.0;
        var dyHaut = (y - r).abs();
        var dyBas = (y + hauteur - r).abs();
        var dy = dyHaut > dyBas ? dyHaut : dyBas;
        if (dy >= r) {
            return 0.0;
        }
        return 2.0 * Math.sqrt(r * r - dy * dy);
    }
}
