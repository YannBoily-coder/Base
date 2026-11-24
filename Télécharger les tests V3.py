import json
import tempfile
import unittest
from pathlib import Path

from protocole_antediluvien_v3 import (
    Etat,
    Niveau,
    Observation,
    ProtocoleAntediluvienV3,
    RegleContexte,
    TraceJSONL,
    normaliser_heure,
)


class TestsV3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.trace = Path(self.tmp.name) / "trace.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def protocole(self, regles=None):
        return ProtocoleAntediluvienV3(TraceJSONL(self.trace), regles=regles)

    def test_identique_strict_est_comparable(self):
        r = self.protocole().executer([
            Observation("x", 1, contexte={"lieu": "A"}),
            Observation("x", 1, contexte={"lieu": "A"}),
        ])
        self.assertEqual(r.etat, Etat.INCONNU)
        self.assertEqual(r.inconnues, [])

    def test_contextes_differents_sans_regle_deviennent_inconnus(self):
        r = self.protocole().executer([
            Observation("temperature", 10, contexte={"lieu": "dehors"}),
            Observation("temperature", 20, contexte={"lieu": "dedans"}),
        ])
        self.assertEqual(r.etat, Etat.INCONNU)
        self.assertTrue(any("Aucune regle" in x for x in r.inconnues))
        self.assertEqual(r.contradictions, [])

    def test_regle_prouve_contextes_differents(self):
        regle = RegleContexte(("lieu", "heure"), {"heure": normaliser_heure})
        r = self.protocole({"temperature": regle}).executer([
            Observation("temperature", 10, contexte={"lieu": "dehors", "heure": "midi"}),
            Observation("temperature", 20, contexte={"lieu": "dedans", "heure": "12:00"}),
        ])
        self.assertEqual(r.contradictions, [])
        self.assertEqual(r.inconnues, [])

    def test_synonymes_normalises_revelent_contradiction(self):
        regle = RegleContexte(("lieu", "heure"), {"heure": normaliser_heure})
        r = self.protocole({"temperature": regle}).executer([
            Observation("temperature", 10, contexte={"lieu": "dehors", "heure": "midi"}),
            Observation("temperature", 20, contexte={"lieu": "dehors", "heure": "12 h"}),
        ])
        self.assertEqual(r.etat, Etat.CONTRADICTOIRE)

    def test_champ_identitaire_manquant_suspend(self):
        regle = RegleContexte(("lieu", "heure"), {"heure": normaliser_heure})
        r = self.protocole({"temperature": regle}).executer([
            Observation("temperature", 10, contexte={"lieu": "dehors"}),
            Observation("temperature", 20, contexte={"lieu": "dehors", "heure": "12:00"}),
        ])
        self.assertEqual(r.etat, Etat.INCONNU)
        self.assertTrue(any("manquant" in x for x in r.inconnues))

    def test_niveaux_differents_ne_se_contredisent_pas(self):
        r = self.protocole().executer([
            Observation("x", 1, niveau=Niveau.OBSERVATION),
            Observation("x", 2, niveau=Niveau.HYPOTHESE),
        ])
        self.assertNotEqual(r.etat, Etat.CONTRADICTOIRE)

    def test_observations_ne_sont_pas_ecrasees(self):
        r = self.protocole().executer([
            Observation("x", 1), Observation("x", 1)
        ])
        self.assertEqual(len(r.valeur), 2)

    def test_maillon_faible_borne_confiance(self):
        r = self.protocole().executer([
            Observation("x", 1, confiance=.9), Observation("x", 1, confiance=.3)
        ])
        self.assertEqual(r.confiance, .3)

    def test_modele_en_erreur_reste_inconnu(self):
        def casse(_):
            raise RuntimeError("boom")
        r = self.protocole().executer([Observation("x", 1)], modele=casse)
        self.assertEqual(r.etat, Etat.INCONNU)

    def test_trace_detecte_alteration(self):
        p = self.protocole()
        p.executer([Observation("x", 1)])
        lignes = self.trace.read_text(encoding="utf-8").splitlines()
        evenement = json.loads(lignes[0])
        evenement["phase"] = "FALSIFIE"
        lignes[0] = json.dumps(evenement)
        self.trace.write_text("\n".join(lignes) + "\n", encoding="utf-8")
        valide, erreurs = TraceJSONL(self.trace).verifier()
        self.assertFalse(valide)
        self.assertTrue(erreurs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
