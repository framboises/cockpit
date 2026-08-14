using Toybox.Application;
using Toybox.Attention;

(:background)
module Alerting {

    const KEY_WL = "lwl";
    const KEY_AL = "lal";

    function reset() {
        Application.Storage.deleteValue(KEY_WL);
        Application.Storage.deleteValue(KEY_AL);
    }

    // On ne vibre que sur franchissement a la hausse. Une redescente ou un
    // plateau reste silencieux, sinon l'alerte devient un bruit de fond qu'on
    // finit par ignorer.
    function shouldAlert(previousLevel, newLevel) {
        if (previousLevel == null) {
            return false;
        }
        if (newLevel == null) {
            return false;
        }
        return newLevel > previousLevel;
    }

    function check(st) {
        var wl = State.wbgtLevel(st);
        var al = State.alertMax(st);

        var lastWl = Application.Storage.getValue(KEY_WL);
        var lastAl = Application.Storage.getValue(KEY_AL);

        var declenche = shouldAlert(lastWl, wl) || shouldAlert(lastAl, al);

        Application.Storage.setValue(KEY_WL, wl);
        Application.Storage.setValue(KEY_AL, al);

        if (declenche) {
            buzz(wl > al ? wl : al);
        }
        return declenche;
    }

    // Usage interne. `hidden` est refuse a l'echelle module par le compilateur,
    // cette fonction est donc visible de partout ; aucun autre fichier ne doit
    // l'appeler directement.
    function buzz(level) {
        var actif = Application.Properties.getValue("alertVibrate");
        if (actif != null && !actif) {
            return;
        }
        if (Attention has :vibrate) {
            var profil = [new Attention.VibeProfile(100, level >= 3 ? 800 : 400)];
            Attention.vibrate(profil);
        }
        if (Attention has :playTone) {
            Attention.playTone(level >= 3 ? Attention.TONE_ALERT_HI
                                          : Attention.TONE_ALERT_LO);
        }
    }
}
