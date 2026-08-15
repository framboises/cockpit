using Toybox.WatchUi;

class CockpitDelegate extends WatchUi.BehaviorDelegate {

    hidden var mView;

    function initialize(view) {
        BehaviorDelegate.initialize();
        mView = view;
    }

    // ENTER force un rafraichissement immediat.
    function onSelect() {
        mView.refresh();
        return true;
    }

    // Page suivante : la liste complete des alertes.
    function onNextPage() {
        mView.nextPage();
        return true;
    }

    function onPreviousPage() {
        mView.previousPage();
        return true;
    }

    // MENU ouvre le menu de saut (SautMenu) : les six pages du cycle, plus
    // "Pics par edition" -- qui reste donc atteignable, en une seule entree,
    // malgre la disparition du raccourci direct qu'etait ce geste avant
    // cette tache.
    function onMenu() {
        var menu = new SautMenu();
        WatchUi.pushView(menu, new SautMenuDelegate(mView), WatchUi.SLIDE_UP);
        return true;
    }
}
