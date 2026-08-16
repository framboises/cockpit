"""Double Mongo partage par les tests montre.

Volontairement minimal : il n'implemente que ce que les modules testes
appellent reellement. Sa regle de conduite est de casser bruyamment plutot
que de laisser passer -- un double trop permissif rend les tests
tautologiques, ce qui est pire que pas de test du tout.
"""

import re
from datetime import datetime, timezone

_OPERATEURS_CONNUS = {"$lte", "$lt", "$gt", "$gte", "$in", "$ne", "$regex"}


class FakeCursor:
    """Ce que rend find() : iterable et triable, comme un curseur pymongo.

    find() rendait une liste nue. Un appelant qui ecrit
    find(...).sort("echeance_min", 1) -- ce que fait meteo_etat pour ordonner
    les echeances PIAF -- tombait donc sur un AttributeError, et ce chemin
    restait intestable. Le tri porte la signature pymongo (cle, sens), pas
    celle de list.sort : c'est un curseur, pas une liste.
    """

    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, cle, sens=1):
        # Trier sur une cle qu'un document ne porte pas rendrait un ordre
        # arbitraire : le test passerait avec le code correct comme avec le
        # code fautif. On leve plutot que de mentir sur l'ordre.
        sans_cle = [d for d in self.docs if cle not in d]
        if sans_cle:
            raise NotImplementedError(
                "FakeCursor ne sait pas trier sur %r : %d document(s) ne "
                "portent pas cette cle" % (cle, len(sans_cle)))
        self.docs.sort(key=lambda d: d[cle], reverse=(sens == -1))
        return self

    def __iter__(self):
        return iter(self.docs)


