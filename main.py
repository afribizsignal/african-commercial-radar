# African Commercial Radar - Complete Pipeline with Database Configuration
# All sources, countries, and keywords are managed via Supabase tables

import os
import requests
import json
import re
import hashlib
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from supabase import create_client, Client

# ============================================
# CONFIGURATION (Loaded from Environment Variables)
# ============================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL")

# ============================================
# STEP 1: LOAD CONFIGURATIONS FROM DATABASE
# ============================================

def load_active_countries():
    """Load all active countries from database"""
    try:
        result = supabase.table("country_config")\
            .select("*")\
            .eq("active", True)\
            .execute()
        return result.data
    except Exception as e:
        print(f"⚠️ Error loading countries: {e}")
        return []

def load_active_sources():
    """Load all active sources from database"""
    try:
        result = supabase.table("source_config")\
            .select("*")\
            .eq("active", True)\
            .execute()
        return result.data
    except Exception as e:
        print(f"⚠️ Error loading sources: {e}")
        return []

def load_keywords():
    """Load active keywords for relevance filtering"""
    try:
        result = supabase.table("keyword_config")\
            .select("*")\
            .eq("active", True)\
            .execute()
        return result.data
    except Exception as e:
        print(f"⚠️ Error loading keywords: {e}")
        return []

# ============================================
# STEP 2: WEB SCRAPING
# ============================================

def fetch_url(url, timeout=30):
    """Fetch content from a URL with proper headers"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.text
        else:
            print(f"  ⚠️ URL returned {response.status_code}: {url}")
            return None
    except Exception as e:
        print(f"  ⚠️ Error fetching {url}: {e}")
        return None

# ============================================
# STEP 3: PARSERS
# ============================================

def parse_html_tenders(html, source_url, country):
    """Parse HTML pages for tender/procurement information"""
    if not html:
        return []
    
    soup = BeautifulSoup(html, 'html.parser')
    events = []
    
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        text = link.get_text(strip=True)
        
        if len(text) > 5 and any(word in text.lower() for word in ['tender', 'procurement', 'bid', 'supply', 'construction']):
            ref_match = re.search(r'(\d{4,})', text)
            ref = ref_match.group(1) if ref_match else None
            date_match = re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', text)
            date_str = date_match.group(1) if date_match else None
            
            events.append({
                'title': text[:200],
                'reference': ref,
                'date': date_str,
                'url': href if href.startswith('http') else source_url + href,
                'source_url': source_url,
                'country': country,
                'type': 'procurement'
            })
    
    return events[:20]

def parse_html_general(html, source_url, country):
    """Parse general HTML for announcements"""
    if not html:
        return []
    
    soup = BeautifulSoup(html, 'html.parser')
    events = []
    
    keywords = ['announce', 'approve', 'licence', 'permit', 'investment', 'project', 
               'expansion', 'contract', 'award', 'financing', 'fund', 'construction',
               'commission', 'launch', 'partner', 'agreement', 'development']
    
    for element in soup.find_all(['h1', 'h2', 'h3', 'p', 'div']):
        text = element.get_text(strip=True)
        if len(text) < 20:
            continue
        
        if any(word in text.lower() for word in keywords):
            if element.name in ['h1', 'h2', 'h3']:
                events.append({
                    'title': text[:200],
                    'description': '',
                    'url': source_url,
                    'source_url': source_url,
                    'country': country,
                    'type': 'announcement'
                })
            elif len(events) < 10:
                events.append({
                    'title': text[:150] + '...' if len(text) > 150 else text,
                    'description': text[:500],
                    'url': source_url,
                    'source_url': source_url,
                    'country': country,
                    'type': 'general'
                })
    
    return events[:15]

def parse_json_ocds(json_data, source_url, country):
    """Parse OCDS JSON data"""
    if not json_data:
        return []
    
    try:
        data = json.loads(json_data) if isinstance(json_data, str) else json_data
    except:
        return []
    
    events = []
    releases = data.get('releases', []) or data.get('data', {}).get('releases', [])
    
    for release in releases[:20]:
        tender = release.get('tender', {})
        buyer = release.get('buyer', {})
        value = tender.get('value', {})
        amount = value.get('amount') if value else None
        
        events.append({
            'title': tender.get('title', ''),
            'description': tender.get('description', '')[:500],
            'value': amount,
            'buyer': buyer.get('name', ''),
            'url': source_url,
            'source_url': source_url,
            'country': country,
            'type': 'procurement'
        })
    
    return events

PARSER_MAP = {
    'html_tenders': parse_html_tenders,
    'html_general': parse_html_general,
    'json_ocds': parse_json_ocds,
}

# ============================================
# STEP 4: AI EXTRACTION
# ============================================

def extract_event_with_ai(raw_event, country="Zambia"):
    """Extract structured event from raw data using AI"""
    
    text_to_analyze = raw_event.get('title', '') + ' ' + raw_event.get('description', '')
    
    prompt = f"""
