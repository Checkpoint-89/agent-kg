"""
Module for semantic filtering of events to retain only business-relevant ones.
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def create_business_relevance_config() -> Dict[str, Any]:
    """
    Create filter configuration for identifying business-relevant events.
    
    Returns:
        Filter configuration dictionary
    """
    # Filter configuration for business relevance
    filter_config: dict[str, Any] = {
        "filter_type": "out", # Filters OUT items where any criterion is True
        "criteria": [
            {
                "name":"msg_pour_organiser_evt",
                "description":"Si traite essentiellement de logistique, d’organisation ou de coordination pratique pour meeting ou évènement. -> True"
            },
            {
                "name":"is_automatic_reply",
                "description":"Si réponse automatique -> True"
            },
            {
                "name":"is_from_domain_followupthen",
                "description":"Si email provient de l'expéditeur followupthen qui est un service de rappels de tâches -> True. Si le domaine n'est pas trouvé ou n'est pas followupthen.com -> False."
            },
            {
                "name":"is_unclear_vague",
                "description":"Si le contenu utile du message est flou, imprécis ou ambigu -> True. Un manque de détail (ex: projet en cours, manque des données) ne suffit pas à rendre le message vague -> False."
            },
        ]
    }
    
    return filter_config