import os
import re
import yaml
import imaplib
import email
from email.header import decode_header
from datetime import datetime

# ── Load Config ───────────────────────────────────────────────────────────────

with open("config/user_profile.yaml", "r") as f:
    profile = yaml.safe_load(f)

# ── Constants ─────────────────────────────────────────────────────────────────

TRIGGER_SUBJECT = "Job Listings"
INPUT_FILE = "config/input_listings.yaml"
PROCESSED_FILE = "config/processed_listings.yaml"

# ── Fetch Emails ──────────────────────────────────────────────────────────────

def fetch_listing_emails():
    """Fetch emails with the trigger subject from Gmail."""
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(gmail_address, gmail_password)
    mail.select("inbox")

    status, messages = mail.search(None, f'(SUBJECT "{TRIGGER_SUBJECT}" UNSEEN)')
    if status != "OK" or not messages[0]:
        print("  [Ingestor] No new listing emails found")
        mail.logout()
        return []

    urls = []
    for msg_id in messages[0].split():
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Extract body
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = msg.get_payload(decode=True).decode(errors="ignore")

        # Extract URLs from body
        found_urls = re.findall(r'https?://[^\s<>"\']+', body)
        for url in found_urls:
            url = url.strip().rstrip(")")
            if url:
                urls.append(url)
                print(f"  [Ingestor] Found URL: {url[:80]}")

        # Mark email as read
        mail.store(msg_id, "+FLAGS", "\\Seen")

    mail.logout()
    print(f"  [Ingestor] {len(urls)} URLs extracted from emails")
    return urls


# ── Load Existing Files ───────────────────────────────────────────────────────

def load_yaml_file(path):
    """Load a YAML file, returning empty dict if file is empty or missing."""
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


# ── Update Input Listings ─────────────────────────────────────────────────────

def update_input_listings(new_urls):
    """Append new URLs to input_listings.yaml, avoiding duplicates."""
    input_data = load_yaml_file(INPUT_FILE)
    processed_data = load_yaml_file(PROCESSED_FILE)

    existing_urls = {l["url"] for l in input_data.get("listings", [])}
    processed_urls = {l["url"] for l in processed_data.get("listings", [])}

    added = 0
    for url in new_urls:
        if url not in existing_urls and url not in processed_urls:
            input_data.setdefault("listings", []).append({
                "url": url,
                "added": datetime.now().strftime("%Y-%m-%d")
            })
            existing_urls.add(url)
            added += 1
        else:
            print(f"  [Ingestor] Skipping duplicate: {url[:80]}")

    with open(INPUT_FILE, "w") as f:
        yaml.dump(input_data, f, allow_unicode=True, default_flow_style=False)

    print(f"  [Ingestor] {added} new URLs added to {INPUT_FILE}")
    return added


# ── Archive Processed Listings ────────────────────────────────────────────────

def archive_processed_listings():
    """Move processed listings from input to processed archive after scout runs."""
    input_data = load_yaml_file(INPUT_FILE)
    processed_data = load_yaml_file(PROCESSED_FILE)

    listings = input_data.get("listings", [])
    if not listings:
        print("  [Ingestor] No listings to archive")
        return

    processed_data.setdefault("listings", [])
    for listing in listings:
        listing["processed"] = datetime.now().strftime("%Y-%m-%d")
        processed_data["listings"].append(listing)

    # Clear input file
    with open(INPUT_FILE, "w") as f:
        yaml.dump({"listings": []}, f)

    # Save archive
    with open(PROCESSED_FILE, "w") as f:
        yaml.dump(processed_data, f, allow_unicode=True, default_flow_style=False)

    print(f"  [Ingestor] {len(listings)} listings archived to {PROCESSED_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Starting ingestor agent...")
    new_urls = fetch_listing_emails()
    if new_urls:
        update_input_listings(new_urls)
    else:
        print("  No new URLs to add.")
    print("Ingestor complete.")


if __name__ == "__main__":
    main()
