using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Position;
using Toybox.Sensor;
using Toybox.PersistedContent;
using Toybox.Math;
using Toybox.Time;
using Toybox.Attention;

// Guidage vers un point envoye depuis le cockpit.
//
// Le point (`gd`) arrive dans le payload normal, adresse a CETTE montre.
// La page dessine une fleche vers lui, la distance, et son nom. START le
// pousse dans les lieux enregistres natifs de la montre, pour passer la
// main a la navigation Garmin -- carte, fil d'Ariane, alerte hors trajet,
// tout ce que cette page ne fera jamais sur 280 pixels.
//
// LE GPS N'EST ALLUME QUE PENDANT QUE CETTE PAGE EST AFFICHEE. C'est le
// seul endroit de l'app qui consomme un capteur continu ; l'oublier allume
// viderait la batterie d'une montre qui tient trente jours autrement.
// CockpitView appelle activer()/desactiver() a chaque changement de page,
// et desactiver() est appele aussi a la fermeture de l'app (onHide).
class GuidageView extends WatchUi.View {

    // Duree d'affichage de la confirmation apres un appui sur START.
    hidden const CONFIRMATION_S = 5;

    hidden var mActive = false;
    hidden var mPosition = null;      // [lat, lon] en degres, ou null
    hidden var mPrecision = null;     // Position.QUALITY_*, ou null
    hidden var mCap = null;           // cap de la MONTRE, degres, ou null
    hidden var mConfirmeA = null;     // instant du dernier waypoint enregistre
    hidden var mEchecWaypoint = false;

    function initialize() {
        View.initialize();
    }

    // --- Cycle de vie du capteur ---------------------------------------

    function activer() {
        if (mActive) { return; }
        mActive = true;
        Position.enableLocationEvents(Position.LOCATION_CONTINUOUS,
                                       method(:surPosition));
        Sensor.enableSensorEvents(method(:surCapteur));
        // Une position peut deja etre connue (une autre app a laisse un fix
        // recent) : la lire tout de suite evite d'afficher << recherche >>
        // pendant une seconde alors qu'on sait deja ou l'on est.
        surPosition(Position.getInfo());
        surCapteur(Sensor.getInfo());
    }

    function desactiver() {
        if (!mActive) { return; }
        mActive = false;
        Position.enableLocationEvents(Position.LOCATION_DISABLE,
                                       method(:surPosition));
        Sensor.enableSensorEvents(null);
        // Les mesures sont OUBLIEES en sortant. Les garder ferait afficher,
        // au retour sur la page, une fleche calculee sur une position
        // vieille de plusieurs minutes -- juste assez plausible pour qu'on
        // la suive.
        mPosition = null;
        mPrecision = null;
        mCap = null;
    }

    function estActive() {
        return mActive;
    }

    // Couture de test : pose directement position, cap et qualite, sans
    // passer par le GPS ni la boussole. Le simulateur ne fournit ni fix ni
    // cap deterministes, et les trois regles qui comptent sur cette page
    // (fleche absente sans cap, distance juste, mot de precision) doivent
    // se verifier en VALEUR. Aucun code de production n'appelle cette
    // fonction -- elle n'est qu'un point d'entree pour GuidageTest.mc.
    function injecterPourTest(position, cap, precision) {
        mPosition = position;
        mCap = cap;
        mPrecision = precision;
    }

    function surPosition(info as Position.Info) as Void {
        if (info == null) { return; }
        mPrecision = info.accuracy;
        if (info.position != null) {
            var deg = info.position.toDegrees();
            // Un fix de qualite NOT_AVAILABLE / LAST_KNOWN porte quand meme
            // des coordonnees, parfois tres anciennes. On les garde (la
            // distance reste indicative) mais la page dit la qualite : c'est
            // a l'utilisateur de savoir s'il peut s'y fier.
            mPosition = [deg[0], deg[1]];
        }
        WatchUi.requestUpdate();
    }

    function surCapteur(info as Sensor.Info) as Void {
        if (info == null) { return; }
        // Sensor.Info.heading est le cap vrai en RADIANS. Le systeme le tire
        // de la boussole magnetique a l'arret et de la direction de
        // deplacement en mouvement -- c'est exactement ce qu'il faut ici,
        // et c'est pourquoi on ne lit PAS Position.Info.heading, qui est
        // le cap route et ne veut rien dire a l'arret.
        if (info has :heading && info.heading != null) {
            mCap = Geo.normaliserDegres(Math.toDegrees(info.heading));
        }
        WatchUi.requestUpdate();
    }

    // --- Lecture du point ----------------------------------------------

