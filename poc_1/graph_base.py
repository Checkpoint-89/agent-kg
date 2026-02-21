from __future__ import annotations
from typing import Literal, Any
import re
import unicodedata

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
    ConfigDict,
)
from pydantic.json_schema import SkipJsonSchema

def sanitize_for_class_name(name: str, style: str|None = "upper") -> str:
    """
    Cleans a string to make it a valid Python class name.
    Removes accents, replaces invalid characters with underscores, and converts to uppercase.
    """
    if not isinstance(name, str): # type: ignore
        raise ValueError("Le nom doit être une chaîne de caractères.")
    # Normalize to NFD form to separate base characters from accents
    s = unicodedata.normalize('NFD', name)
    # Remove the accent characters (combining diacritical marks)
    s = "".join(c for c in s if unicodedata.category(c) != 'Mn')
    # Replace '&' with '_AND_' and clean up spaces
    s = s.strip().replace("&", "_AND_")
    # Replace any sequence of non-alphanumeric characters with a single underscore
    s = re.sub(r'[^a-zA-Z0-9_]+', '_', s)
    # Ensure the name does not start with a number
    if s and s[0].isdigit():
        s = '_' + s
    # Remove leading or trailing underscores
    s = s.strip('_')
    # Reduce multiple underscores to a single one
    s = re.sub(r'_+', '_', s)

    # Apply the desired style
    if style == "capitilize":
        return s.title()
    elif style == "upper":
        return s.upper()
    elif style == "lower":
        return s.lower()

    return s

OBJECTIFS = """
    Objectifs :
    - Tu es l’agent IA du courtier en énergie Omnegy.
    - Extraire des documents les entités et relations correspondant à des cas métiers propres au courtage d’énergie.
    - Une relation doit être une tâche métier unitaire pertinente dans le cadre des processus métiers d'Omnegy.
    - Une entité doit être un type d'acteur métier, un objet ou un concept métier pertinent dans le cadre de processus métiers d'Omnegy.
    - Au final, on utilisera les relations et entités que tu extrairas pour alimenter un graphe de connaissances utile à la compréhension des clients, de leur environnement et du business.
"""

RELATION = "tâche métier unitaire dans un processus métier"
ENTITY = "acteur métier, objet ou concept métier"

def compute_fields(self: Any) -> Any:

    # Subject
    subject_label = self.roles.agents[0].label
    subject_name = self.roles.agents[0].name

    # Object
    obj_label = self.roles.themes[0].label
    obj_name = self.roles.themes[0].name 
    
    # Build labels
    self.labels = [self.relation_type.verb, self.relation_type.label]

    # Build generic name of the relation
    generic_ = f"{subject_label} {self.relation_type.label} {obj_label}"
    self.generic = generic_

    # Build specific name of the relation
    specific_ = f"{subject_label} ({subject_name}) {self.relation_type.label} {obj_label} ({obj_name})"
    self.specific = specific_

    return self

class RelationType(BaseModel):
    f"""
        Le type de {RELATION} (verb, target_category) doit décrire la {RELATION} en elle-même, pas le moyen technique de le réaliser. Par ex: (communiquer, recommandation) plutôt que (envoyer, email), (négocier, contrat) plutôt que (discuter, interlocuteur).
    """
    model_config = ConfigDict(extra="forbid")

    # Axis
    axis: Literal["ONTOLOGIQUE", "DYNAMIQUE", "STRUCTUREL"] = Field(..., description=f"La catégorie de la {RELATION}.")

    # Computed
    label: SkipJsonSchema[str | None] = Field(default=None, description=f"Nom unique de la {RELATION}, utilisé comme identifiant de classe.")

    # Identification
    verb: str = Field(..., description= "Locution verbale à l’infinitif décrivant l'interaction ou l'état. N'inclus pas de sujet ou d'objet, ni de détails contextuels.")
    target_category: str = Field(
        ...,
        description="Nom générique de la catégorie à laquelle appartient l'objet, la cible ou le contexte de l'action représentée par 'verb'. Il doit permettre de discriminer des relations qui utilisent le même verbe. Par exemple, si le verbe est 'communiquer', 'target_category' pourrait être 'prix' ou 'point marché'."
    )

    # Definition
    definition: str = Field(
        ...,
        description=f"Définie la classe à laquelle appartient la {RELATION} indépendamment du contexte et des entités spécifiques impliquées. Cette définition doit être suffisament générique pour permettre un embedding et un regroupement par clustering des relations de même classe extraitent par ailleurs."
    )

    # Validation
    @field_validator('verb', mode='after')
    def verb_in_uppercase(cls, verb: str) -> str:
        return sanitize_for_class_name(verb.strip(), style="upper")
    
    @model_validator(mode='after')
    def set_and_sanitize_name(self) -> 'RelationType':
        """Calcule et nettoie le champ 'label' à partir de 'verb' et 'target_category'."""
        if self.verb:
            self.label = self.verb + "_" + self.target_category.strip().replace(" ", "_")
            self.label = sanitize_for_class_name(self.label, style="upper")
        return self
    
