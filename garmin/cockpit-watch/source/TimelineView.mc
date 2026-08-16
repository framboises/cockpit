using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Time;

// Timeline : ce qui tombe dans les douze prochaines heures.
//
// C'est la raison d'etre du cockpit ramenee au poignet. La valeur n'est pas
// l'heure ("08:00") mais le DELAI ("dans 42 min") : le premier oblige a un
// calcul mental, le second se lit d'un coup.
//
// DEUX SOURCES, et c'est deliberé :
//
//   - `nx` (le PROCHAIN seul) arrive dans le payload normal, donc dans le
//     cache. La page affiche donc quelque chose des son ouverture, meme
//     hors de portee du telephone.
//   - la LISTE complete est demandee a /timeline quand la page s'ouvre.
//     Elle pese ~570 octets qu'il serait absurde de transmettre toutes les
//     minutes pour une page consultee quelques fois par jour.
//
// Ce n'est pas de la redondance : c'est ce qui fait qu'on n'attend jamais
// devant un ecran vide. Le heros s'affiche tout de suite, la liste se
// remplit derriere.
class TimelineView extends WatchUi.View {

    // Quatre vignettes par ecran de liste. Mesure au VRAI device
    // (fenix8solar51mm, 280x280) : deux lignes par vignette (libelle puis
    // lieu), pas de 46 px, la quatrieme finit a 217 px, sous le pied (241).
    // Une cinquieme commencerait a 232 et le chevaucherait.
    hidden const PAR_ECRAN = 4;

    hidden var mSousPage = 0;
    hidden var mListe = null;      // null = pas encore recue
    hidden var mErreur = false;

    function initialize() {
        View.initialize();
    }

    // --- Chargement paresseux -------------------------------------------

    function activer() {
        // La liste survit a la sortie de page : elle change lentement (des
        // horaires poses a l'avance) et le serveur la cache deja 60 s. La
        // redemander a chaque passage serait du trafic BLE pur perte.
        if (mListe == null) {
            Api.fetchTimeline(method(:onRecue));
        }
    }

    function onRecue(ok, liste) as Void {
        if (ok && liste != null) {
            mListe = liste;
            mErreur = false;
        } else if (mListe == null) {
            mErreur = true;
        }
        WatchUi.requestUpdate();
    }

    // --- Etat du livret --------------------------------------------------
    //
    // Publiques pour rester testables en VALEUR (meme raison que
    // TraficView.sousPage) : un livret qui boucle mal, ou dont l'index
    // pointe au-dela d'une liste raccourcie, ne leve aucune exception.

    function vignettes() {
        return mListe == null ? [] : mListe;
    }

    // 1 (le heros) + un ecran par tranche de quatre. Toujours >= 1 : le
    // heros existe des qu'il y a un `nx` dans le cache, avant meme que la
    // liste n'arrive.
    function nbSousPages() {
        var n = vignettes().size();
        if (n <= 0) { return 1; }
        return 1 + (n + PAR_ECRAN - 1) / PAR_ECRAN;
    }

    // Index BORNE a la lecture. La liste raccourcit toute seule au fil des
    // heures (une vignette passee disparait du prochain relevé) : sans ce
    // bornage, l'utilisateur resterait bloque sur un ecran vide.
    function sousPage() {
        var n = nbSousPages();
        if (mSousPage >= n) { return n - 1; }
        if (mSousPage < 0) { return 0; }
        return mSousPage;
    }

    function sousPageSuivante() {
        mSousPage = (sousPage() + 1) % nbSousPages();
        WatchUi.requestUpdate();
    }

    // Revenir au heros en quittant la page : c'est lui qu'on vient chercher
    // d'un coup d'oeil, il doit etre la ou on l'attend.
    function remiseAZero() {
        mSousPage = 0;
    }