    function point() {
        return Pages.bloc(Cache.loadPages(), "gd");
    }

    // Distance en metres jusqu'au point, ou null si l'un des deux bouts
    // manque. Publique pour rester testable en VALEUR.
    function distanceM() {
        var gd = point();
        if (gd == null || mPosition == null) { return null; }
        return Geo.distanceM(mPosition[0], mPosition[1], gd["lat"], gd["lon"]);
    }

    // Angle de la fleche a l'ecran, ou null s'il manque la position OU le
    // cap de la montre. Publique pour rester testable en VALEUR : une
    // fleche qui se dessinerait par defaut vers le haut ne leverait aucune
    // exception, et pointerait pourtant n'importe ou.
    function angleFleche() {
        var gd = point();
        if (gd == null || mPosition == null || mCap == null) { return null; }
        var cap = Geo.capVers(mPosition[0], mPosition[1], gd["lat"], gd["lon"]);
        return Geo.angleRelatif(cap, mCap);
    }

    // Ce que la page doit dire de son etat, en un mot. Publique pour rester
    // testable en VALEUR : c'est la phrase qui distingue << je cherche >> de
    // << je ne sais pas >>, et aucune des deux ne leve d'exception.
    function etat() {
        if (point() == null) { return "sans_point"; }
        if (mPosition == null) { return "recherche_gps"; }
        if (mCap == null) { return "sans_cap"; }
        return "guide";
    }

    // --- START : pousser le point dans les lieux enregistres -----------

    function enregistrerWaypoint() {
        var gd = point();
        if (gd == null) { return false; }
        try {
            var lieu = new Position.Location({
                :latitude => gd["lat"],
                :longitude => gd["lon"],
                :format => :degrees
            });
            var nom = gd["n"];
            if (nom == null || nom.length() == 0) { nom = "Cockpit"; }
            PersistedContent.saveWaypoint(lieu, {:name => nom});
            mConfirmeA = Time.now().value();
            mEchecWaypoint = false;
            if (Attention has :vibrate) {
                Attention.vibrate([new Attention.VibeProfile(40, 150)]);
            }
        } catch (e) {
            // La liste de lieux de la montre peut etre pleine, ou l'API
            // refuser. Le dire plutot que de laisser croire a une
            // reussite -- c'est la regle de toute l'app.
            mEchecWaypoint = true;
            mConfirmeA = null;
        }
        WatchUi.requestUpdate();
        return !mEchecWaypoint;
    }

