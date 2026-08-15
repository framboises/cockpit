using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Application;
using Toybox.Timer;
using Toybox.Time;
using Toybox.Math;

class CockpitView extends WatchUi.View {

    hidden var mState = null;
    hidden var mTimer = null;
    hidden var mPage = 0;
    hidden var mPeriod = 0;

    // Les quatre pages annexes sont dessinees DANS cette vue (onUpdate
    // appelle directement leur onUpdate(dc), deja autonome -- chacune gere
    // deja son propre clear/couleurs et lit son bloc dans Cache/Pages) plutot
    // que poussees sur la pile : HAUT/BAS reste un simple changement de page,
    // sans empiler de vues. Creees une fois ici (le cout, notamment le
    // chargement des icones de MainCouranteView, ne doit pas se repeter a
    // chaque page suivante).
    hidden var mMainCourante;
    hidden var mTrafic;
    hidden var mMeteo;
    hidden var mFrequentation;

    function initialize() {
        View.initialize();
        mMainCourante = new MainCouranteView();
        mTrafic = new TraficView();
        mMeteo = new MeteoView();
        mFrequentation = new FrequentationView();
    }

    function onLayout(dc) {
    }

    function onShow() {
        refresh();
        mPeriod = periodMs();
        mTimer = new Timer.Timer();
        mTimer.start(method(:onTick), mPeriod, true);
    }

    function onHide() {
        if (mTimer != null) {
            mTimer.stop();
            mTimer = null;
        }
    }

    function onTick() as Void {
        refresh();
    }

    function periodMs() {
        var peak = Application.Properties.getValue("pollPeak");
        var normal = Application.Properties.getValue("pollNormal");
        if (peak == null) { peak = 60; }
        if (normal == null) { normal = 180; }
        // On resserre le rythme des que ca chauffe, sans reglage manuel.
        if (State.worstLevel(mState) >= 2) {
            return peak * 1000;
        }
        return normal * 1000;
    }

    function refresh() {
        Api.fetch(method(:onFetched));
    }

    function onFetched(ok, st) {
        if (ok && st != null) {
            Cache.save(st);
            // Les deux chemins (app et fond) ecrivent le meme cache, donc le
            // controle de transition vit dans Alerting, partage : le premier
            // qui ecrit deplace la reference, il n'y a pas de double
            // vibration. Sans cet appel ici, un franchissement de seuil
            // pendant que l'app est ouverte au poignet ne vibrait jamais --
            // il fallait attendre le prochain cycle de fond (jusqu'a 5 min),
            // et l'alerte etait perdue si le niveau redescendait entre-temps.
            Alerting.check(st);
        }
        // En cas d'echec on garde le cache : l'age affiche dira lui-meme
        // depuis combien de temps la donnee n'a pas bouge.
        mState = Cache.load();
        // Reajuste le rythme si le niveau d'alerte ou le WBGT a franchi le
        // seuil pendant que la vue est affichee : sans ca, le polling reste
        // bloque sur la valeur armee a l'ouverture de la vue.
        if (mTimer != null) {
            var p = periodMs();
            if (p != mPeriod) {
                mTimer.stop();
                mTimer.start(method(:onTick), p, true);
                mPeriod = p;
            }
        }
        WatchUi.requestUpdate();
    }

    // Six pages en cycle, dans l'ordre d'urgence operationnelle : tableau de
    // bord, alertes, main courante, trafic, meteo, frequentation.
    function nextPage() {
        mPage = (mPage + 1) % 6;
        WatchUi.requestUpdate();
    }

    // Symetrique de nextPage : + 5 plutot que - 1 pour ne jamais produire de
    // modulo negatif (Monkey C, comme beaucoup de langages, ne garantit pas
    // qu'un modulo d'operande negatif reste positif).
    function previousPage() {
        mPage = (mPage + 5) % 6;
        WatchUi.requestUpdate();
    }

    // Saut direct depuis le menu (SautMenuDelegate) : pose la page courante
    // sans passer par le cycle.
    function setPage(n) {
        mPage = n;
        WatchUi.requestUpdate();
    }

    // Public pour rester testable en VALEUR (DessinTest.mc verifie que
    // nextPage boucle bien sur 6 et revient a 0), pas seulement en absence
    // d'exception.
    function currentPage() {
        return mPage;
    }

    // Table page -> vue annexe pour les pages 2..5, seule source de verite
    // pour l'aiguillage (onUpdate l'appelle, ne duplique pas le mapping).
    // Publique (comme FrequentationView.calculDeltaPct) pour rester
    // testable en VALEUR : un swap accidentel entre deux pages voisines
    // (ex: meteo/frequentation) ne leverait aucune exception -- seule une
    // verification par instanceof le detecte.
    function pageView(n) {
        if (n == 2) { return mMainCourante; }
        if (n == 3) { return mTrafic; }
        if (n == 4) { return mMeteo; }
        return mFrequentation;
    }

