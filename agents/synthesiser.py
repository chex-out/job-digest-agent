import os
import re
import yaml
import anthropic
import imaplib
import email
from datetime import datetime
from email.header import decode_header
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Load Config ───────────────────────────────────────────────────────────────

with open("config/user_profile.yaml", "r") as f:
    profile = yaml.safe_load(f)

# ── API Clients ───────────────────────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── Gmail ─────────────────────────────────────────────────────────────────────

def fetch_digest_replies():
    """Fetch replies to the weekly job digest email."""
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(gmail_address, gmail_password)
    mail.select("inbox")

    status, messages = mail.search(None, '(SUBJECT "Weekly Job Digest" UNSEEN)')
    if status != "OK" or not messages[0]:
        print("  [Synthesiser] No new digest replies found")
        mail.logout()
        return []

    replies = []
    for msg_id in messages[0].split():
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject = decode_header(msg["Subject"])[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode()
        if not subject.startswith("Re:"):
            continue

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = msg.get_payload(decode=True).decode(errors="ignore")

        # Strip quoted original email
        body = re.split(r"On .* wrote:", body)[0].strip()

        if body:
            replies.append({"date": msg["Date"], "body": body})
            print(f"  [Synthesiser] Found reply: {body[:80]}...")

        mail.store(msg_id, "+FLAGS", "\\Seen")

    mail.logout()
    print(f"  [Synthesiser] {len(replies)} replies fetched")
    return replies


# ── Parse Feedback ────────────────────────────────────────────────────────────

def parse_feedback(replies):
    """Parse replies into revision and learning feedback."""
    revisions = []
    learnings = []
    preference_feedback = []

    for reply in replies:
        body = reply["body"]
        date = reply["date"]

        # Extract Revise this: blocks
        revise_matches = re.findall(
            r"Revise this:(.*?)(?=Learn this:|Revise this:|$)",
            body, re.DOTALL | re.IGNORECASE
        )
        for match in revise_matches:
            match = match.strip()
            if match:
                lines = match.strip().splitlines()
                listing_ref = lines[0].strip() if lines else ""
                instructions = "\n".join(lines[1:]).strip()
                if listing_ref and instructions:
                    revisions.append({
                        "date": date,
                        "listing_ref": listing_ref,
                        "instructions": instructions
                    })
                    print(f"  [Synthesiser] Revision request for: {listing_ref}")

        # Extract Learn this: blocks
        learn_matches = re.findall(
            r"Learn this:(.*?)(?=Revise this:|Learn this:|$)",
            body, re.DOTALL | re.IGNORECASE
        )
        for match in learn_matches:
            match = match.strip()
            if match:
                learnings.append({
                    "date": date,
                    "learning": match
                })
                print(f"  [Synthesiser] Learning captured: {match[:80]}")

        # Everything else goes to preference feedback
        remaining = body
        remaining = re.sub(r"Revise this:.*?(?=Learn this:|Revise this:|$)", "", remaining, flags=re.DOTALL | re.IGNORECASE)
        remaining = re.sub(r"Learn this:.*?(?=Revise this:|Learn this:|$)", "", remaining, flags=re.DOTALL | re.IGNORECASE)
        remaining = remaining.strip()
        if remaining:
            preference_feedback.append({
                "date": date,
                "feedback": remaining
            })

    return revisions, learnings, preference_feedback


# ── Application Feedback Log ──────────────────────────────────────────────────

def update_application_feedback_log(learnings):
    """Append new learnings to application_feedback_log.yaml."""
    with open("config/application_feedback_log.yaml", "r") as f:
        log = yaml.safe_load(f) or {"feedback": []}

    for learning in learnings:
        if learning not in log["feedback"]:
            log["feedback"].append(learning)

    with open("config/application_feedback_log.yaml", "w") as f:
        yaml.dump(log, f, allow_unicode=True, default_flow_style=False)

    print(f"  [Synthesiser] {len(learnings)} learnings saved to application_feedback_log.yaml")


# ── Preference Feedback Log ───────────────────────────────────────────────────

def update_preference_feedback_log(preference_feedback):
    """Append preference feedback to feedback_log.yaml."""
    with open("config/feedback_log.yaml", "r") as f:
        log = yaml.safe_load(f) or {}

    entries = log.get("entries", [])
    for item in preference_feedback:
        if item not in entries:
            entries.append(item)
    log["entries"] = entries

    with open("config/feedback_log.yaml", "w") as f:
        yaml.dump(log, f, allow_unicode=True, default_flow_style=False)

    print(f"  [Synthesiser] {len(preference_feedback)} preference feedback entries saved")


# ── Revision ──────────────────────────────────────────────────────────────────

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
    if creds.expired:
        creds.refresh(Request())
    drive_service = build("drive", "v3", credentials=creds)
    docs_service = build("docs", "v1", credentials=creds)
    return drive_service, docs_service


def find_folder(drive_service, listing_ref):
    """Find a Drive folder matching the listing reference."""
    parent_folder_id = profile["google_drive"]["job_applications_folder_id"]
    results = drive_service.files().list(
        q=f"'{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and name contains '{listing_ref}'",
        fields="files(id, name)"
    ).execute()
    files = results.get("files", [])
    if files:
        print(f"  [Revision] Found folder: {files[0]['name']}")
        return files[0]["id"], files[0]["name"]
    print(f"  [Revision] No folder found matching: {listing_ref}")
    return None, None


def find_doc_in_folder(drive_service, folder_id, doc_type):
    """Find a specific doc (Resume, Cover Letter, Notes) within a folder."""
    results = drive_service.files().list(
        q=f"'{folder_id}' in parents and name contains '{doc_type}'",
        fields="files(id, name)"
    ).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    return None


def get_doc_content(docs_service, doc_id):
    """Extract plain text content from a Google Doc."""
    doc = docs_service.documents().get(documentId=doc_id).execute()
    content = ""
    for element in doc.get("body", {}).get("content", []):
        for para_element in element.get("paragraph", {}).get("elements", []):
            content += para_element.get("textRun", {}).get("content", "")
    return content


def overwrite_doc(docs_service, doc_id, new_content):
    """Clear a Google Doc and write new content."""
    doc = docs_service.documents().get(documentId=doc_id).execute()
    doc_length = doc["body"]["content"][-1]["endIndex"] - 1

    requests = []
    if doc_length > 1:
        requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": doc_length}
            }
        })
    requests.append({
        "insertText": {
            "location": {"index": 1},
            "text": new_content
        }
    })

    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests}
    ).execute()
    print(f"  [Revision] Doc {doc_id} updated")