class EntityType(BaseModel):
    label: str = Field(..., description="Classe de l'entité.")
    definition: str = Field(..., description=f"Définition de la classe de l'entité")
    
class Property(BaseModel):

    # Identification
    key: str = Field(..., description=f"Nom de l’attribut décrivant un aspect pertinent de l’entité ou de la {RELATION}.")
    value: str = Field(..., description="Valeur associée à cet l'attribut.")

    # Validation
    @field_validator('key', mode='after')
    def key_in_snake_case(cls, key: str) -> str:
        return key.strip().lower().replace(" ", "_")
    
    @field_validator('value', mode='after')
    def value_in_lowercase(cls, value: str) -> str:
        return value.strip().lower()
    
    def __str__(self):
        return f"{self.key}: {self.value}"

class Entity(BaseModel):
    f"""
        Instructions:
        Renseigne le label et le nom de l'{ENTITY} jouant le rôle considéré.
    """
    model_config = ConfigDict(extra="forbid")

    # Identification
    label: str = Field(
        ..., 
        description=f"Classe de l'{ENTITY}."
    )
    name: str = Field(
        ..., 
        description=f"Dénomination exacte de l’entité, reproduite telle qu’elle apparaît dans le texte source. 'name'  doit permettre d'indentifier de quelle {ENTITY} de la classe 'label' on parle."
    )
    entity_type: SkipJsonSchema[EntityType|None] = Field(default=None, description=f"Type de l'{ENTITY}, dérivé du label.")

    # Properties
    # properties: list[Property]|None = Field(default=None, description=f" Liste d’attributs fournissant des détails pertinent sur l'{ENTITY}.")

    # Definition
    definition: str = Field(
        ...,
        description=f"Définie la classe désignée par 'label' à laquelle appartient l'{ENTITY} indépendamment du contexte. Cette définition doit être suffisament générique pour permettre un embedding et un regroupement par clustering des entités de même classe extraitent par ailleurs."
    )

    # Computed
    metadata: SkipJsonSchema[dict[str, Any]|None] = Field(default=None, description="Dictionnaire pour les métadonnées ajoutées par l'application.")

    to_embed: SkipJsonSchema[str|None] = Field(default=None, description="Description pour l'embedding et le clustering.")

    # Validation
    @field_validator('label', 'name', mode='after')
    def value_in_title_case(cls, value: str) -> str:
        return value.strip().title()
    
    @model_validator(mode='after')
    def check_label_not_generic(self) -> 'Entity':
        """Vérifie que le label n'est pas trop générique."""
        label_lower = self.label.lower()
        GENERIC_ENTITIES = [
            "agent", "theme", "trigger", "purpose", "reason", 
            "instrument", "beneficiary", "context", "origin", 
            "destination", "co_agent", "location", "time",
            "personne", "organisation", "lieu", "objet", 
            "concept", "événement", "document", "information", 
            "donnée", "dossier"]
        if label_lower in GENERIC_ENTITIES:
            raise ValueError(f"Le label '{self.label}' est trop générique. Utilisez un label métier plus spécifique. N'utilise pas les termes suivants: {', '.join(GENERIC_ENTITIES)}.")
        return self

    @model_validator(mode='after')
    def v_compute_fields(self: 'Entity') -> 'Entity':
        self.to_embed = (
            f"Classe de l'entité: {self.label}. "
            f"Définition: {self.definition}"
        )
        self.entity_type = EntityType(label=self.label, definition=self.definition)
        return self
    
class Agent_Entity(Entity):
    f"""
        Identifie les entités qui jouent le rôle suivant dans la {RELATION}.

        agent: {ENTITY} sujet de la {RELATION} qui répond à « qui initie ou contrôle ou perçoit l’action ? ». Inclure par ex.: acteur responsable (humain/organisation/système), acteur qui perçoit l'information. Exclure par ex.: outils, lieux, cadres qui sont des circonstances.
    """
    role: Literal["agent"] = Field("agent", description=f"Rôle de l'{ENTITY} dans la {RELATION}.")

