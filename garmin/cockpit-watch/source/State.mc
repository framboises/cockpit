(:glance :background)
module State {

    function alertMax(st) {
        if (st == null) {
            return 0;
        }
        var al = st["al"];
        if (al == null || al.size() == 0) {
            return 0;
        }
        var max = 0;
        for (var i = 0; i < al.size(); i += 1) {
            var lvl = al[i][0];
            if (lvl != null && lvl > max) {
                max = lvl;
            }
        }
        return max;
    }

    function wbgtLevel(st) {
        if (st == null || st["wl"] == null) {
            return 0;
        }
        return st["wl"];
    }

    // La gravite affichee est toujours le pire des deux axes : une chaleur
    // dangereuse et une alerte critique ne se compensent pas. Centralise ici
    // pour que la bande d'indicateur, la glance et la vue principale ne
    // puissent pas diverger.
    function worstLevel(st) {
        var wl = wbgtLevel(st);
        var al = alertMax(st);
        return wl > al ? wl : al;
    }

    function dataAgeSec(st, nowSec) {
        if (st == null || st["t"] == null) {
            return null;
        }
        return nowSec - st["t"];
    }

    function responseAgeSec(st, nowSec) {
        if (st == null || st["rx"] == null) {
            return null;
        }
        return nowSec - st["rx"];
    }

    // Le pire des deux ages. Une donnee fraiche recue il y a longtemps et une
    // donnee vieille recue a l'instant sont deux pannes differentes : on
    // affiche la plus grave des deux.
    function worstAgeSec(st, nowSec) {
        var a = dataAgeSec(st, nowSec);
        var b = responseAgeSec(st, nowSec);
        if (a == null) {
            return b;
        }
        if (b == null) {
            return a;
        }
        return a > b ? a : b;
    }

    // Hors evenement, le serveur repond "past" : il n'y a pas de releve en
    // cours, `t`, `e` et `er` sont nuls par construction et le payload porte
    // le pic d'une edition close.
    function isPast(st) {
        if (st == null || st["m"] == null) {
            return false;
        }
        return st["m"].equals("past");
    }

    // La CAUSE du mode passe, jamais exposee hors de ce mode : "inactif"
    // (arret volontaire du live-controle, l'etat normal 350 jours par an) et
    // "sans_releve" (le drapeau dit actif mais plus aucun releve n'arrive --
    // le collecteur est en panne) ne doivent surtout pas se peindre pareil,
    // c'est le pied de la vue principale qui en decide. `null` en mode live
    // et pour un cache anterieur a ce champ.
    function motif(st) {
        if (st == null || !isPast(st)) {
            return null;
        }
        return st["mr"];
    }

    // Les personnes presentes. Jamais un chiffre perime : le serveur ne
    // remplit `p` qu'en mode live (voir isPast), donc un accesseur simple
    // suffit ici -- pas de garde supplementaire a dupliquer.
    function presents(st) {
        if (st == null) {
            return null;
        }
        return st["p"];
    }

    function isStale(st, nowSec, staleAfter) {
        // Une edition close ne vieillit pas : son pic est definitif. Sans ce
        // court-circuit, `t` etant nul, la montre finirait par afficher
        // "perime" sur un ecran dont TOUTE la donnee est figee par nature --
        // un avertissement auquel aucune action ne peut repondre, et qui
        // serait permanent hors evenement, c'est-a-dire l'essentiel de
        // l'annee. Le mode repasse a "live" des la premiere reponse ou un
        // evenement tourne.
        if (isPast(st)) {
            return false;
        }
        var age = worstAgeSec(st, nowSec);
        if (age == null) {
            return true;
        }
        return age > staleAfter;
    }
}