    function onUpdate(dc) {
        if (mPage == 0) {
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
            dc.clear();
            drawMain(dc);
        } else if (mPage == 1) {
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
            dc.clear();
            drawAlerts(dc);
        } else {
            pageView(mPage).onUpdate(dc);
        }
    }

    hidden function levelColor(level) {
        if (level >= 3) { return Graphics.COLOR_RED; }
        if (level >= 2) { return Graphics.COLOR_ORANGE; }
        if (level >= 1) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    // Largeur utile de l'ecran rond a la hauteur y. Sur un cadran circulaire, la
    // place disponible depend de l'eloignement au centre : un texte qui tient au
    // milieu deborde en haut ou en bas.
    hidden function largeurUtile(dc, y) {
        var r = dc.getWidth() / 2.0;
        var dy = (y - r).abs();
        if (dy >= r) {
            return 0.0;
        }
        return 2.0 * Math.sqrt(r * r - dy * dy);
    }

    hidden function drawMain(dc) {
        var w = dc.getWidth();
        var st = mState;
        var now = Time.now().value();

        // drawText ancre par le haut (pas de TEXT_JUSTIFY_VCENTER) : un bloc
        // pose a y occupe y -> y + hauteur police. Les positions sont donc
        // calculees a partir des hauteurs reelles du device, pas figees en
        // dur, pour survivre a un changement de police ou de device.
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);
        var hN = dc.getFontHeight(Graphics.FONT_NUMBER_MEDIUM);

        var al = (st != null && st["al"] != null) ? st["al"] : [];

        // Evenement rapporte, en haut : sans lui, une configuration epinglee
        // sur le mauvais evenement serait invisible. Le compte d'alertes y
        // est accroche pour rester visible malgre la troncature a deux
        // lignes plus bas.
        var label = (st != null && st["n"] != null) ? st["n"] : "--";
        var y = 24;
        var n = (st != null && st["al"] != null) ? st["al"].size() : 0;
        if (n > 0) {
            var longue = label + " . " + n.toString() + (n > 1 ? " alertes" : " alerte");
            var courte = label + " . " + n.toString();
            // Le bloc est dans la moitie haute : c'est son sommet qui contraint.
            var dispo = largeurUtile(dc, y);
            if (dc.getTextWidthInPixels(longue, Graphics.FONT_XTINY) <= dispo) {
                label = longue;
            } else if (dc.getTextWidthInPixels(courte, Graphics.FONT_XTINY) <= dispo) {
                label = courte;
            }
            // Si meme la forme courte ne tient pas, on garde le libelle seul :
            // mieux vaut perdre le compte que rendre le nom de l'evenement
            // illisible, c'est lui qui revele une configuration epinglee sur
            // le mauvais evenement.
        }
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY, label,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Le chiffre principal change de nature selon le mode, et sa
        // sous-ligne le nomme. En direct, ce sont les PRESENTS qui montent
        // au sommet -- pas le cumul d'entrees, qui ne redescend jamais et
        // annoncerait encore 130 000 a 23 h quand il ne reste que 40 000
        // personnes. Le cumul et son debit ne disparaissent pas : ils
        // descendent en sous-ligne. Hors evenement, c'est le pic de
        // l'edition rapportee et son instant qui prennent leur place.
        var passe = State.isPast(st);
        var chiffre = passe ? (st != null ? st["pk"] : null)
                            : State.presents(st);
        var sousLigne;
        if (passe) {
            var quand = (st != null) ? st["pkt"] : null;
            sousLigne = (quand != null)
                        ? ("pic " + Fmt.day(quand) + " " + Fmt.hour(quand))
                        : "pic";
        } else {
            sousLigne = Fmt.count(st != null ? st["e"] : null) + " entrees - "
                        + Fmt.rate(st != null ? st["er"] : null) + " pers/h";
        }

        y = y + hX + 4;
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_NUMBER_MEDIUM, Fmt.count(chiffre),
                    Graphics.TEXT_JUSTIFY_CENTER);

