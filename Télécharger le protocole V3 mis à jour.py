from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


class Etat(str, Enum):
    VRAI = "VRAI"
    FAUX = "FAUX"
    INCONNU = "INCONNU"
    CONTRADICTOIRE = "CONTRADICTOIRE"


class Niveau(str, Enum):
    OBSERVATION = "OBSERVATION"
    INTERPRETATION = "INTERPRETATION"
    HYPOTHESE = "HYPOTHESE"
    CONCLUSION = "CONCLUSION"


class Comparabilite(str, Enum):
    MEME_CONTEXTE = "MEME_CONTEXTE"
    CONTEXTES_DIFFERENTS = "CONTEXTES_DIFFERENTS"
    CONTEXTE_INDETERMINABLE = "CONTEXTE_INDETERMINABLE"


class StatutBreche(str, Enum):
    OUVERTE = "OUVERTE"
    SURVEILLEE = "SURVEILLEE"
    CORRIGEE = "CORRIGEE"
    ACCEPTEE = "ACCEPTEE"


class SeveriteBreche(str, Enum):
    FAIBLE = "FAIBLE"
    MOYENNE = "MOYENNE"
    HAUTE = "HAUTE"
    CRITIQUE = "CRITIQUE"


@dataclass
class Breche:
    id: str
    empreinte: str
    categorie: str
    titre: str
    description: str
    statut: StatutBreche
    severite: SeveriteBreche
    premiere_detection_utc: str
    derniere_detection_utc: str
    occurrences: int = 1
    trace_ids: list[str] = field(default_factory=list)
    preuves: list[dict[str, Any]] = field(default_factory=list)
    test_non_regression: Optional[str] = None
    resolution: Optional[str] = None


