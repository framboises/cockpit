using Toybox.WatchUi;

// Menu de saut : neuf entrees, les huit pages du cycle HAUT/BAS (dans le
// meme ordre) plus "Pics par edition". MENU ouvrait autrefois EditionsView
// directement -- ce raccourci disparait au profit de ce menu, mais la vue
// reste atteignable en une seule entree (la derniere), pas nichee plus loin.
class SautMenu extends WatchUi.Menu2 {

    function initialize() {
        Menu2.initialize({:title => "Aller a"});
        addItem(new WatchUi.MenuItem("Tableau de bord", null, 0, {}));
        addItem(new WatchUi.MenuItem("Alertes", null, 1, {}));
        addItem(new WatchUi.MenuItem("Main courante", null, 2, {}));
        addItem(new WatchUi.MenuItem("Trafic", null, 3, {}));
        addItem(new WatchUi.MenuItem("Meteo", null, 4, {}));
        addItem(new WatchUi.MenuItem("Frequentation", null, 5, {}));
        addItem(new WatchUi.MenuItem("Guidage", null, 6, {}));
        addItem(new WatchUi.MenuItem("Timeline", null, 7, {}));
        addItem(new WatchUi.MenuItem("Pics par edition", null, -1, {}));
    }
}


class SautMenuDelegate extends WatchUi.Menu2InputDelegate {

    // La vue principale (CockpitView), sur laquelle on pose la page choisie.
    hidden var mView;

    function initialize(view) {
        Menu2InputDelegate.initialize();
        mView = view;
    }

    // Choisir une page pose mPage sur la vue principale et referme le menu
    // (identifiants 0..7, memes indices que CockpitView.nextPage). Passer
    // par setPage allume le GPS de la page Guidage comme le ferait
    // HAUT/BAS -- c'est setPage qui appelle entrerPage, pas le cycle. Choisir
    // les editions (identifiant -1) pousse EditionsView, exactement comme
    // MENU le faisait directement avant cette tache.
    function onSelect(item) {
        var id = item.getId();
        WatchUi.popView(WatchUi.SLIDE_IMMEDIATE);
        if (id == -1) {
            var vue = new EditionsView();
            WatchUi.pushView(vue, new EditionsDelegate(vue), WatchUi.SLIDE_UP);
        } else {
            mView.setPage(id);
        }
    }

    function onBack() {
        WatchUi.popView(WatchUi.SLIDE_DOWN);
    }
}
