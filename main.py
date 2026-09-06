# African Commercial Radar - Main Pipeline
# This script runs daily to discover, extract, and process commercial events

import os
import requests
import json
from datetime import datetime, timedelta
from supabase import create_client, Client

# ============================================
# CONFIGURATION
# ============================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL")

# ============================================
# COUNTRY CONFIGURATION (Add new countries here)
# ============================================

COUNTRIES = {
    "Zambia": {
        "active": True,
        "sources": [
            {"name": "e-GP Procurement", "url": "https://eprocure.zppa.org.zm/epps/prepareAdvancedSearch.do?type=cft", "type": "procurement"},
            {"name": "Infrastructure Portal", "url": "https://www.infrastructuretransparencyzambia.org/projects", "type": "infrastructure"},
        ]
    },
    "Tanzania": {
        "active": False,  # Set to True when ready
        "sources": [
            {"name": "NeST Procurement", "url": "https://data.nest.go.tz/ocds", "type": "procurement"},
        ]
    },
    "Ghana": {
        "active": False,  # Set to True when ready
        "sources": [
            {"name": "Minerals Commission", "url": "https://www.mincom.gov.gh", "type": "mining"},
        ]
    }
}

# ============================================
# STEP 1: DISCOVERY - Fetch Sources
# ============================================

def fetch_source(url):
    """Fetch data from a source URL"""
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            return response.text
        else:
            print(f"Source returned: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching source: {e}")
        return None

# ============================================
# STEP 2: AI EXTRACTION
# ============================================

