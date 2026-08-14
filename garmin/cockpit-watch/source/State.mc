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

    function isStale(st, nowSec, staleAfter) {
        var age = worstAgeSec(st, nowSec);
        if (age == null) {
            return true;
        }
        return age > staleAfter;
    }
}
