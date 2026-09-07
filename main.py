# African Commercial Radar - COMPLETE FIXED VERSION
# This version uses the SECRET key, fails loudly on errors,
# and actually uses country_config and keyword_config

import os
import requests
import json
import uuid
from urllib.parse import urljoin
from datetime import datetime
from bs4 import BeautifulSoup
from supabase import create_client, Client

print("=" * 70)
print("🚀 AFRICAN COMMERCIAL RADAR - FIXED VERSION")
print("=" * 70)

# ============================================
# STEP 1: SUPABASE CONNECTION (USING SECRET KEY)
# ============================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")

if not SUPABASE_URL:
    raise RuntimeError("❌ SUPABASE_URL is missing from environment variables!")

if not SUPABASE_SECRET_KEY:
    raise RuntimeError("❌ SUPABASE_SECRET_KEY is missing from environment variables!")

print(f"\n📡 Connecting to Supabase: {SUPABASE_URL[:30]}...")

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    print("✅ Connected to Supabase successfully!")
except Exception as e:
    print(f"❌ Failed to connect: {e}")
    raise

# ============================================
# STEP 2: DATABASE LOADER (FAILS LOUDLY)
# ============================================

def db_load(table_name, filters=None):
    """Load data from a Supabase table. Raises error on failure."""
    try:
        query = supabase.table(table_name).select("*")

        if filters:
            for column, value in filters.items():
                query = query.eq(column, value)

        result = query.execute()
        print(f"   ✅ {table_name}: {len(result.data)} rows")
        return result.data

    except Exception as e:
        print(f"   ❌ DATABASE ERROR: {table_name}")
        print(f"      {type(e).__name__}: {e}")
        raise  # Stop the pipeline so we see the error

# ============================================
# STEP 3: LOAD CONFIGURATIONS
# ============================================

print("\n📋 Loading configurations from database...")

countries = db_load("country_config", {"active": True})
sources = db_load("source_config", {"active": True})
keywords = db_load("keyword_config", {"active": True})

print(f"\n   Active countries: {len(countries)}")
print(f"   Active sources: {len(sources)}")
print(f"   Active keywords: {len(keywords)}")

if not sources:
    print("\n❌ No active sources found. Check that source_config has rows with active=true.")
    print("   Running diagnostic query...")
    try:
        all_sources = db_load("source_config")
        print(f"   Total sources in table: {len(all_sources)}")
    except Exception as e:
        print(f"   ❌ Could not check: {e}")
    raise RuntimeError("No active sources found — pipeline cannot continue.")

# ============================================
# STEP 4: KEYWORD SCORING (USES KEYWORD_CONFIG)
# ============================================

def score_relevance(text, keywords):
    """Score how relevant text is using keyword weights from database"""
    if not text or not keywords:
        return 0, []

    text_lower = text.lower()
    score = 0
    matches = []

    for kw in keywords:
        word = kw.get("keyword", "").lower()
        weight = kw.get("weight", 1)

        if word and word in text_lower:
            score += weight
            matches.append(word)

    return score, matches

# ============================================
# STEP 5: WEB SCRAPING
# ============================================

def fetch_url(url, timeout=30):
    """Fetch content from a URL"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        print(f"      🌐 Fetching: {url[:60]}...")
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            print(f"      ✅ Fetched {len(response.text)} bytes")
            return response.text
        else:
            print(f"      ⚠️ HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"      ⚠️ Error: {e}")
        return None

# ============================================
# STEP 6: PARSERS
# ============================================

def parse_html_general(html, source_url, country):
    """Parse HTML and extract text for AI analysis"""
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')

    # Remove script and style tags
    for script in soup(["script", "style"]):
        script.decompose()

    # Get all text
    text = soup.get_text(separator=" ", strip=True)

    # Clean up whitespace
    text = " ".join(text.split())

    # Get title
    title = ""
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True)

    # Also look for headings
    headings = []
    for h in soup.find_all(['h1', 'h2', 'h3']):
        h_text = h.get_text(strip=True)
        if h_text and len(h_text) > 10:
            headings.append(h_text)

    # Combine title + headings + first 5000 chars of body
    combined = title + " " + " ".join(headings) + " " + text[:5000]

    if len(combined) < 50:
        return []

    return [{
        "title": title,
        "body": combined,
        "url": source_url,
        "source_url": source_url,
        "country": country,
        "type": "general"
    }]

def parse_html_tenders(html, source_url, country):
    """Parse HTML for tender information with more detail"""
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')

    # Remove script and style tags
    for script in soup(["script", "style"]):
        script.decompose()

    text = " ".join(soup.get_text(separator=" ", strip=True).split())

    title = ""
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True)

    # Look for tender-specific keywords
    tender_keywords = ['tender', 'procurement', 'bid', 'supply', 'contract', 'construction',
                       'invitation', 'quotation', 'rfp', 'rfq', 'eo i']

    # Only process if it seems tender-related
    if not any(kw in text.lower() for kw in tender_keywords):
        return []

    # Try to find headings
    headings = []
    for h in soup.find_all(['h1', 'h2', 'h3']):
        h_text = h.get_text(strip=True)
        if h_text and len(h_text) > 10:
            headings.append(h_text)

    combined = title + " " + " ".join(headings) + " " + text[:8000]

    return [{
        "title": title or "Tender Document",
        "body": combined,
        "url": source_url,
        "source_url": source_url,
        "country": country,
        "type": "tender"
    }]

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

        title = tender.get('title', '')
        description = tender.get('description', '')
        amount = value.get('amount') if value else None

        combined = f"Tender: {title} {description} Value: {amount}"

        events.append({
            "title": title or "OCDS Tender",
            "body": combined[:8000],
            "url": source_url,
            "source_url": source_url,
            "country": country,
            "type": "ocds",
            "amount": amount
        })

    return events

PARSER_MAP = {
    'html_general': parse_html_general,
    'html_tenders': parse_html_tenders,
    'json_ocds': parse_json_ocds,
}

# ============================================
# STEP 7: AI EXTRACTION
# ============================================

def extract_event_with_ai(raw_event, country="Zambia"):
    """Send FULL content to AI for extraction"""
    text_to_analyze = raw_event.get('body', raw_event.get('title', ''))

    if not text_to_analyze or len(text_to_analyze) < 50:
        return None

    prompt = f"""