class _ResultatSuppression:
    """Ce que pymongo rend sur delete_one/delete_many : un objet portant
    `deleted_count`, pas un entier nu."""

    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class FakeCollection:
    """Collection Mongo minimale : juste ce que le code teste appelle."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.last_query = None
        self.indexes = []

    def find_one(self, query=None, projection=None, sort=None):
        self.last_query = query
        docs = self._matching(query)
        if sort:
            cle, sens = sort[0]
            docs = sorted(docs, key=lambda d: d.get(cle),
                          reverse=(sens == -1))
        return docs[0] if docs else None

    def find(self, query=None, projection=None):
        self.last_query = query
        return FakeCursor(self._matching(query))

    def count_documents(self, query=None):
        return len(self._matching(query))

    def create_index(self, keys, **kwargs):
        # Les kwargs sont conserves ENTIERS (et plus seulement `name`) :
        # `unique` est ce qui garantit qu'un second envoi de guidage
        # remplace le premier au lieu d'en creer un doublon. Ne garder que
        # le nom rendait cette garantie invérifiable, donc son test
        # tautologique.
        self.indexes.append((keys, dict(kwargs)))
        return kwargs.get("name")

    def update_one(self, filtre, update, upsert=False):
        cible = self.find_one(filtre)
        valeurs = update.get("$set", {})
        if cible is not None:
            cible.update(valeurs)
        elif upsert:
            nouveau = dict(filtre)
            nouveau.update(valeurs)
            self.docs.append(nouveau)

    def delete_many(self, query=None):
        restants = [d for d in self.docs if d not in self._matching(query)]
        self.docs = restants

    def delete_one(self, query=None):
        """Supprime AU PLUS un document, et rend son compte.

        Le compte est ce que lit l'appelant (watch_guidage.clear_point rend
        True/False dessus) : le rendre a None ferait passer un << rien a
        effacer >> pour un << efface >>.
        """
        cible = self.find_one(query)
        if cible is None:
            return _ResultatSuppression(0)
        self.docs = [d for d in self.docs if d is not cible]
        return _ResultatSuppression(1)

    def find_one_and_update(self, filtre, update, upsert=False,
                            return_document=None):
        """$set et $inc, avec upsert. Rend le document APRES modification.

        `$inc` n'est pas du sucre ici : c'est lui qui porte le compteur de
        sequence du guidage, donc la vibration cote montre. Un double qui
        l'ignorerait en silence laisserait passer un compteur fige --
        exactement la classe de bug que le faux `$gte` de ce fichier avait
        deja produite. Tout operateur non reconnu leve, plutot que d'etre
        ignore : un test tautologique est pire qu'une absence de test.
        """
        inconnus = set(update.keys()) - {"$set", "$inc"}
        if inconnus:
            raise NotImplementedError(
                "operateurs non geres par le double : %s" % sorted(inconnus))

        cible = self.find_one(filtre)
        if cible is None:
            if not upsert:
                return None
            cible = dict(filtre)
            self.docs.append(cible)

        cible.update(update.get("$set", {}))
        for cle, pas in (update.get("$inc") or {}).items():
            cible[cle] = (cible.get(cle) or 0) + pas
        return cible

    def aggregate(self, pipeline):
        """Pipeline minimal : seuls $match et $group sont reconnus, parce
        que c'est tout ce que watch_pages appelle reellement. Tout etage
        non reconnu leve -- l'ignorer rendrait le test tautologique, comme
        pour _match et FakeCursor.sort."""
        docs = list(self.docs)
        for etage in pipeline:
            if list(etage.keys()) == ["$match"]:
                spec = etage["$match"]
                docs = [d for d in docs
                        if all(self._match(d, cle, val)
                               for cle, val in spec.items())]
            elif list(etage.keys()) == ["$group"]:
                docs = self._aggregate_group(docs, etage["$group"])
            else:
                raise NotImplementedError(
                    "FakeCollection.aggregate ne sait pas traiter l'etage "
                    "%r" % etage)
        return docs

    @classmethod
    def _eval_expr(cls, doc, expr):
        """Evalue une expression d'agregation minimale : reference de champ
        ('$champ') ou operateur {'$eq': [a, b]}. Une expression non reconnue
        leve plutot que de rendre une valeur plausible mais fausse."""
        if isinstance(expr, str) and expr.startswith("$"):
            return doc.get(expr[1:])
        if isinstance(expr, dict):
            if list(expr.keys()) == ["$eq"]:
                gauche, droite = expr["$eq"]
                return (cls._eval_expr(doc, gauche)
                        == cls._eval_expr(doc, droite))
            raise NotImplementedError(
                "FakeCollection.aggregate ne sait pas evaluer %r" % expr)
        return expr

    @classmethod
    def _aggregate_group(cls, docs, spec):
        id_spec = spec.get("_id")
        accum_specs = {cle: val for cle, val in spec.items() if cle != "_id"}
        for accum_expr in accum_specs.values():
            if list(accum_expr.keys()) != ["$sum"] or accum_expr["$sum"] != 1:
                # Seul $sum: 1 (compter des documents) est implemente : rien
                # d'autre n'est appele par le code teste a ce jour.
                raise NotImplementedError(
                    "FakeCollection.aggregate ne sait pas accumuler %r"
                    % accum_expr)

        groupes = {}
        ordre = []
        for doc in docs:
            if isinstance(id_spec, dict):
                valeur_id = {cle: cls._eval_expr(doc, expr)
                             for cle, expr in id_spec.items()}
                cle_groupe = tuple(sorted(valeur_id.items()))
            else:
                valeur_id = cls._eval_expr(doc, id_spec)
                cle_groupe = valeur_id
            if cle_groupe not in groupes:
                groupes[cle_groupe] = {"_id": valeur_id}
                groupes[cle_groupe].update(
                    {cle: 0 for cle in accum_specs})
                ordre.append(cle_groupe)
            for cle in accum_specs:
                groupes[cle_groupe][cle] += 1
        return [groupes[cle_groupe] for cle_groupe in ordre]

    def _matching(self, query):
        if not query:
            return list(self.docs)
        out = []
        for doc in self.docs:
            if all(self._match(doc, cle, val) for cle, val in query.items()):
                out.append(doc)
        return out

    @staticmethod
    def _as_comparable_utc(value):
        """Aligne un datetime naif sur la convention pymongo : un client non
        tz_aware stocke un aware en UTC et le relit naif-UTC. Comparer un
        conscient a un naif leve un TypeError en Python pur, alors que
        MongoDB compare les deux sans broncher (BSON normalise tout en UTC
        sur le fil). Sans cette normalisation, un test qui reproduit
        fidelement ce que pymongo rend (naif) contre un `now_utc` conscient
        casserait le double a cause du Fake, pas du code teste."""
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @classmethod
    def _match(cls, doc, cle, val):
        actuel = cls._as_comparable_utc(doc.get(cle))
        if isinstance(val, dict):
            # Un operateur inconnu laisserait passer le document en silence :
            # le filtre serait ignore et le test passerait autant avec le code
            # correct qu'avec le code fautif. On leve plutot que de mentir.
            inconnus = set(val) - _OPERATEURS_CONNUS
            if inconnus:
                raise NotImplementedError(
                    "FakeCollection ne sait pas filtrer %s" % sorted(inconnus))
            if "$lte" in val and not (
                    actuel is not None
                    and actuel <= cls._as_comparable_utc(val["$lte"])):
                return False
            if "$lt" in val and not (
                    actuel is not None
                    and actuel < cls._as_comparable_utc(val["$lt"])):
                return False
            if "$gt" in val and not (
                    actuel is not None
                    and actuel > cls._as_comparable_utc(val["$gt"])):
                return False
            if "$gte" in val and not (
                    actuel is not None
                    and actuel >= cls._as_comparable_utc(val["$gte"])):
                return False
            if "$in" in val and actuel not in val["$in"]:
                return False
            if "$ne" in val and actuel == val["$ne"]:
                return False
            if "$regex" in val and not (
                    isinstance(actuel, str)
                    and re.search(val["$regex"], actuel)):
                return False
            return True
        # Les deux cotes passent par la normalisation, pas seulement le
        # document : les operateurs le faisaient deja, l'egalite simple non.
        # Un filtre {"run_at": <datetime naif>} -- ce que fait meteo_etat pour
        # relire les echeances du dernier run PIAF -- ne trouvait donc jamais
        # le document dont la valeur venait pourtant d'etre lue, l'un des
        # cotes etant rendu conscient et l'autre non.
        return actuel == cls._as_comparable_utc(val)


class FakeDb:
    def __init__(self, **collections):
        self._cols = {k: FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection([])
        return self._cols[name]

    def list_collection_names(self):
        return list(self._cols)
