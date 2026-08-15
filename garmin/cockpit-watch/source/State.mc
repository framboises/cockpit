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