    // --- Rendu ----------------------------------------------------------

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, "GUIDAGE",
                    Graphics.TEXT_JUSTIFY_CENTER);

        var gd = point();
        if (gd == null) {
            // Etat NORMAL, et de loin le plus frequent : personne n'est
            // guide la plupart du temps. Ce n'est pas une panne, et la page
            // ne doit surtout pas le peindre comme telle.
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() / 2 - hS, Graphics.FONT_SMALL,
                        "aucun point", Graphics.TEXT_JUSTIFY_CENTER);
            dc.drawText(w / 2, dc.getHeight() / 2 + 6, Graphics.FONT_XTINY,
                        "envoye depuis le cockpit",
                        Graphics.TEXT_JUSTIFY_CENTER);
            // L'indice sur START vit ICI, et pas dans le pied de page de
            // l'ecran guide : la corde n'y fait que 134 px (mesuree), ou
            // "GPS faible . START = enregistrer" sortait tronque a
            // "GPS faible . STA". Cet ecran-la est de toute facon celui que
            // l'on voit presque toujours -- c'est le bon endroit pour
            // apprendre le geste.
            dc.drawText(w / 2, dc.getHeight() / 2 + 6 + hX + 6,
                        Graphics.FONT_XTINY,
                        "START l'enregistrera",
                        Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        var distance = distanceM();
        var angle = angleFleche();

        if (angle != null) {
            dessinerFleche(dc, w / 2, 112, 44, angle, couleurPrecision());
        } else {
            // PAS de fleche par defaut. Sans cap, on connait la direction du
            // point dans le monde mais pas l'orientation du poignet :
            // dessiner reviendrait a pointer le nord en pretendant montrer
            // la route. Un disque neutre tient la place et ne dit rien.
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.setPenWidth(3);
            dc.drawCircle(w / 2, 112, 30);
            dc.setPenWidth(1);
        }

        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 160, Graphics.FONT_MEDIUM,
                    Geo.formatDistance(distance), Graphics.TEXT_JUSTIFY_CENTER);

        var nom = gd["n"];
        if (nom == null) { nom = ""; }
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 201, Graphics.FONT_XTINY,
                    ajusterTexte(dc, nom, Graphics.FONT_XTINY,
                                 Pages.largeurUtile(dc, 201, hX)),
                    Graphics.TEXT_JUSTIFY_CENTER);

        dessinerPied(dc, hX);
    }

    // Fleche pleine : un triangle isocele oriente selon `angle` (0 = haut de
    // l'ecran = tout droit devant). Une fleche pleine se lit d'un coup d'oeil
    // la ou un trait demande d'etre interprete.
    hidden function dessinerFleche(dc, cx, cy, rayon, angle, couleur) {
        var rad = Math.toRadians(angle);
        // Repere ecran : x vers la droite, y vers le BAS. Un cap de 0 doit
        // pointer vers le haut, donc la pointe est a -cos.
        var pointe = [cx + rayon * Math.sin(rad), cy - rayon * Math.cos(rad)];
        var gauche = angleVers(cx, cy, rayon * 0.72, angle + 140.0);
        var droite = angleVers(cx, cy, rayon * 0.72, angle - 140.0);
        var creux = angleVers(cx, cy, rayon * 0.28, angle + 180.0);
        dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
        dc.fillPolygon([pointe, gauche, creux, droite]);
    }

    hidden function angleVers(cx, cy, rayon, angle) {
        var rad = Math.toRadians(Geo.normaliserDegres(angle));
        return [cx + rayon * Math.sin(rad), cy - rayon * Math.cos(rad)];
    }

    // La couleur dit la CONFIANCE dans la fleche, pas l'urgence : verte sur
    // un fix franc, ambre sur un fix degrade. Le mot correspondant est au
    // pied de page -- la couleur ne porte jamais seule une information.
    hidden function couleurPrecision() {
        if (mPrecision == null) { return Graphics.COLOR_ORANGE; }
        if (mPrecision == Position.QUALITY_GOOD) { return Graphics.COLOR_GREEN; }
        if (mPrecision == Position.QUALITY_USABLE) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_ORANGE;
    }

    // Mot de la qualite du fix. Publique pour rester testable en VALEUR :
    // c'est ce mot, et non la couleur, qui dit a l'utilisateur s'il peut
    // suivre la fleche.
    function motPrecision() {
        if (mPrecision == null) { return "recherche GPS"; }
        if (mPrecision == Position.QUALITY_GOOD) { return "GPS bon"; }
        if (mPrecision == Position.QUALITY_USABLE) { return "GPS moyen"; }
        if (mPrecision == Position.QUALITY_POOR) { return "GPS faible"; }
        return "recherche GPS";
    }

    hidden function dessinerPied(dc, hX) {
        var w = dc.getWidth();
        var y = dc.getHeight() - hX - 17;
        var texte;
        var couleur = Graphics.COLOR_DK_GRAY;

        // TOUS les libelles ci-dessous ont ete MESURES contre la corde du
        // pied de page, qui ne fait que 133,7 px a cette ordonnee -- la plus
        // etroite de la page. Les premieres redactions ("boussole
        // indisponible" 164 px, "enregistrement refuse" 168 px,
        // "enregistre dans les lieux" 186 px) sortaient tronquees au milieu
        // d'un mot, ce qu'aucun controle geometrique ne signale : le texte
        // coupe tient parfaitement dans l'ecran.
        if (mEchecWaypoint) {
            texte = "non enregistre";               // 109 px
            couleur = Graphics.COLOR_RED;
        } else if (mConfirmeA != null
                   && Time.now().value() - mConfirmeA < CONFIRMATION_S) {
            texte = "enregistre";                   // 78 px
            couleur = Graphics.COLOR_GREEN;
        } else if (etat().equals("sans_cap")) {
            // Distinct de << recherche GPS >> : on SAIT ou l'on est, c'est
            // l'orientation du poignet qui manque. Les deux se corrigent
            // differemment (attendre vs bouger le bras), le dire epargne
            // une minute d'attente inutile.
            texte = "sans boussole";                // 108 px
        } else {
            texte = motPrecision();                 // 63 a 111 px
        }

        dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY,
                    ajusterTexte(dc, texte, Graphics.FONT_XTINY,
                                 Pages.largeurUtile(dc, y, hX)),
                    Graphics.TEXT_JUSTIFY_CENTER);
    }

    hidden function ajusterTexte(dc, texte, font, dispo) {
        if (dc.getTextWidthInPixels(texte, font) <= dispo) {
            return texte;
        }
        var t = texte;
        while (t.length() > 1 && dc.getTextWidthInPixels(t, font) > dispo) {
            t = t.substring(0, t.length() - 1);
        }
        return t;
    }
}
