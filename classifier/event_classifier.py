#!/usr/bin/env python3
"""
Event classifier module for TSE_EventBase project.
Uses Anthropic's Claude API to classify corporate events.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import anthropic
import sqlite3
import json
import logging
from typing import List, Dict, Optional
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, CLASSIFICATION_BATCH_SIZE, DB_PATH, EVENT_TYPES, DIRECTIONS, MAGNITUDES

logger = logging.getLogger(__name__)

class EventClassifier:
    def __init__(self, api_key: str, base_url: str = None, db_path: str = DB_PATH):
        client_params = {
            "api_key": api_key
        }
        if base_url:
            client_params["base_url"] = base_url
        
        self.client = anthropic.Anthropic(**client_params)
        self.db_path = db_path
        self.model = ANTHROPIC_MODEL
    
    def get_unclassified_events(self, limit: int = None) -> List[Dict]:
        """
        Retrieve events that haven't been classified yet.
        
        Args:
            limit: Maximum number of events to retrieve
            
        Returns:
            List of unclassified event dictionaries
        """
        return self.get_filtered_unclassified_events(None, None, limit)

    def get_filtered_unclassified_events(self, include_keywords: List[str] = None,
                                      exclude_keywords: List[str] = None,
                                      limit: int = None) -> List[Dict]:
        """
        Retrieve events that haven't been classified yet with optional filtering.
        
        Args:
            include_keywords: List of keywords to include (OR condition)
            exclude_keywords: List of keywords to exclude (NOT condition)
            limit: Maximum number of events to retrieve
            
        Returns:
            List of unclassified event dictionaries
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = """
            SELECT id, ticker, company_name, event_date, headline, raw_json
            FROM events
            WHERE event_type IS NULL OR event_type = ''
        """
        params = []
        
        if include_keywords:
            include_conditions = " OR ".join(["headline LIKE ?" for _ in include_keywords])
            include_params = [f"%{keyword}%" for keyword in include_keywords]
            query += f" AND ({include_conditions})"
            params.extend(include_params)
        
        if exclude_keywords:
            exclude_conditions = " OR ".join(["headline LIKE ?" for _ in exclude_keywords])
            exclude_params = [f"%{keyword}%" for keyword in exclude_keywords]
            query += f" AND NOT ({exclude_conditions})"
            params.extend(exclude_params)
        
        query += " ORDER BY event_date DESC"
        
        if limit:
            query += f" LIMIT {limit}"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to list of dictionaries
        events = []
        for row in rows:
            events.append({
                'id': row[0],
                'ticker': row[1],
                'company_name': row[2],
                'event_date': row[3],
                'headline': row[4],
                'raw_json': row[5]
            })
        
        conn.close()
        return events
    
    def classify_events_batch(self, events: List[Dict]) -> List[Dict]:
        """
        Classify a batch of events using the Anthropic API.
        
        Args:
            events: List of event dictionaries to classify
            
        Returns:
            List of classified event results
        """
        if not events:
            return []
        
        # Prepare the prompt for batch classification
        events_text = ""
        for i, event in enumerate(events):
            events_text += f"Event {i+1}:\n"
            events_text += f"ID: {event['id']}\n"
            events_text += f"Ticker: {event['ticker']}\n"
            events_text += f"Company: {event['company_name']}\n"
            events_text += f"Date: {event['event_date']}\n"
            events_text += f"Headline: {event['headline']}\n\n"
        
        system_prompt = f"""You are an expert financial analyst specializing in Japanese corporate events. Your task is to classify corporate events from the Tokyo Stock Exchange based on their headlines and context.

For each event, please provide the following classifications:

1. event_type: One of the following categories:
   - earnings: Earnings announcements (決算短信, 四半期決算, 通期決算)
   - forecast_revision: Changes to earnings forecasts (業績修正)
   - dividend: Dividend announcements (配当, 中間配当)
   - buyback: Share buybacks (自己株式買付, 株式買還)
   - ma: Mergers and acquisitions (M&A, 合併, 増資, 株式交換)
   - tender_offer: Tender offers (TOB, 証券公開買い付け)
   - leadership_change: Leadership changes (代表取締役変更, 社長交代)
   - stock_split: Stock splits or consolidations (株式分割, 株式併合)
   - large_holding: Large shareholding notifications (大量保有, 5%超保有)
   - capital_raise: Capital raising activities (新株発行, 第三者割当)
   - delisting: Delisting announcements (上場廃止, 上場維持困難)
   - other: Any other type of announcement

2. direction: Market sentiment impact (positive, negative, neutral)

3. magnitude: Impact scale (large, medium, small)

4. headline_en: English translation of the headline

5. summary: Brief English summary of the event

Please return your response as valid JSON with the following structure:
{{
  "classifications": [
    {{
      "id": <event_id>,
      "event_type": "<type>",
      "event_subtype": "<more_specific_type_if_applicable>",
      "direction": "<direction>",
      "magnitude": "<magnitude>",
      "headline_en": "<english_translation>",
      "summary": "<english_summary>"
    }}
  ]
}}

Be accurate and consistent in your classifications. If you cannot determine a classification with confidence, use 'other' for event_type."""
        
        user_prompt = f"""Please classify the following events:

{events_text}

Return only the JSON response with classifications for all events."""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                temperature=0.1,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            
            # Extract the response content
            response_text = response.content[0].text.strip()
            
            # Clean up potential markdown formatting
            if response_text.startswith("```json"):
                response_text = response_text[7:]  # Remove ```json
            if response_text.endswith("```"):
                response_text = response_text[:-3]  # Remove ```
            
            # Parse the JSON response
            result = json.loads(response_text)
            
            # Validate the structure
            if 'classifications' not in result:
                logger.error(f"Invalid response structure: {response_text}")
                return []
            
            return result['classifications']
            
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response: {e}")
            logger.error(f"Response text: {response_text}")
            return []
        except Exception as e:
            logger.error(f"Error calling Anthropic API: {e}")
            return []
    
    def update_event_classification(self, event_id: int, classification: Dict):
        """
        Update an event with its classification in the database.
        
        Args:
            event_id: ID of the event to update
            classification: Classification dictionary
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE events 
            SET event_type = ?, event_subtype = ?, direction = ?, magnitude = ?, 
                headline_en = ?, summary = ?, classified_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            classification.get('event_type'),
            classification.get('event_subtype'),
            classification.get('direction'),
            classification.get('magnitude'),
            classification.get('headline_en'),
            classification.get('summary'),
            event_id
        ))
        
        conn.commit()
        conn.close()
    
    def classify_all_unclassified(self, batch_size: int = CLASSIFICATION_BATCH_SIZE):
        """
        Classify all unclassified events in the database.
        
        Args:
            batch_size: Number of events to process in each batch
        """
        self.classify_filtered_events(batch_size=batch_size)

    def classify_filtered_events(self, batch_size: int = CLASSIFICATION_BATCH_SIZE,
                               include_keywords: List[str] = None,
                               exclude_keywords: List[str] = None):
        """
        Classify filtered unclassified events in the database.
        
        Args:
            batch_size: Number of events to process in each batch
            include_keywords: List of keywords to include (OR condition)
            exclude_keywords: List of keywords to exclude (NOT condition)
        """
        logger.info("Starting classification of filtered unclassified events...")
        
        # Get total count first
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = "SELECT COUNT(*) FROM events WHERE event_type IS NULL OR event_type = ''"
        params = []
        
        if include_keywords:
            include_conditions = " OR ".join(["headline LIKE ?" for _ in include_keywords])
            include_params = [f"%{keyword}%" for keyword in include_keywords]
            query += f" AND ({include_conditions})"
            params.extend(include_params)
        
        if exclude_keywords:
            exclude_conditions = " OR ".join(["headline LIKE ?" for _ in exclude_keywords])
            exclude_params = [f"%{keyword}%" for keyword in exclude_keywords]
            query += f" AND NOT ({exclude_conditions})"
            params.extend(exclude_params)
        
        cursor.execute(query, params)
        total_unclassified = cursor.fetchone()[0]
        conn.close()
        
        logger.info(f"Found {total_unclassified} filtered unclassified events")
        
        if total_unclassified == 0:
            logger.info("No filtered unclassified events found, exiting.")
            return
        
        processed = 0
        while True:
            # Get next batch of filtered unclassified events
            events = self.get_filtered_unclassified_events(
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
                limit=batch_size
            )
            
            if not events:
                break
            
            logger.info(f"Classifying batch of {len(events)} events...")
            
            # Classify the batch
            classifications = self.classify_events_batch(events)
            
            # Update the database with classifications
            successful_updates = 0
            for classification in classifications:
                try:
                    event_id = classification.get('id')
                    if event_id:
                        self.update_event_classification(event_id, classification)
                        successful_updates += 1
                        processed += 1
                except Exception as e:
                    logger.error(f"Error updating event {classification.get('id')}: {e}")
            
            logger.info(f"Completed batch: {successful_updates}/{len(classifications)} successful updates")
            logger.info(f"Progress: {processed}/{total_unclassified} ({processed/total_unclassified*100:.1f}%)")
        
        logger.info(f"Classification completed. Processed {processed} events.")

if __name__ == "__main__":
    import argparse
    from config import ANTHROPIC_API_KEY
    
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY environment variable is required")
    
    parser = argparse.ArgumentParser(description="Classify events using Anthropic API")
    parser.add_argument("--batch-size", type=int, default=CLASSIFICATION_BATCH_SIZE, 
                       help="Number of events to process in each batch")
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=getattr(logging, 'INFO'),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    classifier = EventClassifier(api_key=ANTHROPIC_API_KEY)
    classifier.classify_all_unclassified(batch_size=args.batch_size)
    
    print("Event classification completed.")