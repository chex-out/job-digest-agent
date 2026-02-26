import os
import json
import yaml
import asyncio
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession
import anthropic
import requests
from exa_py import Exa

# ── Load Config ───────────────────────────────────────────────────────────────

with open("config/user_profile.yaml", "r") as f:
    profile = yaml.safe_load(f)

# ── API Clients ───────────────────────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
exa = Exa(api_key=os.environ["EXA_API_KEY"])

# ── Telegram Source ───────────────────────────────────────────────────────────

async def fetch_telegram_listings():
    """Fetch job listings from Telegram bot DMs."""
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session_string = os.environ["TELEGRAM_SESSION_STRING"]
    bot_username = profile["telegram"]["bot_username"]
    lookback_days = profile["telegram"]["lookback_days"]

    listings = []
    cutoff = datetime.now() - timedelta(days=lookback_days)

    async with TelegramClient(StringSession(session_string), api_id, api_hash) as client:
        async for message in client.iter_messages(bot_username, offset_date=cutoff, reverse=True):
            if message.text:
                listings.append(message.text)

    print(f"  [Telegram] Fetched {len(listings)} messages")
    return listings


def parse_telegram_listing(raw_text):
    """Use Claude to parse a raw Telegram message into structured listing data."""
    prompt = f"""Extract job listing details from this Telegram message and return a JSON object with these fields:
- job_title (string)
- company (string)  
- location (string)
- apply_url (string)
- description (string, any additional details available)

If a field is not found, use null. Return only valid JSON, no other text.

Message:
{raw_text}"""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        return json.loads(response.content[0].text)
    except json.JSONDecodeError:
        return None


# ── Exa Source ────────────────────────────────────────────────────────────────

def fetch_exa_listings():
    """Fetch job listings from Exa using search terms from user profile."""
    search_terms = profile["search"]["search_terms"]
    location = profile["search"]["location"]
    results_per_term = profile["search"]["results_per_term"]
    listings = []

    for term in search_terms:
        query = f"{term} job listing {location}"
        print(f"  [Exa] Searching: {query}")
        try:
            results = exa.search_and_contents(
                query,
                num_results=results_per_term,
                use_autoprompt=True,
                include_domains=["linkedin.com", "jobstreet.com", "glassdoor.com", "indeed.com"],
                text={"max_characters": 2000}
            )
            for result in results.results:
                listings.append({
                    "job_title": result.title,
                    "company": None,
                    "location": location,
                    "apply_url": result.url,
                    "description": result.text
                })
        except Exception as e:
            print(f"  [Exa] Error for term '{term}': {e}")

    print(f"  [Exa] Fetched {len(listings)} listings")
    return listings


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate_listings(listings):
    """Remove duplicate listings based on URL."""
    seen_urls = set()
    unique = []
    for listing in listings:
        url = listing.get("apply_url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(listing)
    print(f"  [Dedup] {len(unique)} unique listings after deduplication")
    return unique


# ── Redirect Resolver ─────────────────────────────────────────────────────────

def resolve_redirect(url):
    """Follow redirects to get the final URL."""
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        return response.url
    except Exception:
        return url


# ── Company Context ───────────────────────────────────────────────────────────

def fetch_company_context(company_name):
    """Fetch Glassdoor and NodeFlair context for a company via Exa."""
    context = {}
    sources = profile.get("review_sources", [])

    for source in sources:
        query = f"{company_name} Singapore {source} reviews"
        try:
            results = exa.search_and_contents(
                query,
                num_results=2,
                include_domains=[f"{source}.com"] if source != "nodeflair" else ["nodeflair.com"],
                text={"max_characters": 1500}
            )
            if results.results:
                context[source] = results.results[0].text
        except Exception as e:
            print(f"  [Context] Error fetching {source} for {company_name}: {e}")

    return context


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_listing(listing, company_context):
    """Score a listing on role fit and skills match using Claude."""

    with open("config/resume.md", "r") as f:
        resume = f.read()

    with open("config/user_profile.yaml", "r") as f:
        profile_text = f.read()

    with open("config/feedback_log.yaml", "r") as f:
        feedback = f.read()

    context_text = ""
    for source, text in company_context.items():
        context_text += f"\n### {source.capitalize()} Reviews\n{text}\n"

    prompt = f"""You are evaluating a job listing for a candidate. Score the listing on two dimensions, each out of 10.

## Candidate Profile
{profile_text}

## Candidate Resume
{resume}

## Feedback & Preferences (from past reactions)
{feedback}

## Job Listing
Title: {listing.get('job_title')}
Company: {listing.get('company')}
Location: {listing.get('location')}
URL: {listing.get('apply_url')}
Description: {listing.get('description')}

## Company Context
{context_text if context_text else "No company context available."}

## Instructions
Score the listing on:
1. role_fit (1-10): How well does this role match the candidate's desired role, seniority, progression goals, preferred industries, and company type preferences?
2. skills_match (1-10): How well does the candidate's current skillset match the requirements in the JD?

Also provide:
- a brief rationale for each score (1-2 sentences)
- a company_snapshot: 2-3 sentences summarising culture, management quality, and growth opportunities based on available reviews. Note if reviews are sparse.
- flag any red flags or standout positives

Return a JSON object only with these fields:
{{
  "role_fit": <int>,
  "role_fit_rationale": <string>,
  "skills_match": <int>,
  "skills_match_rationale": <string>,
  "company_snapshot": <string>,
  "red_flags": <string or null>,
  "standout_positives": <string or null>
}}"""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        return json.loads(response.content[0].text)
    except json.JSONDecodeError:
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("Starting scout agent...")
    all_listings = []

    # Fetch from Telegram
    if profile.get("telegram", {}).get("enabled"):
        print("Fetching from Telegram...")
        raw_messages = await fetch_telegram_listings()
        for msg in raw_messages:
            parsed = parse_telegram_listing(msg)
            if parsed:
                if parsed.get("apply_url"):
                    parsed["apply_url"] = resolve_redirect(parsed["apply_url"])
                all_listings.append(parsed)

    # Fetch from Exa
    print("Fetching from Exa...")
    exa_listings = fetch_exa_listings()
    all_listings.extend(exa_listings)

    # Deduplicate
    all_listings = deduplicate_listings(all_listings)

    # Score each listing
    print(f"Scoring {len(all_listings)} listings...")
    scored_listings = []
    for i, listing in enumerate(all_listings):
        print(f"  Scoring {i+1}/{len(all_listings)}: {listing.get('job_title')} at {listing.get('company')}")
        company_context = fetch_company_context(listing.get("company", ""))
        scores = score_listing(listing, company_context)
        if scores:
            listing.update(scores)
            scored_listings.append(listing)

    # Sort by role fit descending
    scored_listings.sort(key=lambda x: x.get("role_fit", 0), reverse=True)

    # Save results
    os.makedirs("output", exist_ok=True)
    output_path = f"output/scout_results_{datetime.now().strftime('%y%m%d')}.json"
    with open(output_path, "w") as f:
        json.dump(scored_listings, f, indent=2)

    print(f"Scout complete. {len(scored_listings)} listings scored and saved to {output_path}")
    return scored_listings


if __name__ == "__main__":
    asyncio.run(main())


