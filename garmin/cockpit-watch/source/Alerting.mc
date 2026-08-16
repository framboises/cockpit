using Toybox.Application;
using Toybox.Attention;

(:background)
module Alerting {

    const KEY_WL = "lwl";
    const KEY_AL = "lal";
    const KEY_GS = "lgs";

    function reset() {
        Application.Storage.deleteValue(KEY_WL);
        Application.Storage.deleteValue(KEY_AL);
        Application.Storage.deleteValue(KEY_GS);
    }

    // Un point de guidage vibre sur tout CHANGEMENT de sequence, pas
    // seulement a la hausse -- contrairement aux seuils WBGT et alertes,
    // ou seule une degradation merite qu'on leve le poignet.
    //
    // Deux raisons. D'abord effacer un guidage puis en renvoyer un fait
    // repartir la sequence a 1 : une regle << a la hausse >> rendrait ce
    // nouveau point silencieux. Ensuite un guidage n'a pas de gravite --
    // il n'y a pas de sequence << moins grave >> qu'une autre, il y a un
    // ordre qui arrive ou pas.
    //
    // Le passage d'un point a AUCUN point (seq -> null) ne vibre pas :
    // effacer un guidage est une annulation, pas une consigne.
    function guidageChange(precedent, courant) {
        if (courant == null) {
            return false;
        }
        if (precedent == null) {
            // Premier releve apres installation ou apres vidage du cache :
            // on ne sait pas si ce point est nouveau. Vibrer ferait sonner
            // la montre au demarrage pour un ordre peut-etre vieux d'une
            // heure -- meme prudence que shouldAlert sur un niveau inconnu.
            return false;
        }
        return courant != precedent;
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

        var gs = State.guidageSeq(st);

        var lastWl = Application.Storage.getValue(KEY_WL);
        var lastAl = Application.Storage.getValue(KEY_AL);
        var lastGs = Application.Storage.getValue(KEY_GS);

        var declenche = shouldAlert(lastWl, wl) || shouldAlert(lastAl, al);
        var guidage = guidageChange(lastGs, gs);

        Application.Storage.setValue(KEY_WL, wl);
        Application.Storage.setValue(KEY_AL, al);
        Application.Storage.setValue(KEY_GS, gs);

        if (declenche) {
            buzz(wl > al ? wl : al);
        } else if (guidage) {
            // Vibration DISTINCTE de celle des alertes : un point de guidage
            // n'est pas un danger, c'est une consigne de deplacement. Deux
            // impulsions courtes plutot qu'une longue, et pas de tonalite
            // d'alerte.
            buzzGuidage();
        }
        return declenche || guidage;
    }

    // Deux impulsions courtes : la signature d'une consigne, distincte de
    // la vibration longue d'une alerte. Respecte le meme reglage utilisateur
    // (alertVibrate) -- couper les vibrations doit tout couper.
    function buzzGuidage() {
        var actif = Application.Properties.getValue("alertVibrate");
        if (actif != null && !actif) {
            return;
        }
        if (Attention has :vibrate) {
            Attention.vibrate([new Attention.VibeProfile(75, 220),
                               new Attention.VibeProfile(0, 120),
                               new Attention.VibeProfile(75, 220)]);
        }
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