class RegistreBreches:
    """Registre persistant, deduplique par empreinte semantique stable."""

    def __init__(self, chemin: str | Path) -> None:
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        if not self.chemin.exists():
            self._sauver([])

    @staticmethod
    def _empreinte(categorie: str, titre: str, cle: Optional[str]) -> str:
        charge = json.dumps(
            {"categorie": categorie, "titre": titre, "cle": cle},
            sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(charge.encode()).hexdigest()

    def _charger(self) -> list[dict[str, Any]]:
        try:
            contenu = json.loads(self.chemin.read_text(encoding="utf-8"))
            return contenu if isinstance(contenu, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _sauver(self, elements: list[dict[str, Any]]) -> None:
        temporaire = self.chemin.with_suffix(self.chemin.suffix + ".tmp")
        temporaire.write_text(
            json.dumps(elements, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        temporaire.replace(self.chemin)

    def enregistrer(
        self, *, categorie: str, titre: str, description: str, trace_id: str,
        severite: SeveriteBreche = SeveriteBreche.MOYENNE,
        cle: Optional[str] = None, preuve: Optional[dict[str, Any]] = None,
        test_non_regression: Optional[str] = None,
    ) -> Breche:
        maintenant = datetime.now(timezone.utc).isoformat()
        empreinte = self._empreinte(categorie, titre, cle)
        elements = self._charger()
        for element in elements:
            if element.get("empreinte") == empreinte:
                element["derniere_detection_utc"] = maintenant
                element["occurrences"] = int(element.get("occurrences", 0)) + 1
                if trace_id not in element.setdefault("trace_ids", []):
                    element["trace_ids"].append(trace_id)
                if preuve is not None:
                    element.setdefault("preuves", []).append(preuve)
                if test_non_regression and not element.get("test_non_regression"):
                    element["test_non_regression"] = test_non_regression
                self._sauver(elements)
                return Breche(**element)

        breche = Breche(
            id=str(uuid.uuid4()), empreinte=empreinte, categorie=categorie,
            titre=titre, description=description, statut=StatutBreche.OUVERTE,
            severite=severite, premiere_detection_utc=maintenant,
            derniere_detection_utc=maintenant, trace_ids=[trace_id],
            preuves=[preuve] if preuve is not None else [],
            test_non_regression=test_non_regression,
        )
        elements.append(asdict(breche))
        self._sauver(elements)
        return breche

    def lister(self, statut: Optional[StatutBreche] = None) -> list[Breche]:
        elements = self._charger()
        if statut is not None:
            elements = [e for e in elements if e.get("statut") == statut.value]
        return [Breche(**e) for e in elements]

    def mettre_a_jour(
        self, identifiant: str, statut: StatutBreche,
        resolution: Optional[str] = None, test_non_regression: Optional[str] = None,
    ) -> Breche:
        elements = self._charger()
        for element in elements:
            if element.get("id") == identifiant:
                element["statut"] = statut.value
                if resolution is not None:
                    element["resolution"] = resolution
                if test_non_regression is not None:
                    element["test_non_regression"] = test_non_regression
                self._sauver(elements)
                return Breche(**element)
        raise KeyError(f"Breche inconnue: {identifiant}")


@dataclass(frozen=True)
class Observation:
    cle: str
    valeur: Any
    niveau: Niveau = Niveau.OBSERVATION
    source: str = "utilisateur"
    confiance: float = 1.0
    contexte: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.cle.strip():
            raise ValueError("La cle ne peut pas etre vide.")
        if not 0.0 <= self.confiance <= 1.0:
            raise ValueError("La confiance doit etre comprise entre 0 et 1.")


@dataclass(frozen=True)
class RegleContexte:
    """Regle explicite de comparaison. Sans regle, aucune equivalence semantique n'est inventee."""

    champs_identitaires: tuple[str, ...]
    normaliseurs: dict[str, Callable[[Any], Any]] = field(default_factory=dict)

    def normaliser(self, contexte: dict[str, Any]) -> tuple[Optional[dict[str, Any]], list[str]]:
        resultat: dict[str, Any] = {}
        inconnues: list[str] = []
        for champ in self.champs_identitaires:
            if champ not in contexte:
                inconnues.append(f"Champ contextuel manquant: {champ}")
                continue
            valeur = contexte[champ]
            normaliseur = self.normaliseurs.get(champ)
            if normaliseur is not None:
                try:
                    valeur = normaliseur(valeur)
                except Exception as exc:
                    inconnues.append(
                        f"Normalisation impossible pour {champ}: {type(exc).__name__}"
                    )
                    continue
            resultat[champ] = valeur
        return (resultat if not inconnues else None), inconnues


@dataclass
class Resultat:
    etat: Etat
    valeur: Any = None
    confiance: float = 0.0
    sources: list[str] = field(default_factory=list)
    contexte: dict[str, Any] = field(default_factory=dict)
    inconnues: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)
    prochaine_action: Optional[str] = None
    message: str = ""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class TraceJSONL:
    def __init__(self, chemin: str | Path) -> None:
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self._dernier_hash = self._lire_dernier_hash()

    def _lire_dernier_hash(self) -> str:
        if not self.chemin.exists() or self.chemin.stat().st_size == 0:
            return "GENESIS"
        lignes = [l for l in self.chemin.read_text(encoding="utf-8").splitlines() if l]
        if not lignes:
            return "GENESIS"
        try:
            return json.loads(lignes[-1])["hash"]
        except (json.JSONDecodeError, KeyError):
            return "TRACE_CORROMPUE"

    def ecrire(self, trace_id: str, phase: str, details: dict[str, Any]) -> None:
        evenement = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "phase": phase,
            "details": details,
            "hash_precedent": self._dernier_hash,
        }
        canonique = json.dumps(evenement, sort_keys=True, ensure_ascii=False, default=str)
        evenement["hash"] = hashlib.sha256(canonique.encode()).hexdigest()
        with self.chemin.open("a", encoding="utf-8") as flux:
            flux.write(json.dumps(evenement, ensure_ascii=False, default=str) + "\n")
        self._dernier_hash = evenement["hash"]

    def verifier(self) -> tuple[bool, list[str]]:
        precedent = "GENESIS"
        erreurs: list[str] = []
        if not self.chemin.exists():
            return True, erreurs
        for numero, ligne in enumerate(self.chemin.read_text(encoding="utf-8").splitlines(), 1):
            try:
                evenement = json.loads(ligne)
                hash_lu = evenement.pop("hash")
                if evenement["hash_precedent"] != precedent:
                    erreurs.append(f"Ligne {numero}: chainage invalide")
                canonique = json.dumps(evenement, sort_keys=True, ensure_ascii=False, default=str)
                if hashlib.sha256(canonique.encode()).hexdigest() != hash_lu:
                    erreurs.append(f"Ligne {numero}: contenu modifie")
                precedent = hash_lu
            except (json.JSONDecodeError, KeyError) as exc:
                erreurs.append(f"Ligne {numero}: trace illisible ({exc})")
        return not erreurs, erreurs


class ProtocoleAntediluvienV3:
    """Base reconstruite autour d'un invariant nouveau: toute correction cree un nouveau domaine a tester."""

    INVARIANTS = (
        "ne_pas_inventer_une_equivalence",
        "ne_pas_ecraser_les_observations",
        "contradiction_seulement_si_comparabilite_prouvee",
        "incomparabilite_devient_inconnu",
        "toute_correction_ajoute_un_test_de_non_regression",
        "confiance_bornee_par_le_maillon_le_plus_faible",
        "trace_append_only_verifiable",
    )

    def __init__(
        self, journal: TraceJSONL, regles: Optional[dict[str, RegleContexte]] = None,
        registre_breches: Optional[RegistreBreches] = None,
    ) -> None:
        self.journal = journal
        self.regles = dict(regles or {})
        self.registre_breches = registre_breches
        self.logger = logging.getLogger("protocole_antediluvien_v3")

    def _trace(self, trace_id: str, phase: str, **details: Any) -> None:
        self.journal.ecrire(trace_id, phase, details)
        self.logger.debug("%s %s %s", trace_id, phase, details)

    def _ouvrir_breche(
        self, trace_id: str, categorie: str, titre: str, description: str,
        *, cle: Optional[str] = None, preuve: Optional[dict[str, Any]] = None,
        severite: SeveriteBreche = SeveriteBreche.MOYENNE,
        test_non_regression: Optional[str] = None,
    ) -> Optional[Breche]:
        if self.registre_breches is None:
            return None
        breche = self.registre_breches.enregistrer(
            categorie=categorie, titre=titre, description=description,
            trace_id=trace_id, severite=severite, cle=cle, preuve=preuve,
            test_non_regression=test_non_regression,
        )
        self._trace(
            trace_id, "BRECHE_ENREGISTREE", breche_id=breche.id,
            empreinte=breche.empreinte, categorie=breche.categorie,
            occurrences=breche.occurrences,
        )
        return breche

    def _comparer_contextes(
        self, gauche: Observation, droite: Observation
    ) -> tuple[Comparabilite, list[str], dict[str, Any]]:
        if gauche.niveau != droite.niveau:
            return Comparabilite.CONTEXTES_DIFFERENTS, [], {"raison": "niveaux differents"}

        regle = self.regles.get(gauche.cle)
        if regle is None:
            # Egalite structurelle stricte seulement. Toute autre relation reste inconnue.
            if gauche.contexte == droite.contexte:
                return Comparabilite.MEME_CONTEXTE, [], {"mode": "egalite stricte"}
            return (
                Comparabilite.CONTEXTE_INDETERMINABLE,
                [f"Aucune regle de contexte pour '{gauche.cle}'"],
                {"gauche": gauche.contexte, "droite": droite.contexte},
            )

        contexte_g, erreurs_g = regle.normaliser(gauche.contexte)
        contexte_d, erreurs_d = regle.normaliser(droite.contexte)
        erreurs = erreurs_g + erreurs_d
        if erreurs:
            return Comparabilite.CONTEXTE_INDETERMINABLE, erreurs, {}
        if contexte_g == contexte_d:
            return Comparabilite.MEME_CONTEXTE, [], {"normalise": contexte_g}
        return (
            Comparabilite.CONTEXTES_DIFFERENTS,
            [],
            {"gauche": contexte_g, "droite": contexte_d},
        )

    def _analyser_coherence(
        self, observations: list[Observation], trace_id: str
    ) -> tuple[list[str], list[str]]:
        contradictions: list[str] = []
        inconnues: list[str] = []
        for i, gauche in enumerate(observations):
            for droite in observations[i + 1 :]:
                if gauche.cle != droite.cle:
                    continue
                statut, raisons, details = self._comparer_contextes(gauche, droite)
                self._trace(
                    trace_id,
                    "COMPARAISON_CONTEXTE",
                    cle=gauche.cle,
                    statut=statut.value,
                    raisons=raisons,
                    details=details,
                )
                if statut == Comparabilite.CONTEXTE_INDETERMINABLE:
                    inconnues.extend(raisons)
                elif statut == Comparabilite.MEME_CONTEXTE and gauche.valeur != droite.valeur:
                    contradictions.append(
                        f"'{gauche.cle}' differe dans un contexte prouve equivalent: "
                        f"{gauche.valeur!r} != {droite.valeur!r}"
                    )
        return sorted(set(contradictions)), sorted(set(inconnues))

    @staticmethod
    def _serialiser(observations: list[Observation]) -> list[dict[str, Any]]:
        return [asdict(o) for o in observations]

    def executer(
        self,
        observations: Iterable[Observation],
        modele: Optional[Callable[[list[Observation]], Optional[bool]]] = None,
    ) -> Resultat:
        observations = list(observations)
        resultat = Resultat(etat=Etat.INCONNU)
        tid = resultat.trace_id
        self._trace(tid, "CAPTURE", observations=self._serialiser(observations))
        self._trace(tid, "INVARIANTS", invariants=self.INVARIANTS)

        if not observations:
            resultat.inconnues = ["Aucune observation"]
            resultat.message = "Je ne comprends pas: aucune observation."
            self._ouvrir_breche(
                tid, "ENTREE", "Absence d'observation",
                "Le protocole ne peut rien evaluer sans observation.",
                severite=SeveriteBreche.FAIBLE,
                test_non_regression="test_aucune_observation_reste_inconnue",
            )
            return resultat

        resultat.sources = sorted({o.source for o in observations})
        resultat.confiance = min(o.confiance for o in observations)
        resultat.valeur = self._serialiser(observations)

        contradictions, inconnues = self._analyser_coherence(observations, tid)
        resultat.contradictions = contradictions
        resultat.inconnues = inconnues

        if contradictions:
            resultat.etat = Etat.CONTRADICTOIRE
            resultat.message = "Contradiction detectee entre observations comparables."
            resultat.prochaine_action = "Verifier les sources ou ajouter une mesure independante"
            self._trace(tid, "ARRET_CONTRADICTION", contradictions=contradictions)
            self._ouvrir_breche(
                tid, "COHERENCE", "Contradiction entre observations comparables",
                "Des valeurs differentes ont ete trouvees dans un contexte prouve equivalent.",
                preuve={"contradictions": contradictions},
                severite=SeveriteBreche.HAUTE,
                test_non_regression="test_synonymes_normalises_revelent_contradiction",
            )
            return resultat

        if inconnues:
            resultat.etat = Etat.INCONNU
            resultat.message = "Comparabilite non demontree; aucune conclusion forcee."
            resultat.prochaine_action = "Definir une regle contextuelle explicite et testee"
            self._trace(tid, "ARRET_COMPARABILITE_INCONNUE", inconnues=inconnues)
            self._ouvrir_breche(
                tid, "CONTEXTE", "Comparabilite contextuelle indeterminee",
                "Les observations ne peuvent etre comparees sans regle contextuelle suffisante.",
                preuve={"inconnues": inconnues},
                severite=SeveriteBreche.MOYENNE,
                test_non_regression="test_contextes_differents_sans_regle_deviennent_inconnus",
            )
            return resultat

        if modele is None:
            resultat.message = "Observations coherentes conservees; aucun modele fourni."
            resultat.prochaine_action = "Fournir un modele testable"
            return resultat

        try:
            prediction = modele(observations)
        except Exception as exc:
            resultat.inconnues = [f"Modele en erreur: {type(exc).__name__}"]
            resultat.message = "Erreur contenue; aucune conclusion forcee."
            self._trace(tid, "ERREUR_MODELE", type=type(exc).__name__, message=str(exc))
            self._ouvrir_breche(
                tid, "MODELE", "Exception du modele",
                "Le modele a leve une exception; la conclusion a ete suspendue.",
                preuve={"type": type(exc).__name__, "message": str(exc)},
                severite=SeveriteBreche.HAUTE,
                test_non_regression="test_modele_en_erreur_reste_inconnu",
            )
            return resultat

        if prediction is None or not isinstance(prediction, bool):
            resultat.inconnues = ["Le modele ne retourne pas un booleen interpretable"]
            resultat.message = "Le modele ne sait pas conclure."
            self._ouvrir_breche(
                tid, "MODELE", "Sortie de modele non interpretable",
                "Le contrat True/False/None n'a pas ete respecte.",
                preuve={"type": type(prediction).__name__, "valeur": repr(prediction)},
                severite=SeveriteBreche.MOYENNE,
                test_non_regression="test_sortie_modele_non_booleenne_ouvre_breche",
            )
            return resultat

        resultat.etat = Etat.VRAI if prediction else Etat.FAUX
        resultat.valeur = prediction
        resultat.message = "Conclusion limitee au modele et aux regles de contexte fournies."
        resultat.prochaine_action = "Ajouter un test antagoniste de non-regression"
        self._trace(tid, "CONCLUSION", etat=resultat.etat.value, confiance=resultat.confiance)
        return resultat


def normaliser_heure(valeur: Any) -> str:
    texte = str(valeur).strip().lower()
    synonymes = {"midi": "12:00", "12 h": "12:00", "12h": "12:00"}
    return synonymes.get(texte, texte)
