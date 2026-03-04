import os
import sys
import json
import yaml
import argparse
import anthropic
import re
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from exa_py import Exa

# ── Load Config ───────────────────────────────────────────────────────────────

with open("config/user_profile.yaml", "r") as f:
    profile = yaml.safe_load(f)

with open("config/resume.md", "r") as f:
    base_resume = f.read()

with open("config/cover_letter.md", "r") as f:
    base_cover_letter = f.read()

# ── API Clients ───────────────────────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
exa = Exa(api_key=os.environ["EXA_API_KEY"])

# ── Google Drive ──────────────────────────────────────────────────────────────

def get_drive_service():
    token_data = json.loads(os.environ["GOOGLE_OAUTH_TOKEN"])
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"]
    )
    if creds.expired:
        creds.refresh(Request())
    drive_service = build("drive", "v3", credentials=creds)
    docs_service = build("docs", "v1", credentials=creds)
    return drive_service, docs_service


def create_application_folder(drive_service, company, role):
    date_prefix = datetime.now().strftime("%y%m%d")
    folder_name = f"{date_prefix} {company} {role}"
    parent_folder_id = profile["google_drive"]["job_applications_folder_id"]

    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id]
    }

    folder = drive_service.files().create(
        body=folder_metadata,
        fields="id, name"
    ).execute()

    print(f"  [Drive] Created folder: {folder['name']}")
    return folder["id"], folder["name"]


def create_google_doc(drive_service, docs_service, folder_id, title, content):
    doc_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id]
    }

    doc = drive_service.files().create(
        body=doc_metadata,
        fields="id"
    ).execute()

    doc_id = doc["id"]

    content_chunks = [content[i:i+40000] for i in range(0, len(content), 40000)]
    requests = []
    for chunk in reversed(content_chunks):
        requests.append({
            "insertText": {
                "location": {"index": 1},
                "text": chunk
            }
        })

    if requests:
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests}
        ).execute()

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    print(f"  [Docs] Created doc: {title}")
    return doc_id, doc_url


# ── Fetch Listing ─────────────────────────────────────────────────────────────

