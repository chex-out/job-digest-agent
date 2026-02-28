import os
import json
import yaml
import anthropic
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Load Config ───────────────────────────────────────────────────────────────

with open("config/user_profile.yaml", "r") as f:
    profile = yaml.safe_load(f)

with open("config/resume.md", "r") as f:
    base_resume = f.read()

with open("config/cover_letter.md", "r") as f:
    base_cover_letter = f.read()

# ── API Clients ───────────────────────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def get_drive_service():
    """Authenticate using OAuth and return Google Drive and Docs service clients."""
    token_data = json.loads(os.environ["GOOGLE_OAUTH_TOKEN"])

    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"]
    )

    # Refresh token if expired
    if creds.expired:
        creds.refresh(Request())

    drive_service = build("drive", "v3", credentials=creds)
    docs_service = build("docs", "v1", credentials=creds)
    return drive_service, docs_service


# ── Folder Creation ───────────────────────────────────────────────────────────

def create_application_folder(drive_service, company, role):
    """Create a YYMMDD [Company] [Role] subfolder in Job Applications."""
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


# ── Google Doc Creation ───────────────────────────────────────────────────────

def create_google_doc(drive_service, docs_service, folder_id, title, content):
    """Create a Google Doc inside the specified folder."""
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


# ── Resume Tailoring ──────────────────────────────────────────────────────────

def tailor_resume(listing):
    """Use Claude to tailor the base resume for a specific listing."""
    prompt = f"""You are tailoring a candidate's resume for a specific job listing.

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


# ── Cover Letter Tailoring ────────────────────────────────────────────────────

def tailor_cover_letter(listing):
    """Use Claude to tailor the base cover letter for a specific listing."""
    prompt = f"""You are tailoring a candidate's cover letter for a specific job listing.

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


# ── Notes Doc ─────────────────────────────────────────────────────────────────

def create_notes(listing, resume_changes, cover_letter_changes, folder_url):
    """Use Claude to create a concise notes doc summarising changes and flags."""
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
3. Resume Changes — bullet points of key changes made and anything needing candidate verification
4. Cover Letter Changes — bullet points of key changes and assumptions to verify
5. Gaps to Address — any JD requirements not covered by the resume that the candidate may want to address
6. Red Flags — if any

Keep it scannable. The candidate should be able to review this in under 2 minutes."""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Starting preparer agent...")

    # Load scout results
    today = datetime.now().strftime("%y%m%d")
    scout_results_path = f"output/scout_results_{today}.json"

    if not os.path.exists(scout_results_path):
        print(f"No scout results found at {scout_results_path}. Run scout.py first.")
        return

    with open(scout_results_path, "r") as f:
        listings = json.load(f)

    threshold = profile["scoring"]["preparer_threshold"]
    qualifying = [l for l in listings if l.get("role_fit", 0) >= threshold]
    print(f"  {len(qualifying)} listings meet the threshold of {threshold}/10")

    if not qualifying:
        print("No listings above threshold. Preparer agent done.")
        return

    drive_service, docs_service = get_drive_service()
    prepared = []

    for listing in qualifying:
        company = listing.get("company") or "Unknown Company"
        role = listing.get("job_title") or "Unknown Role"
        print(f"\nPreparing: {role} at {company}")

        # Create Drive folder
        folder_id, folder_name = create_application_folder(drive_service, company, role)
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"

        # Tailor resume
        print("  Tailoring resume...")
        tailored_resume_full = tailor_resume(listing)
        resume_parts = tailored_resume_full.split("## Changes Made")
        tailored_resume = resume_parts[0].strip()
        resume_changes = resume_parts[1].strip() if len(resume_parts) > 1 else ""

        # Tailor cover letter
        print("  Tailoring cover letter...")
        tailored_cover_full = tailor_cover_letter(listing)
        cover_parts = tailored_cover_full.split("## Changes Made")
        tailored_cover = cover_parts[0].strip()
        cover_changes = cover_parts[1].strip() if len(cover_parts) > 1 else ""

        # Create notes doc
        print("  Creating notes...")
        notes_content = create_notes(listing, resume_changes, cover_changes, folder_url)

        # Write to Google Drive
        create_google_doc(drive_service, docs_service, folder_id, f"{folder_name} — Resume", tailored_resume)
        create_google_doc(drive_service, docs_service, folder_id, f"{folder_name} — Cover Letter", tailored_cover)
        create_google_doc(drive_service, docs_service, folder_id, f"{folder_name} — Notes", notes_content)

        prepared.append({
            "job_title": role,
            "company": company,
            "role_fit": listing.get("role_fit"),
            "skills_match": listing.get("skills_match"),
            "apply_url": listing.get("apply_url"),
            "folder_url": folder_url,
            "notes_summary": notes_content
        })

    # Save preparer output
    output_path = f"output/preparer_results_{today}.json"
    with open(output_path, "w") as f:
        json.dump(prepared, f, indent=2)

    print(f"\nPreparer complete. {len(prepared)} applications prepared and saved to {output_path}")
    return prepared


if __name__ == "__main__":
    main()
