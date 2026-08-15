"""Double Mongo partage par les tests montre.

Volontairement minimal : il n'implemente que ce que les modules testes
appellent reellement. Sa regle de conduite est de casser bruyamment plutot
que de laisser passer -- un double trop permissif rend les tests
tautologiques, ce qui est pire que pas de test du tout.
"""

from datetime import datetime, timezone

_OPERATEURS_CONNUS = {"$lte", "$lt", "$gt", "$gte", "$in", "$ne"}


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
        return list(self._matching(query))

    def count_documents(self, query=None):
        return len(self._matching(query))

    def create_index(self, keys, **kwargs):
        self.indexes.append((tuple(keys), kwargs.get("name")))
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
            return True
        return actuel == val


class FakeDb:
    def __init__(self, **collections):
        self._cols = {k: FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection([])
        return self._cols[name]

    def list_collection_names(self):
        return list(self._cols)