class Theme_Entity(Entity):
    f"""
        Identifie les entités qui jouent le rôle suivant dans la {RELATION}.

        theme: Entité objet de la {RELATION} qui répond à « qu’est-ce qui est impliqué, concerné, affecté ou modifié ? ». Inclure par ex.: objet transporté, sujet (contrat, offre), marchandise en circulation ou {ENTITY} créée, altérée, déplacée, détruite.
    """
    role: Literal["theme"] = Field("theme", description=f"Rôle de l'{ENTITY} dans la {RELATION}.")

class Circumstance_Entity(Entity):
    f"""
        Identifie les entités qui jouent un des rôles suivants dans la {RELATION}.

        trigger: Entité circonstancielle de la {RELATION} qui répond à « qu’est-ce qui déclenche sans intention ? ». Inclure par ex.: événement, signal, condition (alerte, panne, échéance). Exclure par ex.: motif/décision. Arbitrage: intention/règle → reason.

        purpose: Entité circonstancielle de la {RELATION} : « dans quel but, pour quel objectif l’action a-t-elle lieu ? ». Inclure par ex.: intention poursuivie, objectif stratégique, finalité visée. Exclure par ex.: déclencheur non intentionnel (alerte, signal). Arbitrage: cause non intentionnelle → trigger ; justification/norme → reason.

        reason: Entité circonstancielle de la {RELATION} : « quelle cause ou justification explique l’action ? ». Inclure par ex.: motif, explication, justification, norme, contrainte. Exclure par ex.: déclencheur non intentionnel (alerte, signal) et but poursuivi. Arbitrage: cause non intentionnelle → trigger ; objectif intentionnel → purpose.

        instrument: Entité circonstancielle de la {RELATION} qui répond à « quel moyen est utilisé ? ». Inclure par ex.: outil, logiciel, matériel, procédure. Exclure par ex.: acteurs autonomes, lieux, cadres. Arbitrage: acteur → agent/co_agent ; lieu → location ; cadre → context.

        beneficiary: Entité circonstancielle de la {RELATION} qui répond à « qui bénéficie de l’action ? ». Inclure par ex.: client/usager, {ENTITY} qui profite. Exclure par ex.: simple destination logistique. Arbitrage: destination d’envoi → destination ; {ENTITY} modifiée → patient ; {ENTITY} qui perçoit → theme.
    """
    role: Literal["trigger", "purpose", "reason", "instrument", "beneficiary"] = Field(..., description=f"Rôle de l'{ENTITY} dans la {RELATION}.")

class Context_Entity(Entity):
    f"""
        Identifie les entités qui jouent le rôle suivant dans la {RELATION}.

        context: Entité circonstancielle de la {RELATION} qui répond à « dans quel cadre l’action se déroule-t-elle ? ». Inclure par ex.: environnement juridique, contractuel, organisationnel, économique. Exclure par ex.: lieux, temps. Arbitrage: endroit → location ; période → time.
    """
    role: Literal["context"] = Field("context", description=f"Rôle de l'{ENTITY} dans la {RELATION}.")

class OriginDestination_Entity(Entity):
    f"""
        Identifie les entités qui jouent un des rôles suivants dans la {RELATION}.

        origin: Entité circonstancielle de la {RELATION} qui répond à « d’où cela vient-il ? ». Inclure par ex.: source/provenance d’une action ou d’un mouvement. Exclure par ex.: sujet de l'action, lieu d’exécution, destination. Arbitrage: sujet de l'action → agent, lieu d’exécution → location ; destination → destination.

        destination: Entité circonstancielle de la {RELATION} : « où ou vers qui cela va-t-il ? ». Inclure par ex.: lieu d’arrivée, cible d’envoi, destinataire logistique. Exclure par ex.: bénéficiaire (avantage reçu). Arbitrage: avantage → beneficiary ; objet modifié → patient ; lieu d’exécution → location.
    """
    role: Literal["origin", "destination"] = Field(..., description=f"Rôle de l'{ENTITY} dans la {RELATION}.")

