import os
import re
import yaml
import anthropic
import imaplib
import email
from datetime import datetime
from email.header import decode_header

# ── Load Config ───────────────────────────────────────────────────────────────

with open("config/user_profile.yaml", "r") as f:
    profile = yaml.safe_load(f)

# ── API Clients ───────────────────────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── Fetch Feedback Replies ────────────────────────────────────────────────────

def fetch_feedback_replies():
    """Fetch replies to the job digest email via IMAP."""
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]

    replies = []

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(gmail_address, gmail_password)
    mail.select("inbox")

    # Search for replies to digest emails
    status, messages = mail.search(None, '(SUBJECT "Weekly Job Digest")')
    if status != "OK" or not messages[0]:
        print("  [IMAP] No digest replies found")
        mail.logout()
        return replies

    for msg_id in messages[0].split():
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Only process replies (emails with Re: in subject)
        subject = decode_header(msg["Subject"])[0][0]
        if isinstance(subject, bytes):
            subject = subject.decode()
        if not subject.startswith("Re:"):
            continue

        # Extract plain text body
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode()
                    break
        else:
            body = msg.get_payload(decode=True).decode()

        # Strip quoted original email
        body = re.split(r"On .* wrote:", body)[0].strip()

        if body:
            date = msg["Date"]
            replies.append({"date": date, "feedback": body})
            print(f"  [IMAP] Found reply: {body[:80]}...")

    mail.logout()
    print(f"  [IMAP] {len(replies)} feedback replies fetched")
    return replies


# ── Append to Feedback Log ────────────────────────────────────────────────────

def append_to_feedback_log(replies):
    """Append new feedback replies to feedback_log.yaml."""
    with open("config/feedback_log.yaml", "r") as f:
        log = yaml.safe_load(f) or {}

    entries = log.get("entries", [])

    for reply in replies:
        entry = {
            "date": reply["date"],
            "feedback": reply["feedback"]
        }
        if entry not in entries:
            entries.append(entry)

    log["entries"] = entries

    with open("config/feedback_log.yaml", "w") as f:
        yaml.dump(log, f, allow_unicode=True, default_flow_style=False)

    print(f"  [Log] {len(replies)} new entries appended to feedback_log.yaml")


# ── Synthesise Preferences ────────────────────────────────────────────────────

def synthesise_preferences():
    """Use Claude to synthesise feedback log into an updated preference profile."""
    with open("config/feedback_log.yaml", "r") as f:
        feedback_log = f.read()

    with open("config/user_profile.yaml", "r") as f:
        current_profile = f.read()

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

Do not modify any other sections (google_drive, scoring, telegram, feedback, review_sources).

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

    # Strip markdown code fences if present
    updated_yaml = re.sub(r"^```yaml\n?", "", updated_yaml)
    updated_yaml = re.sub(r"\n?```$", "", updated_yaml).strip()

    return updated_yaml, changelog


# ── Save Updated Profile ──────────────────────────────────────────────────────

def save_updated_profile(updated_yaml, changelog):
    """Save the updated profile and changelog for review."""
    # Save proposed profile as a separate file for review before committing
    with open("config/user_profile_proposed.yaml", "w") as f:
        f.write(updated_yaml)

    # Save changelog
    today = datetime.now().strftime("%y%m%d")
    os.makedirs("output", exist_ok=True)
    changelog_path = f"output/synthesis_changelog_{today}.txt"
    with open(changelog_path, "w") as f:
        f.write(f"Synthesis Changelog — {today}\n\n")
        f.write(changelog)

    print(f"  [Synthesis] Proposed profile saved to config/user_profile_proposed.yaml")
    print(f"  [Synthesis] Changelog saved to {changelog_path}")
    return changelog


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Starting synthesiser agent...")

    # Fetch and log feedback replies
    replies = fetch_feedback_replies()
    if replies:
        append_to_feedback_log(replies)
    else:
        print("  No new feedback to process.")

    # Check if there are any entries in the feedback log at all
    with open("config/feedback_log.yaml", "r") as f:
        log = yaml.safe_load(f) or {}

    if not log.get("entries"):
        print("  Feedback log is empty — skipping synthesis.")
        return

    # Synthesise preferences
    print("  Synthesising preferences...")
    updated_yaml, changelog = synthesise_preferences()
    changelog_summary = save_updated_profile(updated_yaml, changelog)

    print("\nSynthesiser complete.")
    print("\nChangelog preview:")
    print(changelog_summary[:500])
    print("\nReview config/user_profile_proposed.yaml before replacing user_profile.yaml.")


if __name__ == "__main__":
    main()
```