def apply_revision(drive_service, docs_service, revision):
    """Apply revision instructions to the relevant Google Doc."""
    listing_ref = revision["listing_ref"]
    instructions = revision["instructions"]

    # Determine which document type is being revised
    doc_type = "Cover Letter" if "cover letter" in listing_ref.lower() else "Resume"
    listing_name = re.sub(r"(?i)\s*[-—]\s*(cover letter|resume).*$", "", listing_ref).strip()

    folder_id, folder_name = find_folder(drive_service, listing_name)
    if not folder_id:
        print(f"  [Revision] Skipping — folder not found for: {listing_name}")
        return

    doc_id = find_doc_in_folder(drive_service, folder_id, doc_type)
    if not doc_id:
        print(f"  [Revision] Skipping — {doc_type} doc not found in folder")
        return

    # Get current content
    current_content = get_doc_content(docs_service, doc_id)

    # Use Claude to apply the revision instructions
    with open("config/application_feedback_log.yaml", "r") as f:
        feedback_log = f.read()

    prompt = f"""You are revising a candidate's {doc_type} based on specific feedback instructions.

## Current {doc_type}
{current_content}

## Revision Instructions
{instructions}

## General Learnings (apply where relevant)
{feedback_log}

## Instructions
Apply the revision instructions precisely. Do not make changes beyond what is instructed.
Return the full revised {doc_type} only, no preamble or explanation."""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    revised_content = response.content[0].text.strip()
    overwrite_doc(docs_service, doc_id, revised_content)
    print(f"  [Revision] {doc_type} revised for: {listing_name}")


# ── Preference Synthesis ──────────────────────────────────────────────────────

def synthesise_preferences():
    """Use Claude to synthesise preference feedback into an updated profile."""
    with open("config/feedback_log.yaml", "r") as f:
        feedback_log = f.read()

    with open("config/user_profile.yaml", "r") as f:
        current_profile = f.read()

    if not yaml.safe_load(feedback_log) or not yaml.safe_load(feedback_log).get("entries"):
        print("  [Synthesiser] No preference feedback to synthesise")
        return None, None

    prompt = f"""You are updating a candidate's job preference profile based on feedback they have provided on past job listings.

## Current Preference Profile
{current_profile}

## Feedback Log
{feedback_log}

## Instructions
Analyse the feedback log and identify:
1. Refinements — updates to existing preferences based on patterns in the feedback
2. New signals — preferences not currently captured in the profile

Then produce two things:

### 1. Updated user_profile.yaml
Return the full updated YAML file with changes incorporated. Only update the following sections based on feedback:
- desired_role
- company_preferences
- search.search_terms

Do not modify any other sections.

### 2. Changelog
A concise bullet-point list of every change made and the feedback that prompted it. Format as:
- [CHANGED/ADDED/REMOVED] <what changed> — based on: "<feedback quote>"

Separate the two outputs with the delimiter: ---CHANGELOG---"""

    response = anthropic_client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    full_output = response.content[0].text
    parts = full_output.split("---CHANGELOG---")
    updated_yaml = parts[0].strip()
    changelog = parts[1].strip() if len(parts) > 1 else "No changes recorded."

    updated_yaml = re.sub(r"^```yaml\n?", "", updated_yaml)
    updated_yaml = re.sub(r"\n?```$", "", updated_yaml).strip()

    return updated_yaml, changelog


def save_updated_profile(updated_yaml, changelog):
    """Save proposed profile and changelog for review."""
    with open("config/user_profile_proposed.yaml", "w") as f:
        f.write(updated_yaml)

    today = datetime.now().strftime("%y%m%d")
    os.makedirs("output", exist_ok=True)
    changelog_path = f"output/synthesis_changelog_{today}.txt"
    with open(changelog_path, "w") as f:
        f.write(f"Synthesis Changelog — {today}\n\n")
        f.write(changelog)

    print(f"  [Synthesiser] Proposed profile saved to config/user_profile_proposed.yaml")
    print(f"  [Synthesiser] Changelog saved to {changelog_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import json
    global json
    print("Starting synthesiser agent...")

    replies = fetch_digest_replies()
    if not replies:
        print("  No replies to process.")
        return

    revisions, learnings, preference_feedback = parse_feedback(replies)

    # Save learnings
    if learnings:
        update_application_feedback_log(learnings)

    # Save preference feedback
    if preference_feedback:
        update_preference_feedback_log(preference_feedback)

    # Apply revisions
    if revisions:
        drive_service, docs_service = get_drive_service()
        for revision in revisions:
            apply_revision(drive_service, docs_service, revision)

    # Synthesise preference profile
    updated_yaml, changelog = synthesise_preferences()
    if updated_yaml:
        save_updated_profile(updated_yaml, changelog)

    print("\nSynthesiser complete.")


if __name__ == "__main__":
    main()