class TimeLocation_Entity(Entity):
    f"""
        Identifie les entités qui jouent un des rôles suivants dans la {RELATION}.

        time: Entité circonstancielle de la {RELATION} qui répond à « quand l’action a-t-elle lieu ? ». Inclure par ex.: date, heure, intervalle. Exclure par ex.: métadonnées non temporelles. Arbitrage: conserver la granularité disponible

        location: Entité circonstancielle de la {RELATION} qui répond à « où l’action se déroule-t-elle ? ». Inclure par ex.: lieu d’exécution (physique ou logique). Exclure par ex.: provenance, destination. Arbitrage: provenance → origin ; destination → destination.
    """
    role: Literal["time", "location"] = Field(..., description=f"Rôle de l'{ENTITY} dans la {RELATION}.")

class Roles(BaseModel):
    f""" 
        Identifie les rôles associés à la {RELATION}.
    """
    agents: list[Agent_Entity] = Field(
        ..., 
        description=f"Entités jouant le rôle d'agent dans la {RELATION}. Distingue les entités par des labels différents.",
        min_length=1)
    themes: list[Theme_Entity] = Field(
        ...,
        description=f"Entités jouant le rôle de thème dans la {RELATION}. Distingue les entités par des labels différents.",
        min_length=1)
    circumstances: list[Circumstance_Entity] = Field(
        ..., 
        description=f"Entités jouant un rôle circonstanciel dans la {RELATION}. Distingue les entités par des labels différents.",
        min_length=1)
    context: list[Context_Entity] = Field(
        ..., 
        description=f"Entité jouant un rôle contextuel dans la {RELATION}. Distingue les entités par des labels différents.",
        min_length=1,)
    origin_destinations: list[OriginDestination_Entity] = Field(
        ...,
        description=f"Entités jouant un rôle d'origine ou de destination dans la {RELATION}. Distingue les entités par des labels différents."
    )
    time_locations: list[TimeLocation_Entity] = Field(
        ...,
        description=f"Entités jouant un rôle temporel ou de localisation dans la {RELATION}. Distingue les entités par des labels différents."
    )

    
class BaseRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Identification
    description: str = Field(
        ..., 
        description=f"Décris la {RELATION} dans son contexte. Sois précis et exhaustif dans ta description, en incluant les éléments pertinents qui aident à comprendre la {RELATION}. Rends ta description suffisamment explicite pour que ta description puisse être comprise sans référence à d'autres documents ou contextes."
    )

    # Roles
    roles: Roles = Field(
        ...,
        description=f"Roles informant la {RELATION} et attribution d'entités à ces rôles."
    )

    # Quote
    quote: str = Field(
        ...,
        description=f"Extrais la partie de texte qui illustre au mieux la {RELATION}, ses rôles et entités."
    )

    # Properties
    # properties: list[Property] | None = Field(default=None, description=f"Liste des informations pertinentes sur la {RELATION}.")

    # "Computed" fields
    labels : SkipJsonSchema[list[str]|None] = Field(default=None, description=f"Liste de labels associés à la {RELATION}, dérivée du type de {RELATION}.")
    generic: SkipJsonSchema[str|None] = Field(default=None, description=f"Description générique de la {RELATION}, sans référence à des entités spécifiques. Sert à l'embedding et au clustering.")
    specific: SkipJsonSchema[str|None] = Field(default=None, description=f"Description canonique de la {RELATION}.")
    # Metadata
    metadata: SkipJsonSchema[dict[str, Any]|None] = Field(default=None, description="Dictionnaire pour les métadonnées ajoutées par l'application.")

    def update_metadata(self, metadata_to_add: dict[str, Any]) -> None:
        f"""
        Updates the {RELATION}'s metadata.
        Adds or overwrites keys in the metadata dictionary
        with the key-value pairs from the input dictionary.
        """
        if self.metadata is None:
            self.metadata = {}
        self.metadata.update(metadata_to_add)

class Relation(BaseRelation):

    # Identification
    relation_type: RelationType = Field(..., description=f"Type de {RELATION}")

    # Definition
    to_embed: SkipJsonSchema[str|None] = Field(default=None, description="Description pour l'embedding et le clustering.") 

    # Validation
    @model_validator(mode='after')
    def v_compute_fields(self: 'Relation') -> 'Relation':

        compute_fields(self)

        self.to_embed = (
            f"Description générique: {self.generic}. "
            f"Définition: {self.relation_type.definition}"
        )

        return self