You are a commercial intelligence analyst for Africa.

Extract the following information from the text below. If information is not present, use "UNKNOWN".

Return ONLY valid JSON with these exact fields:

{{
    "entity_name": "the main company or project mentioned",
    "sector": "Mining or Energy or Infrastructure or Regulation or ESG",
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
{text_to_analyze[:4000]}
"""

    if not OPENROUTER_API_KEY:
        print("      ⚠️ No OpenRouter API key set")
        return None

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
            print(f"      ⚠️ AI error: {response.status_code}")
            return None
    except Exception as e:
        print(f"      ⚠️ AI error: {e}")
        return None

# ============================================
# STEP 8: STORE EVENT
# ============================================

def store_event(event_data, country="Zambia", raw_title=""):
    """Save extracted event to Supabase using UUID"""
    if not event_data:
        return False

    # Use UUID for unique event ID
    event_id = str(uuid.uuid4())

    # Try to get entity name
    entity_name = event_data.get("entity_name", "")
    if not entity_name or entity_name == "UNKNOWN":
        entity_name = raw_title[:50] if raw_title else "Unknown Entity"

    event_record = {
        "event_id": event_id,
        "detected_date": datetime.now().strftime("%Y-%m-%d"),
        "event_date": datetime.now().strftime("%Y-%m-%d"),
        "country": country,
        "entity_name": entity_name,
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
        print(f"      ✅ Event stored: {event_id[:8]}...")
        return True
    except Exception as e:
        print(f"      ❌ Database error: {e}")
        return False

# ============================================
# STEP 9: MAIN PIPELINE
# ============================================

# Get API keys from environment
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

print("\n" + "=" * 70)
print("🔍 PIPELINE STARTING")
print("=" * 70)

total_events = 0

# Process each country
for country_config in countries:
    country_name = country_config.get("country_name", "Unknown")
    print(f"\n📍 Processing country: {country_name}")

    # Get sources for this country
    country_sources = [s for s in sources if s.get("country") == country_name]

    if not country_sources:
        print(f"   ⚠️ No sources configured for {country_name}")
        continue

    print(f"   Found {len(country_sources)} sources")

    for source in country_sources:
        source_name = source.get('source_name', 'Unknown')
        url = source.get('url', '')
        parser_type = source.get('parser_type', 'html_general')

        print(f"\n   📡 Processing: {source_name}")

        if not url:
            print(f"      ⚠️ No URL for {source_name}")
            continue

        html = fetch_url(url)

        if not html:
            print(f"      ⚠️ No data from {source_name}")
            continue

        parser = PARSER_MAP.get(parser_type, parse_html_general)
        raw_events = parser(html, url, country_name)

        print(f"      📝 Found {len(raw_events)} candidate items")

        # Apply keyword scoring
        for raw in raw_events[:10]:
            text_to_score = raw.get('body', raw.get('title', ''))
            score, matches = score_relevance(text_to_score, keywords)

            if score < 5:  # Minimum relevance threshold
                print(f"      ⏭️ Low relevance score: {score} (minimum: 5)")
                continue

            print(f"      🤖 AI processing: {raw.get('title', '')[:50]}... (score: {score})")

            event = extract_event_with_ai(raw, country_name)

            if event:
                if store_event(event, country_name, raw.get('title', '')):
                    total_events += 1

# ============================================
# FINAL SUMMARY
# ============================================

print("\n" + "=" * 70)
print("✅ PIPELINE COMPLETE")
print("=" * 70)
print(f"\n📊 Processed {total_events} events successfully.")
print("=" * 70)
