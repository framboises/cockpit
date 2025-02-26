L.drawLocal = {
    draw: {
        toolbar: {
            actions: {
                title: 'Annuler le dessin',
                text: 'Annuler'
            },
            finish: {
                title: 'Terminer le dessin',
                text: 'Terminer'
            },
            undo: {
                title: 'Supprimer le dernier point',
                text: 'Supprimer le dernier point'
            },
            buttons: {
                polyline: 'Dessiner une ligne',
                polygon: 'Dessiner un polygone',
                rectangle: 'Dessiner un rectangle',
                circle: 'Dessiner un cercle',
                marker: 'Placer un marqueur',
                circlemarker: 'Placer un cercle marqueur'
            }
        },
        handlers: {
            circle: {
                tooltip: {
                    start: 'Cliquez et maintenez pour dessiner le cercle.'
                },
                radius: 'Rayon'
            },
            circlemarker: {
                tooltip: {
                    start: 'Cliquez sur la carte pour placer un cercle marqueur.'
                }
            },
            polygon: {
                tooltip: {
                    start: 'Cliquez pour commencer à dessiner la forme.',
                    cont: 'Cliquez pour continuer à dessiner la forme.',
                    end: 'Cliquez sur le premier point pour fermer cette forme.'
                }
            },
            polyline: {
                tooltip: {
                    start: 'Cliquez pour commencer à dessiner une ligne.',
                    cont: 'Cliquez pour continuer à dessiner la ligne.',
                    end: 'Cliquez sur le dernier point pour terminer la ligne.'
                }
            },
            rectangle: {
                tooltip: {
                    start: 'Cliquez et faites glisser pour dessiner un rectangle.'
                }
            },
            marker: {
                tooltip: {
                    start: 'Cliquez sur la carte pour placer un marqueur.'
                }
            },
            simpleshape: {
                tooltip: {
                    end: 'Relâchez la souris pour terminer le dessin.'
                }
            }
        }
    },
    edit: {
        toolbar: {
            actions: {
                save: {
                    title: 'Enregistrer les modifications',
                    text: 'Enregistrer'
                },
                cancel: {
                    title: 'Annuler les modifications',
                    text: 'Annuler'
                },
                clearAll: {
                    title: 'Tout effacer',
                    text: 'Tout effacer'
                }
            },
            buttons: {
                edit: 'Modifier les polygones',
                editDisabled: 'Aucun polygone à modifier',
                remove: 'Supprimer les polygones',
                removeDisabled: 'Aucun polygone à supprimer'
            }
        },
        handlers: {
            edit: {
                tooltip: {
                    text: 'Faites glisser les poignées ou un marqueur pour modifier la forme.',
                    subtext: 'Cliquez sur Annuler pour annuler les changements.'
                }
            },
            remove: {
                tooltip: {
                    text: 'Cliquez sur un polygone ou une ligne pour les supprimer.'
                }
            }
        }
    }
};