class EventRelations(BaseModel):
    f"""
    {OBJECTIFS.strip()}
    """
    model_config = ConfigDict(extra="forbid")

    relations: list[Relation] = Field(
        ...,
        description=f"""
            Consignes : 
                - Identifier les relations pertinentes et les classer selon l’un des trois axes définis ci-dessous.
                - Les définitions priment toujours sur les exemples.
                - Les exemples sont illustratifs : ils ne constituent pas une liste fermée et ne doivent pas être reproduits mécaniquement.

            Axes de classification des relations :

            1 - Axe ontologique (statique) — CE QUI EST
                Définition : caractéristiques stables, inhérentes ou durables d’une {ENTITY}.
                Critère : si la {RELATION} reste vraie indépendamment d’un instant précis (pas de début ni de fin explicite) → classer en ontologique.
                Exemples : TRAVAILLE_CHEZ, EST_LOCALISÉ_A, POSSÈDE, OCCUPE_POSTE.
                Contre-exemple : SIGNER_CONTRAT → action ponctuelle, donc dynamique.

            2 - Axe dynamique (changement) — CE QUI CHANGE
                Définition : actions, processus, transitions ou événements impliquant une modification ou un but.
                Critère : si la {RELATION} décrit une action (souvent verbale) avec un début et une fin → classer en dynamique.
                Exemples : SIGNER_CONTRAT, INSTALLER_CAPTEUR, NÉGOCIER, RÉSILIER_CONTRAT.
                Contre-exemple : FAIT_PARTIE_DE → appartenance stable, donc structurel.

            3 - Axe structurel (organisation) — CE QUI RELIE
                Définition : liens organisationnels, hiérarchiques ou de dépendance entre entités.
                Critère : si la {RELATION} exprime une inclusion, une hiérarchie ou une dépendance (interne ou externe) → classer en structurel.
                Exemples : FAIT_PARTIE_DE, DIRIGE, COMPREND, DÉPEND_DE.
                Contre-exemple : EST_LOCALISÉ_A → localisation géographique, donc ontologique.

            Important :
            - Toujours appliquer la définition avant de se référer aux exemples.
            - Les exemples couvrent différents domaines pour illustrer le principe, mais n’épuisent pas les possibilités.
        """
    )

# Relation CLusters
class RelationTypeCluster(BaseModel):
    name: str = Field(..., description="Nom du cluster.")
    definition: str = Field(..., description="Définition du cluster. Explicite en quoi ce cluster se distingue des autres.")
    relation_types: list[RelationType] = Field(..., description="Liste des relations types.")

    @field_validator('name', mode='after')
    def sanitize_name(cls, name: str) -> str:
        return sanitize_for_class_name(name)

class RelationTypeClusters(BaseModel):
    thinking: str = Field(..., description="Détaille ton analyse des cas métiers décrits dans l'input avec pour objectif de les réunir en cluster métier pertinent. Tu peux redéfinire les relations types si nécessaire.")
    clusters: list[RelationTypeCluster] = Field(..., description="Liste de clusters et relations identifiées ou redéfinies.")

    def __str__(self):
        string = ""
        for cluster in self.clusters:
            string += "*****************************************"
            string += f"\n{cluster.name}\n{cluster.definition}\n"
            for rel in cluster.relation_types:
                string += f"  {rel.verb} {rel.target_category if rel.target_category else ''}: {rel.definition}\n"
        return string.strip()


# Entity Clusters
class EntityTypeCluster(BaseModel):
    name: str = Field(..., description="Nom du cluster.")
    definition: str = Field(..., description="Définition du cluster. Explicite en quoi ce cluster se distingue des autres.")
    entity_types: list[EntityType] = Field(..., description="Liste des entity types.")

    @field_validator('name', mode='after')
    def sanitize_name(cls, name: str) -> str:
        return sanitize_for_class_name(name)

class EntityTypeClusters(BaseModel):
    thinking: str = Field(..., description=f"Détaille ton analyse des {ENTITY} décrites dans l'input avec pour objectif de les réunir en clusters métiers pertinents. Tu peux redéfinire les {ENTITY} si nécessaire.")
    clusters: list[EntityTypeCluster] = Field(..., description=f"Liste de clusters et {ENTITY} identifiées ou redéfinies.")

    def __str__(self):
        string = ""
        for cluster in self.clusters:
            string += "*****************************************"
            string += f"\n{cluster.name}\n{cluster.definition}\n"
            for ent in cluster.entity_types:
                string += f"  {ent.label}: {ent.definition}\n"
        return string.strip()