You are a commercial intelligence analyst for Africa.

Extract the following information from the text below. If information is not present, use "UNKNOWN".

Return ONLY valid JSON with these exact fields:

{{
    "entity_name": "the main company or project mentioned",
    "sector": "Mining or Energy or Infrastructure or Regulation",
    "event_type": "Procurement or Financing or Regulation or Project or ESG",
    "what_changed": "summary of what happened (2-3 sentences)",
    "commercial_relevance": number from 0-100,
    "opportunity_flag": true or false,
    "risk_level": "Low or Medium or High",
    "action_window": "0-30 days or 1-3 months or 3-6 months or 6-12 months",
    "confidence": "Low or Medium or High",
    "next_catalyst": "what to watch next"
}}

TEXT TO ANALYZE:
{text_to_analyze[:3000]}
"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "google/gemini-2.0-flash-lite-preview-02-05",
        "messages": [
            {"role": "system", "content": "You extract commercial events from African business information. Return only valid JSON."},
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
            print(f"  ⚠️ AI error: {response.status_code}")
            return None
    except Exception as e:
        print(f"  ⚠️ AI extraction error: {e}")
        return None

# ============================================
# STEP 5: ENTITY RESOLUTION
# ============================================

def resolve_entity(entity_name, country, sector):
    """Find or create an entity"""
    if not entity_name or entity_name == "UNKNOWN":
        return None
    
    try:
        result = supabase.table("entities")\
            .select("entity_id")\
            .ilike("canonical_name", f"%{entity_name}%")\
            .execute()
        
        if len(result.data) > 0:
            return result.data[0]["entity_id"]
    except:
        pass
    
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
        print(f"  ⚠️ Entity creation error: {e}")
        return None

# ============================================
# STEP 6: STORE EVENT
# ============================================

def store_event(event_data, country="Zambia", raw_title=""):
    """Save extracted event to Supabase"""
    
    if not event_data:
        return False
    
    try:
        existing = supabase.table("events")\
            .select("event_id")\
            .eq("entity_name", event_data.get("entity_name", ""))\
            .eq("event_type", event_data.get("event_type", ""))\
            .execute()
        
        if len(existing.data) > 0:
            print(f"  ⏭️ Duplicate event found - skipping")
            return False
    except:
        pass
    
    entity_id = resolve_entity(
        event_data.get("entity_name", ""),
        country,
        event_data.get("sector", "Mining")
    )
    
    event_id = f"{country[:3].upper()}-{datetime.now().strftime('%Y')}-{str(int(datetime.now().timestamp()))[-4:]}"
    
    event_record = {
        "event_id": event_id,
        "detected_date": datetime.now().strftime("%Y-%m-%d"),
        "event_date": datetime.now().strftime("%Y-%m-%d"),
        "country": country,
        "entity_id": entity_id,
        "entity_name": event_data.get("entity_name", raw_title[:50] if raw_title else "UNKNOWN"),
        "event_type": event_data.get("event_type", "Project"),
        "what_changed": event_data.get("what_changed", raw_title[:200] if raw_title else ""),
        "opportunity_flag": event_data.get("opportunity_flag", False),
        "risk_level": event_data.get("risk_level", "Medium"),
        "commercial_relevance": event_data.get("commercial_relevance", 50),
        "confidence": event_data.get("confidence", "Medium"),
        "action_window": event_data.get("action_window", "1-3 months"),
        "next_catalyst": event_data.get("next_catalyst", ""),
        "source_url": event_data.get("source_url", ""),
        "verification_status": "pending",
        "review_status": "pending"
    }
    
    try:
        supabase.table("events").insert(event_record).execute()
        print(f"  ✅ Event stored: {event_id}")
        return True
    except Exception as e:
        print(f"  ⚠️ Database error: {e}")
        return False

# ============================================
# STEP 7: CLIENT WATCH RULE MATCHING
# ============================================

def match_client_watch_rules(event_data, country="Zambia"):
    """Check which clients should receive this event"""
    
    try:
        clients = supabase.table("clients")\
            .select("*")\
            .eq("active", True)\
            .execute()
    except:
        return []
    
    matched_clients = []
    
    for client in clients.data:
        if not client.get("receive_all_countries", True):
            if country not in client.get("countries", []):
                continue
        
        if not client.get("receive_all_sectors", True):
            if event_data.get("sector", "Mining") not in client.get("sectors", []):
                continue
        
        try:
            rules = supabase.table("client_watch_rules")\
                .select("*")\
                .eq("client_id", client["client_id"])\
                .eq("active", True)\
                .execute()
        except:
            rules = {"data": []}
        
        if len(rules.data) == 0:
            matched_clients.append(client["client_id"])
            continue
        
        for rule in rules.data:
            watch_type = rule.get("watch_type")
            watch_value = rule.get("watch_value", "").lower()
            
            if watch_type == "entity":
                if watch_value.lower() in event_data.get("entity_name", "").lower():
                    matched_clients.append(client["client_id"])
                    break
            elif watch_type == "keyword":
                text_to_check = (event_data.get("what_changed", "") + " " + 
                                event_data.get("entity_name", "")).lower()
                if watch_value.lower() in text_to_check:
                    matched_clients.append(client["client_id"])
                    break
            elif watch_type == "sector":
                if watch_value.lower() == event_data.get("sector", "").lower():
                    matched_clients.append(client["client_id"])
                    break
    
    return list(set(matched_clients))

# ============================================
# STEP 8: SEND ALERTS
# ============================================

def send_alert(event_data, matched_clients, country="Zambia"):
    """Send email alerts to matched clients"""
    if not RESEND_API_KEY:
        return
    
    score = event_data.get("commercial_relevance", 0)
    if score < 80:
        return
    
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
    <p><strong>Next Catalyst:</strong> {event_data.get('next_catalyst', 'Monitor for updates')}</p>
    <p><strong>Confidence:</strong> {event_data.get('confidence', 'Medium')}</p>
    """
    
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    
    for email in [ALERT_EMAIL]:
        data = {
            "from": "African Commercial Radar <onboarding@resend.dev>",
            "to": [email],
            "subject": subject,
            "html": html
        }
        try:
            requests.post("https://api.resend.com/emails", headers=headers, json=data)
            print(f"  📧 Alert sent to {email}")
        except:
            pass

# ============================================
# MAIN PIPELINE — CONFIGURATION DRIVEN
# ============================================

def run_pipeline():
    print(f"🚀 African Commercial Radar - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # Load configurations from database
    print("📋 Loading configurations from database...")
    countries = load_active_countries()
    sources = load_active_sources()
    keywords = load_keywords()
    
    print(f"   Active countries: {len(countries)}")
    print(f"   Active sources: {len(sources)}")
    print(f"   Active keywords: {len(keywords)}")
    
    if not sources:
        print("⚠️ No active sources found. Please add sources to source_config table.")
        return 0
    
    total_events = 0
    
    for source in sources:
        country = source.get('country', 'Zambia')
        source_name = source.get('source_name', 'Unknown')
        url = source.get('url', '')
        parser_type = source.get('parser_type', 'html_general')
        
        print(f"\n📡 Processing {source_name} ({country})...")
        
        if not url:
            print(f"  ⚠️ No URL for {source_name}")
            continue
        
        html = fetch_url(url)
        if not html:
            print(f"  ⚠️ No data from {source_name}")
            continue
        
        parser = PARSER_MAP.get(parser_type, parse_html_general)
        raw_events = parser(html, url, country)
        
        print(f"  📝 Found {len(raw_events)} candidate events")
        
        for raw in raw_events[:10]:
            print(f"  🤖 AI processing: {raw.get('title', '')[:60]}...")
            event = extract_event_with_ai(raw, country)
            
            if event:
                if store_event(event, country, raw.get('title', '')):
                    total_events += 1
                    matched = match_client_watch_rules(event, country)
                    if matched:
                        send_alert(event, matched, country)
    
    print(f"\n✅ Pipeline complete. Processed {total_events} events.")
    return total_events

# ============================================
# RUN THE PIPELINE
# ============================================

if __name__ == "__main__":
    run_pipeline()
