"""
Module for retrieving events linked to a specific opportunity.
"""
import logging
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from shared.models.opportunity_history_table import OpportunityHistoryTable
from shared.models.event_table import EventTable
from shared.utils.aws_utils import parallel_read_json_from_s3

logger = logging.getLogger(__name__)

def get_carbogreen_opportunity_id(engine: Engine) -> str:
    """
    Find the actual opportunity ID for Carbogreen.
    
    Args:
        engine: SQLAlchemy engine
        
    Returns:
        Opportunity ID for Carbogreen
    """
    with engine.connect() as connection:
        query = text("""
            SELECT id 
            FROM public.opportunity 
            WHERE name ILIKE :name_pattern
            LIMIT 1
        """)
        
        result = connection.execute(query, {"name_pattern": "%Carbogreen%"})
        row = result.fetchone()
        
    if row:
        return row[0]
    else:
        raise ValueError("Could not find Carbogreen opportunity ID")

def get_opportunity_events(engine: Engine, opportunity_id: str, limit: int|None) -> pd.DataFrame:
    """
    Retrieve all events associated with a specific opportunity ID.
    
    Args:
        engine: SQLAlchemy engine
        opportunity_id: ID of the opportunity to retrieve events for
        
    Returns:
        DataFrame containing all events associated with the opportunity
    """
    logger.info(f"Retrieving events for opportunity ID: {opportunity_id}")
    
    # Create a session to query the database
    with engine.connect() as connection:
        # Query opportunity_history_enriched table to get event references
        query = text(f"""
            SELECT evt.*
            FROM {OpportunityHistoryTable.__table_args__[1]["schema"]}.opportunity_history_enriched ohe
            JOIN {EventTable.__table_args__["schema"]}.event evt ON ohe.new_value = evt.event_id
            WHERE ohe.record_id = :opportunity_id
            AND ohe.field in ('email', 'feed_item', 'transcription', 'type', 'status', 'teams', 'internal_teams')
            LIMIT :limit
        """)
        
        result = connection.execute(query, {"opportunity_id": opportunity_id, "limit": limit})
        events_df = pd.DataFrame(result.fetchall(), columns=result.keys())
    
    if events_df.empty:
        logger.warning(f"No events found for opportunity ID {opportunity_id}")
        return pd.DataFrame()
    
    logger.info(f"Found {len(events_df)} events for opportunity ID {opportunity_id}")
    
    # Load event content from S3
    logger.info("Loading event content from S3...")
    events_df['bucket'] = events_df['s3_path'].apply(lambda x: x.split('/', 1)[0] if x else None)
    events_df['key'] = events_df['s3_path'].apply(lambda x: x.split('/', 1)[1] if x else None)
    
    # Filter out rows with None values
    valid_events_df = events_df.dropna(subset=['bucket', 'key'])
    
    if valid_events_df.empty:
        logger.warning("No valid S3 paths found for events")
        return events_df
    
    # Load formatted content from S3
    events_df['formatted'] = parallel_read_json_from_s3(valid_events_df, 'bucket', 'key', deserialize=False)
    
    # Clean up temporary columns
    events_df.drop(columns=['bucket', 'key'], inplace=True)
    
    return events_df