    // Le heros : la premiere vignette de la liste si elle est arrivee, sinon
    // le `nx` du cache. L'ordre compte -- la liste est plus fraiche que le
    // noyau (elle est demandee a l'ouverture de la page), et les deux
    // portent la meme forme, donc un seul chemin de rendu.
    // Publique pour rester testable en VALEUR.
    function heros() {
        var liste = vignettes();
        if (liste.size() > 0) {
            return liste[0];
        }
        return State.prochaine(Cache.load());
    }

    // --- Rendu ------------------------------------------------------------

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        if (sousPage() == 0) {
            dessinerHeros(dc);
        } else {
            dessinerListe(dc, sousPage() - 1);
        }
        dessinerPied(dc);
    }

    hidden function dessinerHeros(dc) {
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, "PROCHAIN",
                    Graphics.TEXT_JUSTIFY_CENTER);

        var v = heros();
        if (v == null) {
            // Etat NORMAL l'essentiel de l'annee, et pas une panne : rien
            // n'est prevu dans les douze prochaines heures. Le distinguer
            // d'une source injoignable (pied de page, plus bas) est ce qui
            // evite de chercher un incident la ou il n'y en a pas.
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() / 2 - hS / 2, Graphics.FONT_SMALL,
                        mErreur ? "indisponible" : "rien de prevu",
                        Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        // Le DELAI en gros, l'heure en petit dessous. C'est le delai qui
        // porte la decision ; l'heure sert a la confirmer.
        var now = Time.now().value();
        dc.setColor(couleurUrgence(v[0], now), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 60, Graphics.FONT_MEDIUM, Fmt.delai(v[0], now),
                    Graphics.TEXT_JUSTIFY_CENTER);

        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 60 + hM - 2, Graphics.FONT_XTINY, Fmt.hour(v[0]),
                    Graphics.TEXT_JUSTIFY_CENTER);

        var yAct = 60 + hM - 2 + hX + 12;
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, yAct, Graphics.FONT_SMALL,
                    ajusterLibelle(dc, v, Graphics.FONT_SMALL,
                                   Pages.largeurUtile(dc, yAct, hS)),
                    Graphics.TEXT_JUSTIFY_CENTER);

        var yLieu = yAct + hS - 2;
        var lieu = (v.size() > 2 && v[2] != null) ? v[2] : "";
        if (lieu.length() > 0) {
            dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yLieu, Graphics.FONT_XTINY,
                        ajusterTexte(dc, lieu, Graphics.FONT_XTINY,
                                     Pages.largeurUtile(dc, yLieu, hX)),
                        Graphics.TEXT_JUSTIFY_CENTER);
        }
    }

    // "Ouverture parkings" seul, ou "Ouverture parkings (8)" quand la
    // vignette en factorise plusieurs. Le compte arrive SEPARE du libelle
    // (watch_timeline._compacter) : la vue choisit de l'afficher ou non
    // selon la place, plutot que d'analyser une chaine pour le retrouver.
    // Publique pour rester testable en VALEUR.
    function libelle(v) {
        if (v == null) { return ""; }
        var texte = (v[1] != null) ? v[1] : "";
        var suffixe = suffixeCompte(v);
        return suffixe.length() > 0 ? (texte + suffixe) : texte;
    }

    // " (8)", ou "" si la vignette n'en factorise pas plusieurs.
    // Publique pour rester testable en VALEUR.
    function suffixeCompte(v) {
        if (v == null || v.size() <= 3 || v[3] == null || v[3] <= 1) {
            return "";
        }
        return " (" + v[3].toString() + ")";
    }

    // Libelle ajuste a la place disponible, LE COMPTE TOUJOURS PRESERVE.
    //
    // Trouve a la sonde : "Ouverture des tribunes nord es (12)" tronque a
    // la corde sortait "Ouverture des tribunes nord es (1" -- soit un
    // chiffre FAUX, qui annonce une tribune la ou il y en a douze. Un mot
    // abrege se voit ; un nombre ampute se lit comme une valeur.
    //
    // Meme regle que la ligne d'axe de TraficView : le NOM est le seul
    // element elastique, les chiffres ne cedent jamais.
    hidden function ajusterLibelle(dc, v, font, dispo) {
        var suffixe = suffixeCompte(v);
        var texte = (v != null && v[1] != null) ? v[1] : "";
        if (suffixe.length() == 0) {
            return ajusterTexte(dc, texte, font, dispo);
        }
        var place = dispo - dc.getTextWidthInPixels(suffixe, font);
        if (place <= 0) {
            // Cas extreme (corde plus etroite que le seul compte) : mieux
            // vaut le compte seul qu'un compte faux.
            return suffixe;
        }
        return ajusterTexte(dc, texte, font, place) + suffixe;
    }

    // La couleur dit l'IMMINENCE, et le mot la dit aussi ("dans 5 min") --
    // jamais la couleur seule, regle commune a toute l'app.
    hidden function couleurUrgence(quand, now) {
        if (quand == null) { return Graphics.COLOR_DK_GRAY; }
        var reste = quand - now;
        if (reste < 300) { return Graphics.COLOR_RED; }      // moins de 5 min
        if (reste < 1800) { return Graphics.COLOR_ORANGE; }  // moins de 30 min
        return Graphics.COLOR_WHITE;
    }

    hidden function dessinerListe(dc, ecran) {
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var liste = vignettes();
        var now = Time.now().value();

        var debut = ecran * PAR_ECRAN;
        var fin = debut + PAR_ECRAN;
        if (fin > liste.size()) { fin = liste.size(); }

        // "A VENIR 5-8 / 9", mais "A VENIR 9 / 9" quand le dernier ecran
        // n'en porte qu'une : "9-9" se lit comme une erreur d'affichage.
        // Meme correction que l'entete de TraficView, pour la meme raison.
        var plage = (fin - debut > 1)
                    ? ((debut + 1).toString() + "-" + fin.toString())
                    : fin.toString();
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY,
                    "A VENIR " + plage + " / " + liste.size().toString(),
                    Graphics.TEXT_JUSTIFY_CENTER);

        var y = 26 + hX + 10;
        for (var i = debut; i < fin; i += 1) {
            var v = liste[i];
            // Heure a gauche, delai a droite : l'heure ancre dans la
            // journee, le delai dit l'urgence. Les deux sur une ligne, le
            // libelle sur la suivante -- les libelles sont longs et
            // meritent toute la corde.
            var dispo = Pages.largeurUtile(dc, y, hX) - 16;
            var xG = w / 2.0 - dispo / 2.0;
            var xD = w / 2.0 + dispo / 2.0;

            dc.setColor(couleurUrgence(v[0], now), Graphics.COLOR_TRANSPARENT);
            dc.drawText(xG, y, Graphics.FONT_XTINY, Fmt.hour(v[0]),
                        Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(xD, y, Graphics.FONT_XTINY, Fmt.delai(v[0], now),
                        Graphics.TEXT_JUSTIFY_RIGHT);

            var yLib = y + hX - 3;
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yLib, Graphics.FONT_XTINY,
                        ajusterLibelle(dc, v, Graphics.FONT_XTINY,
                                       Pages.largeurUtile(dc, yLib, hX)),
                        Graphics.TEXT_JUSTIFY_CENTER);

            y = yLib + hX + 6;
        }
    }

    hidden function dessinerPied(dc) {
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var y = dc.getHeight() - hX - 17;
        var texte;
        var couleur = Graphics.COLOR_DK_GRAY;

        if (mErreur && mListe == null) {
            texte = "liste indisponible";
            couleur = Graphics.COLOR_RED;
        } else if (mListe == null) {
            texte = "chargement...";
        } else {
            var total = nbSousPages();
            texte = (total > 1)
                    ? (sousPage() + 1).toString() + "/" + total.toString()
                    : "12 h a venir";
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
