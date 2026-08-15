using Toybox.Lang;

(:glance :background)
module Fmt {

    const DASH = "--";

    // Espace insecable fine entre milliers : un compteur a six chiffres est
    // illisible d'un coup d'oeil sans separation.
    function count(n) {
        if (n == null) {
            return DASH;
        }
        var s = n.toString();
        var out = "";
        var c = 0;
        for (var i = s.length() - 1; i >= 0; i -= 1) {
            out = s.substring(i, i + 1) + out;
            c += 1;
            if (c % 3 == 0 && i > 0) {
                out = " " + out;
            }
        }
        return out;
    }

    function rate(n) {
        if (n == null) {
            return DASH;
        }
        return n.toString();
    }

    function wbgt(w) {
        if (w == null) {
            return DASH;
        }
        return w.format("%.1f");
    }

    function age(sec) {
        if (sec == null) {
            return DASH;
        }
        if (sec < 0) {
            sec = 0;
        }
        if (sec < 60) {
            return sec.toString() + " s";
        }
        if (sec < 3600) {
            return (sec / 60).toString() + " min";
        }
        // Au-dela de deux jours, les heures ne se lisent plus : un releve vieux
        // de 113 jours s'affichait "2725 h", chiffre qu'il faut diviser de tete
        // pour comprendre, et qui deborde de la largeur utile sur l'ecran rond.
        if (sec < 172800) {
            return (sec / 3600).toString() + " h";
        }
        return (sec / 86400).toString() + " j";
    }
}