def fetch_listing(url):
    """Fetch and parse a single job listing URL."""
    print(f"  Fetching: {url[:80]}")
    try:
        result = exa.get_contents([url], text={"max_characters": 3000})
        page = result.results[0] if result.results else None
        description = (page.text or "").strip() if page else ""
        title = (page.title or "").strip() if page else ""
        partial_data = len(description) < 200

        if partial_data:
            print(f"  [Scout] Sparse content — using fallback")
            fallback_prompt = f"""A job listing URL was fetched but returned little or no content.

URL: {url}
Page title: {title}

Based on the URL and title alone, extract what you can and return a JSON object with:
- job_title (string)
- company (string)
- location (string, default to "Singapore" if unknown)
- description (string, write "Limited data available — please review listing directly." plus any inferences from the URL and title)
- partial_data (boolean, always true)

Return only valid JSON, no code fences."""

            response = anthropic_client.messages.create(
                model="claude-opus-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": fallback_prompt}]
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```json\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
            try:
                listing = json.loads(raw)
                listing["apply_url"] = url
            except json.JSONDecodeError:
                listing = {
                    "job_title": title or "Unknown Role",
                    "company": None,
                    "location": "Singapore",
                    "apply_url": url,
                    "description": "Limited data available — please review listing directly.",
                    "partial_data": True
                }
        else:
            listing = {
                "job_title": title,
                "company": None,
                "location": "Singapore",
                "apply_url": url,
                "description": description,
                "partial_data": False
            }

        # Clean up LinkedIn-style titles
        raw_title = listing.get("job_title", "")
        if " hiring " in raw_title:
            listing["company"] = raw_title.split(" hiring ")[0].strip()
            listing["job_title"] = raw_title.split(" hiring ")[1].split(" in ")[0].strip()
        if " | LinkedIn" in listing.get("job_title", ""):
            listing["job_title"] = listing["job_title"].split(" | LinkedIn")[0].strip()

        return listing

    except Exception as e:
        print(f"  [Error] Failed to fetch listing: {e}")
        return None


# ── Score Listing ─────────────────────────────────────────────────────────────

def fetch_company_context(company_name):
    """Fetch Glassdoor and NodeFlair context for a company via Exa."""
    if not company_name:
        return {}
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


def score_listing(listing, company_context):
    """Score a listing on role fit and skills match."""
    with open("config/feedback_log.yaml", "r") as f:
        feedback = f.read()

    context_text = ""
    for source, text in company_context.items():
        context_text += f"\n### {source.capitalize()} Reviews\n{text}\n"

    prompt = f"""You are evaluating a job listing for a candidate. Score the listing on two dimensions, each out of 10.

## Candidate Profile
{yaml.dump(profile)}

## Candidate Resume
{base_resume}

## Feedback & Preferences
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
Score on:
1. role_fit (1-10)
2. skills_match (1-10)

Provide:
- role_fit_rationale: max 15 words
- skills_match_rationale: max 15 words
- company_snapshot: max 20 words
- red_flags: max 10 words or null
- standout_positives: max 10 words or null

Return JSON only, no code fences:
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

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [Score] Failed to parse score response")
        return None


# ── Prepare Materials ─────────────────────────────────────────────────────────

def tailor_resume(listing):
    with open("config/application_feedback_log.yaml", "r") as f:
        app_feedback = f.read()

    prompt = f"""You are tailoring a candidate's resume for a specific job listing.

## Learned Preferences
{app_feedback}

## Base Resume
{base_resume}

## Job Listing
Title: {listing.get('job_title')}
Company: {listing.get('company')}
Description: {listing.get('description')}

## Instructions
Tailor the resume for this specific role by:
1. Mirroring language and keywords from the JD where they accurately reflect the candidate's experience
2. Reordering bullet points within each role to lead with the most relevant experience
3. Adjusting the professional summary to reflect this specific role and company
4. Do NOT invent experience, metrics, or skills the candidate does not have
5. Keep all dates, titles, and company names unchanged
6. Return the full tailored resume in Markdown format

After the resume, add a section titled "## Changes Made" that lists:
- Each specific change made and why
- Any areas where you made assumptions that the candidate should verify before submitting
- Any gaps between the JD requirements and the candidate's experience worth addressing in the cover letter"""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def tailor_cover_letter(listing):
    with open("config/application_feedback_log.yaml", "r") as f:
        app_feedback = f.read()

    prompt = f"""You are tailoring a candidate's cover letter for a specific job listing.

## Learned Preferences
{app_feedback}

## Base Cover Letter (including agent instructions at the bottom)
{base_cover_letter}

## Job Listing
Title: {listing.get('job_title')}
Company: {listing.get('company')}
Description: {listing.get('description')}

## Scores
Role Fit: {listing.get('role_fit')}/10 — {listing.get('role_fit_rationale')}
Skills Match: {listing.get('skills_match')}/10 — {listing.get('skills_match_rationale')}

## Instructions
- Replace [ROLE] and [COMPANY] with the actual role and company name
- Adjust emphasis based on the agent instructions in the base cover letter
- Mirror key language from the JD where it accurately reflects the candidate's experience
- Keep the tone formal, precise, and direct — avoid generic opener phrases
- Do NOT invent experience or skills the candidate does not have
- Keep the letter to 4 paragraphs maximum
- Return the final cover letter only, no preamble

After the cover letter, add a section titled "## Changes Made" listing:
- What you adjusted and why
- Any assumptions made that the candidate should verify"""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def create_notes(listing, resume_changes, cover_letter_changes):
    prompt = f"""Summarise the following into a concise application notes document for the candidate to review.

## Job Details
Title: {listing.get('job_title')}
Company: {listing.get('company')}
URL: {listing.get('apply_url')}
Role Fit: {listing.get('role_fit')}/10
Skills Match: {listing.get('skills_match')}/10
Role Fit Rationale: {listing.get('role_fit_rationale')}
Skills Match Rationale: {listing.get('skills_match_rationale')}
Company Snapshot: {listing.get('company_snapshot')}
Red Flags: {listing.get('red_flags')}
Standout Positives: {listing.get('standout_positives')}

## Resume Changes
{resume_changes}

## Cover Letter Changes
{cover_letter_changes}

## Instructions
Produce a concise notes document with these sections:
1. Role Summary — 2 sentences on the role and why it scored well
2. Company Snapshot — from the review data above
3. Why Work Here — 3-4 bullet points answering "Why would you like to work at this company?" Draw from company snapshot, standout positives, industry, mission, and market position. Frame from the candidate's perspective. One sentence per bullet.
4. Resume Changes — bullet points of key changes and anything needing verification
5. Cover Letter Changes — bullet points of key changes and assumptions to verify
6. Gaps to Address — any JD requirements not covered by the resume
7. Red Flags — if any

Keep it scannable. Reviewable in under 2 minutes."""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Prepare application materials for a single job listing.")
    parser.add_argument("url", help="The job listing URL to prepare materials for")
    parser.add_argument("--force", action="store_true", help="Prepare materials even if score is below threshold")
    args = parser.parse_args()

    print(f"Starting prepare_single agent for: {args.url}")

    # Fetch listing
    listing = fetch_listing(args.url)
    if not listing:
        print("Failed to fetch listing. Exiting.")
        sys.exit(1)

    print(f"  Listing: {listing.get('job_title')} at {listing.get('company')}")

    # Score listing
    print("  Scoring listing...")
    company_context = fetch_company_context(listing.get("company", ""))
    scores = score_listing(listing, company_context)
    if not scores:
        print("  Failed to score listing. Exiting.")
        sys.exit(1)

    listing.update(scores)
    threshold = profile["scoring"]["preparer_threshold"]

    print(f"  Role Fit: {listing['role_fit']}/10 — {listing['role_fit_rationale']}")
    print(f"  Skills Match: {listing['skills_match']}/10 — {listing['skills_match_rationale']}")

    if listing["role_fit"] < threshold and not args.force:
        print(f"  Role fit {listing['role_fit']}/10 is below threshold {threshold}/10.")
        print(f"  Run with --force to prepare materials anyway.")
        sys.exit(0)

    # Prepare materials
    drive_service, docs_service = get_drive_service()
    company = listing.get("company") or "Unknown Company"
    role = listing.get("job_title") or "Unknown Role"

    folder_id, folder_name = create_application_folder(drive_service, company, role)

    print("  Tailoring resume...")
    tailored_resume_full = tailor_resume(listing)
    resume_parts = tailored_resume_full.split("## Changes Made")
    tailored_resume = resume_parts[0].strip()
    resume_changes = resume_parts[1].strip() if len(resume_parts) > 1 else ""

    print("  Tailoring cover letter...")
    tailored_cover_full = tailor_cover_letter(listing)
    cover_parts = tailored_cover_full.split("## Changes Made")
    tailored_cover = cover_parts[0].strip()
    cover_changes = cover_parts[1].strip() if len(cover_parts) > 1 else ""

    print("  Creating notes...")
    notes_content = create_notes(listing, resume_changes, cover_changes)

    create_google_doc(drive_service, docs_service, folder_id, f"{folder_name} — Resume", tailored_resume)
    create_google_doc(drive_service, docs_service, folder_id, f"{folder_name} — Cover Letter", tailored_cover)
    create_google_doc(drive_service, docs_service, folder_id, f"{folder_name} — Notes", notes_content)

    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    print(f"\nDone. Materials ready at: {folder_url}")


if __name__ == "__main__":
    main()