        y = y + hN + 2;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY, sousLigne,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // WBGT, colore par son niveau.
        y = y + hX + 5;
        var wl = State.wbgtLevel(st);
        dc.setColor(levelColor(wl), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_MEDIUM,
                    "WBGT " + Fmt.wbgt(st != null ? st["w"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);

        y = y + hM + 4;

        // Bande de voyants : uniquement s'il y a quelque chose a signaler
        // (fiches PC org en instance, ou trafic sorti du vert). L'absence
        // dit "rien a signaler" mieux qu'une ligne de zeros, et une page qui
        // doit se lire en deux secondes ne doit pas en etre encombree --
        // elle ne consomme donc AUCUNE hauteur quand tout est calme.
        var pg = Cache.loadPages();
        var mc = Pages.bloc(pg, "mc");
        var tr = Pages.bloc(pg, "tr");
        var vd = (tr != null) ? tr["vd"] : null;
        var bouts = [];
        if (mc != null) {
            var enCours = mc["s"][0] + mc["sc"][0] + mc["tq"][0] + mc["f"][0]
                          + mc["o"][0];
            if (enCours > 0) { bouts.add("MC " + enCours.toString()); }
        }
        if (vd != null && vd >= 1) {
            bouts.add(Pages.verdictMot(vd));
        }
        var nBouts = bouts.size();
        if (nBouts > 0) {
            var texteVoyants = "";
            for (var iv = 0; iv < nBouts; iv += 1) {
                if (iv > 0) { texteVoyants += "   "; }
                texteVoyants += bouts[iv];
            }
            // Coloree par le pire des deux axes. Un trafic degrade est un
            // signal de gravite connue (echelle 0-3 du mur) ; des fiches en
            // instance seules n'en portent aucune -- gris neutre plutot que
            // le vert de "fluide", qui laisserait croire a un satisfecit.
            var couleurVoyants = (vd != null && vd >= 1) ? levelColor(vd)
                                                          : Graphics.COLOR_LT_GRAY;
            dc.setColor(couleurVoyants, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, texteVoyants,
                        Graphics.TEXT_JUSTIFY_CENTER);
            y = y + hX + 2;
        }

        // Alertes : deux lignes au plus, le reste sur la seconde page.
        if (al.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "RAS",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var nShow = al.size() > 2 ? 2 : al.size();
            for (var i = 0; i < nShow; i += 1) {
                dc.setColor(levelColor(al[i][0]), Graphics.COLOR_TRANSPARENT);
                dc.drawText(w / 2, y, Graphics.FONT_XTINY, al[i][1],
                            Graphics.TEXT_JUSTIFY_CENTER);
                y += hX + 2;
            }
        }

        // Pied de page, positionne depuis le bas de l'ecran (rond : la corde
        // disponible se retrecit pres du bord, d'ou des textes courts).
        var staleAfter = Application.Properties.getValue("staleAfter");
        if (staleAfter == null) { staleAfter = 90; }
        var age = State.worstAgeSec(st, now);

        if (passe) {
            // Le pied nomme la CAUSE du mode passe, pas seulement l'etat :
            // "inactif" (live-controle arrete sur le cockpit, l'etat normal
            // 350 jours par an) et "sans_releve" (le drapeau dit actif mais
            // plus aucun releve n'arrive -- le collecteur est en panne) se
            // confondaient jusqu'ici sous un seul "edition terminee". Les
            // peindre pareil perdrait l'information la plus utile : celle
            // qui dit si une intervention est necessaire.
            var mr = State.motif(st);
            var texte = "hors evenement";
            var couleur = Graphics.COLOR_DK_GRAY;
            if (mr != null && mr.equals("inactif")) {
                texte = "live inactif";
            } else if (mr != null && mr.equals("sans_releve")) {
                texte = "aucun releve";
                couleur = Graphics.COLOR_RED;
            }
            dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() - hX - 17, Graphics.FONT_XTINY,
                        texte, Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }
        // "compteur" et pas "perime" : le payload ne porte qu'un horodatage, `t`,
        // celui du releve Skidata. Le WBGT du creneau courant peut etre frais
        // alors que le compteur date de plusieurs mois — et l'inverse est plus
        // grave : pendant un evenement le compteur se met a jour en permanence,
        // donc rien n'afficherait "perime" meme si le flux meteo s'etait fige.
        // Nommer ce qui vieillit vaut mieux qu'un "perime" qui parait tout couvrir.
        var stale = State.isStale(st, now, staleAfter);
        dc.setColor(stale ? Graphics.COLOR_RED : Graphics.COLOR_DK_GRAY,
                    Graphics.COLOR_TRANSPARENT);
        var foot = "compteur " + Fmt.age(age);
        dc.drawText(w / 2, dc.getHeight() - hX - 17, Graphics.FONT_XTINY, foot,
                    Graphics.TEXT_JUSTIFY_CENTER);
    }

    hidden function drawAlerts(dc) {
        var w = dc.getWidth();
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 30, Graphics.FONT_XTINY, "ALERTES",
                    Graphics.TEXT_JUSTIFY_CENTER);
        var al = (mState != null && mState["al"] != null) ? mState["al"] : [];
        if (al.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, 130, Graphics.FONT_SMALL, "RAS",
                        Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }
        var y = 66;
        for (var i = 0; i < al.size(); i += 1) {
            dc.setColor(levelColor(al[i][0]), Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, al[i][1],
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += 30;
        }
    }
}
