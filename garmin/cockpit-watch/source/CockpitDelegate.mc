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
        mView.nextPage();
        return true;
    }
}
