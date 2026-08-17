using Toybox.Application;
using Toybox.WatchUi;
using Toybox.Background;
using Toybox.Time;

class CockpitApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state) {
        // ⚠️ APPLICATION.STORAGE SURVIT AU SIDELOAD. Reinstaller l'app ne
        // vide pas son stockage : un code d'erreur memorise lors d'un essai
        // ANTERIEUR -- avec un autre jeton, une autre version -- reste en
        // place, et n'etait efface qu'apres une requete REUSSIE.
        //
        // Consequence observee : "jeton refuse" restait affiche apres
        // quatre reconstructions successives, alors que le jeton compile
        // etait accepte par le serveur (HTTP 200 verifie) et bien present
        // dans le binaire. Le message ne venait pas du serveur, il venait
        // du stockage.
        //
        // Au demarrage, la montre ne sait RIEN de son lien au serveur.
        // Pretendre le contraire est faux, et c'est exactement le principe
        // deja applique par Alerting (pas de vibration sans reference
        // anterieure). L'erreur se reconstruira d'elle-meme au premier
        // echange rate, quelques secondes plus tard.
        Api.oublierErreur();

        // 5 minutes est le plancher impose par la plateforme, pas un choix :
        // "Temporal events cannot be set to occur less than 5 minutes after
        // the last temporal event occurred". Un seul evenement temporel peut
        // etre enregistre a la fois.
        if (Toybox has :Background) {
            Background.registerForTemporalEvent(new Time.Duration(300));
        }
    }

    function onStop(state) {
    }

    function getInitialView() {
        var view = new CockpitView();
        return [view, new CockpitDelegate(view)];
    }

    (:glance)
    function getGlanceView() {
        return [new CockpitGlanceView()];
    }

    // La bande verticale que le systeme dessine a gauche de la glance. La
    // colorer coute zero pixel dessine et donne le niveau au premier regard.
    //
    // La fraicheur passe avant le niveau : une donnee perimee (telephone
    // hors de portee, jeton revoque, certificat expire) rend le niveau
    // affiche non fiable -- une bande verte dessus etait un mensonge, pas
    // une absence d'alerte. `staleAfter` reprend le meme repli a 90 s que
    // CockpitView et GlanceView.
    (:glance)
    function getGlanceTheme() {
        var st = Cache.load();
        var staleAfter = Application.Properties.getValue("staleAfter");
        if (staleAfter == null) { staleAfter = 90; }
        if (State.isStale(st, Time.now().value(), staleAfter)) {
            return AppBase.GLANCE_THEME_GOLD;
        }
        var worst = State.worstLevel(st);
        if (worst >= 3) {
            return AppBase.GLANCE_THEME_RED;
        }
        if (worst >= 2) {
            return AppBase.GLANCE_THEME_GOLD;
        }
        return AppBase.GLANCE_THEME_GREEN;
    }

    function getServiceDelegate() {
        return [new CockpitService()];
    }
}