def extract_event_with_ai(text, source_url, country="Zambia"):
    """Send text to AI for structured event extraction"""
    
    prompt = f"""
You are a commercial intelligence analyst for Africa.

Extract the following information from the text below. If information is not present, use "UNKNOWN".

Return ONLY valid JSON with these exact fields:

{{
    "entity_name": "name of company or project",
    "country": "{country}",
    "sector": "Mining or Energy or Infrastructure",
    "event_type": "Procurement or Financing or Regulation or Project or ESG",
    "what_changed": "summary of what happened (2-3 sentences)",
    "commercial_relevance": number from 0-100,
    "opportunity_flag": true or false,
    "risk_level": "Low or Medium or High",
    "action_window": "0-30 days or 1-3 months or 3-6 months or 6-12 months",
    "confidence": "Low or Medium or High",
    "source_url": "{source_url}"
}}

TEXT TO ANALYZE:
{text[:3000]}
"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "google/gemini-2.0-flash-lite-preview-02-05",
        "messages": [
            {"role": "system", "content": "You extract commercial events from African business news. Return only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 500
    }
    
    try:
        response = requests.post(OPENROUTER_URL, headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content)
        else:
            print(f"AI error: {response.status_code}")
            return None
    except Exception as e:
        print(f"AI extraction error: {e}")
        return None

# ============================================
# STEP 3: ENTITY RESOLUTION
# ============================================

def resolve_entity(entity_name, country, sector):
    """Find or create an entity"""
    if not entity_name or entity_name == "UNKNOWN":
        return None
    
    # Check if entity exists
    try:
        result = supabase.table("entities")\
            .select("entity_id")\
            .ilike("canonical_name", f"%{entity_name}%")\
            .execute()
        
        if len(result.data) > 0:
            return result.data[0]["entity_id"]
    except:
        pass
    
    # Create new entity
    entity_id = f"{country[:3].upper()}-{sector[:3].upper()}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        supabase.table("entities").insert({
            "entity_id": entity_id,
            "canonical_name": entity_name,
            "entity_type": "Project",
            "country": country,
            "sector": sector,
            "aliases": [entity_name]
        }).execute()
        return entity_id
    except Exception as e:
        print(f"Entity creation error: {e}")
        return None

# ============================================
# STEP 4: STORE EVENT
# ============================================

def store_event(event_data, country="Zambia"):
    """Save extracted event to Supabase"""
    
    if not event_data:
        return False
    
    # Check for duplicates
    try:
        existing = supabase.table("events")\
            .select("event_id")\
            .eq("entity_name", event_data.get("entity_name", ""))\
            .eq("event_type", event_data.get("event_type", ""))\
            .execute()
        
        if len(existing.data) > 0:
            print("Duplicate event found - skipping")
            return False
    except:
        pass
    
    # Resolve entity
    entity_id = resolve_entity(
        event_data.get("entity_name", ""),
        country,
        event_data.get("sector", "Mining")
    )
    
    # Generate event ID
    event_id = f"{country[:3].upper()}-{datetime.now().strftime('%Y')}-{str(int(datetime.now().timestamp()))[-4:]}"
    
    event_record = {
        "event_id": event_id,
        "detected_date": datetime.now().strftime("%Y-%m-%d"),
        "event_date": datetime.now().strftime("%Y-%m-%d"),
        "country": country,
        "entity_id": entity_id,
        "entity_name": event_data.get("entity_name", "UNKNOWN"),
        "event_type": event_data.get("event_type", "Project"),
        "what_changed": event_data.get("what_changed", ""),
        "opportunity_flag": event_data.get("opportunity_flag", False),
        "risk_level": event_data.get("risk_level", "Medium"),
        "commercial_relevance": event_data.get("commercial_relevance", 50),
        "confidence": event_data.get("confidence", "Medium"),
        "action_window": event_data.get("action_window", "1-3 months"),
        "source_url": event_data.get("source_url", ""),
        "verification_status": "pending",
        "review_status": "pending"
    }
    
    try:
        supabase.table("events").insert(event_record).execute()
        print(f"Event stored: {event_id}")
        return True
    except Exception as e:
        print(f"Database error: {e}")
        return False

# ============================================
# STEP 5: CLIENT WATCH RULE MATCHING
# ============================================

def match_client_watch_rules(event_data, country="Zambia"):
    """Check which clients should receive this event"""
    
    # Get all active clients
    try:
        clients = supabase.table("clients")\
            .select("*")\
            .eq("active", True)\
            .execute()
    except:
        return []
    
    matched_clients = []
    
    for client in clients.data:
        # Check if client wants all countries or this specific country
        if not client.get("receive_all_countries", True):
            if country not in client.get("countries", []):
                continue
        
        # Check if client wants all sectors or this specific sector
        if not client.get("receive_all_sectors", True):
            if event_data.get("sector", "Mining") not in client.get("sectors", []):
                continue
        
        # Check watch rules
        try:
            rules = supabase.table("client_watch_rules")\
                .select("*")\
                .eq("client_id", client["client_id"])\
                .eq("active", True)\
                .execute()
        except:
            rules = {"data": []}
        
        # If no rules, client receives everything (default)
        if len(rules.data) == 0:
            matched_clients.append(client["client_id"])
            continue
        
        # Check if any rule matches
        for rule in rules.data:
            watch_type = rule.get("watch_type")
            watch_value = rule.get("watch_value", "").lower()
            
            if watch_type == "entity":
                if watch_value.lower() in event_data.get("entity_name", "").lower():
                    matched_clients.append(client["client_id"])
                    break
            elif watch_type == "keyword":
                # Check if keyword appears in what_changed or entity_name
                text_to_check = (event_data.get("what_changed", "") + " " + 
                                event_data.get("entity_name", "")).lower()
                if watch_value.lower() in text_to_check:
                    matched_clients.append(client["client_id"])
                    break
            elif watch_type == "sector":
                if watch_value.lower() == event_data.get("sector", "").lower():
                    matched_clients.append(client["client_id"])
                    break
            elif watch_type == "event_type":
                if watch_value.lower() == event_data.get("event_type", "").lower():
                    matched_clients.append(client["client_id"])
                    break
    
    return list(set(matched_clients))  # Remove duplicates

# ============================================
# STEP 6: SEND ALERTS
# ============================================

def send_alert(event_data, matched_clients, country="Zambia"):
    """Send email alerts to matched clients"""
    if not RESEND_API_KEY:
        print("No Resend API key - skipping alert")
        return
    
    score = event_data.get("commercial_relevance", 0)
    if score < 80:
        print(f"Score {score} below threshold - no alert")
        return
    
    # Get client emails
    client_emails = []
    try:
        # For MVP, we send to the owner's email
        # In production, you would query client emails
        client_emails = [ALERT_EMAIL]
    except:
        client_emails = [ALERT_EMAIL]
    
    subject = f"🔴 HIGH PRIORITY: {event_data.get('entity_name', 'Event')} - {country}"
    
    html = f"""
    <h2>African Commercial Radar — High Priority Alert</h2>
    
    <p><strong>Country:</strong> {country}</p>
    <p><strong>Entity:</strong> {event_data.get('entity_name', 'Unknown')}</p>
    <p><strong>Event Type:</strong> {event_data.get('event_type', 'Unknown')}</p>
    <p><strong>Commercial Relevance:</strong> {score}/100</p>
    <p><strong>Risk Level:</strong> {event_data.get('risk_level', 'Medium')}</p>
    <p><strong>Action Window:</strong> {event_data.get('action_window', 'Unknown')}</p>
    <p><strong>What Changed:</strong> {event_data.get('what_changed', '')}</p>
    <p><strong>Source:</strong> <a href="{event_data.get('source_url', '#')}">View Source</a></p>
    <p><strong>Confidence:</strong> {event_data.get('confidence', 'Medium')}</p>
    
    <hr>
    <p><em>Reply to this email to Ask Analyst for more details.</em></p>
    """
    
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    
    for email in client_emails:
        data = {
            "from": "African Commercial Radar <onboarding@resend.dev>",
            "to": [email],
            "subject": subject,
            "html": html
        }
        try:
            response = requests.post("https://api.resend.com/emails", headers=headers, json=data)
            if response.status_code == 200:
                print(f"Alert sent to {email}")
            else:
                print(f"Email error: {response.status_code}")
        except Exception as e:
            print(f"Email send error: {e}")

# ============================================
# MAIN PIPELINE
# ============================================

def run_pipeline():
    print(f"🚀 African Commercial Radar - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    events_processed = 0
    
    # Process each active country
    for country, config in COUNTRIES.items():
        if not config.get("active", False):
            print(f"⏭️ Skipping {country} (not active)")
            continue
        
        print(f"📡 Processing {country}...")
        
        for source in config.get("sources", []):
            print(f"  📡 Fetching {source['name']}...")
            data = fetch_source(source["url"])
            
            if data:
                print(f"  🤖 AI processing {source['name']}...")
                event = extract_event_with_ai(data[:2000], source["url"], country)
                
                if event:
                    events_processed += 1
                    if store_event(event, country):
                        # Match client watch rules
                        matched_clients = match_client_watch_rules(event, country)
                        if matched_clients:
                            send_alert(event, matched_clients, country)
    
    print(f"✅ Pipeline complete. Processed {events_processed} events.")
    return events_processed

# ============================================
# RUN THE PIPELINE
# ============================================

if __name__ == "__main__":
    run_pipeline()
