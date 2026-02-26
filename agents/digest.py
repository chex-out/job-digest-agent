import os
import json
import yaml
import resend
from datetime import datetime

# ── Load Config ───────────────────────────────────────────────────────────────

with open("config/user_profile.yaml", "r") as f:
    profile = yaml.safe_load(f)

# ── Resend Client ─────────────────────────────────────────────────────────────

resend.api_key = os.environ["RESEND_API_KEY"]

# ── HTML Builders ─────────────────────────────────────────────────────────────

def score_colour(score):
    """Return a colour hex based on score value."""
    if score >= 8:
        return "#2e7d32"   # green
    elif score >= 6:
        return "#f57c00"   # amber
    else:
        return "#c62828"   # red


def build_listing_card(listing, index):
    """Build an HTML card for a single job listing."""
    role_fit = listing.get("role_fit", 0)
    skills_match = listing.get("skills_match", 0)
    prepared = listing.get("folder_url") is not None

    folder_section = ""
    if prepared:
        folder_section = f"""
        <div style="margin-top:12px; padding:10px; background:#f0f4ff; border-radius:4px;">
            <strong>📁 Materials Ready for Review</strong><br>
            <a href="{listing.get('folder_url')}" style="color:#1a56db;">Open Google Drive Folder</a>
            <div style="margin-top:8px; font-size:13px; color:#444;">
                {listing.get('notes_summary', '')[:400]}...
                <br><a href="{listing.get('folder_url')}" style="color:#1a56db;">Read full notes →</a>
            </div>
        </div>"""

    return f"""
    <div style="margin-bottom:24px; padding:16px; border:1px solid #e0e0e0; border-radius:6px; font-family:Arial,sans-serif;">
        <div style="font-size:11px; color:#888; margin-bottom:4px;">#{index}</div>
        <div style="font-size:17px; font-weight:bold; color:#1a1a1a;">{listing.get('job_title', 'Unknown Role')}</div>
        <div style="font-size:14px; color:#444; margin-bottom:10px;">{listing.get('company', 'Unknown Company')}</div>

        <div style="display:inline-block; margin-right:16px;">
            <span style="font-size:12px; color:#666;">Role Fit</span><br>
            <span style="font-size:20px; font-weight:bold; color:{score_colour(role_fit)};">{role_fit}/10</span>
        </div>
        <div style="display:inline-block;">
            <span style="font-size:12px; color:#666;">Skills Match</span><br>
            <span style="font-size:20px; font-weight:bold; color:{score_colour(skills_match)};">{skills_match}/10</span>
        </div>

        <div style="margin-top:10px; font-size:13px; color:#555;">
            <strong>Role:</strong> {listing.get('role_fit_rationale', '')}<br>
            <strong>Skills:</strong> {listing.get('skills_match_rationale', '')}
        </div>

        {f'<div style="margin-top:8px; font-size:13px; color:#555;"><strong>Company:</strong> {listing.get("company_snapshot", "")}</div>' if listing.get('company_snapshot') else ''}
        {f'<div style="margin-top:6px; font-size:13px; color:#c62828;"><strong>⚠️ Red Flags:</strong> {listing.get("red_flags")}</div>' if listing.get('red_flags') else ''}
        {f'<div style="margin-top:6px; font-size:13px; color:#2e7d32;"><strong>✅ Standout:</strong> {listing.get("standout_positives")}</div>' if listing.get('standout_positives') else ''}

        {folder_section}

        <div style="margin-top:12px;">
            <a href="{listing.get('apply_url', '#')}" style="display:inline-block; padding:8px 16px; background:#1a56db; color:white; text-decoration:none; border-radius:4px; font-size:13px;">View Listing →</a>
        </div>
    </div>"""


def build_email_html(all_listings, prepared_listings):
    """Build the full digest email HTML."""
    today = datetime.now().strftime("%d %B %Y")
    total = len(all_listings)
    prepared_count = len(prepared_listings)
    threshold = profile["scoring"]["preparer_threshold"]

    # Merge folder_url and notes_summary into all_listings for prepared ones
    prepared_map = {l["apply_url"]: l for l in prepared_listings}
    for listing in all_listings:
        if listing.get("apply_url") in prepared_map:
            listing["folder_url"] = prepared_map[listing["apply_url"]]["folder_url"]
            listing["notes_summary"] = prepared_map[listing["apply_url"]]["notes_summary"]

    # Split into prepared and others
    above_threshold = [l for l in all_listings if l.get("role_fit", 0) >= threshold]
    below_threshold = [l for l in all_listings if l.get("role_fit", 0) < threshold]

    above_cards = "".join([build_listing_card(l, i+1) for i, l in enumerate(above_threshold)])
    below_cards = "".join([build_listing_card(l, i+1) for i, l in enumerate(below_threshold)])

    below_section = ""
    if below_threshold:
        below_section = f"""
        <h2 style="font-size:16px; color:#888; margin-top:32px;">Other Listings This Week</h2>
        <p style="font-size:13px; color:#aaa;">Below your {threshold}/10 threshold — no materials prepared.</p>
        {below_cards}"""

    feedback_prompt = """
    <div style="margin-top:32px; padding:16px; background:#f9f9f9; border:1px solid #e0e0e0; border-radius:6px; font-family:Arial,sans-serif;">
        <strong>💬 Send Feedback</strong>
        <p style="font-size:13px; color:#555; margin-top:6px;">
            Reply to this email with your reactions to any listing — e.g. "Listing 2 — too junior" or "Listing 4 — perfect scope, use as benchmark."
            Your feedback will be used to improve your preference profile over time.
        </p>
    </div>"""

    return f"""
    <div style="max-width:640px; margin:0 auto; font-family:Arial,sans-serif; color:#1a1a1a;">
        <div style="padding:24px 0; border-bottom:2px solid #1a56db; margin-bottom:24px;">
            <div style="font-size:22px; font-weight:bold;">Good morning, Abraham ☀️</div>
            <div style="font-size:14px; color:#666; margin-top:4px;">Your Weekly Job Market Digest — {today}</div>
            <div style="font-size:13px; color:#888; margin-top:8px;">
                {total} listings found &nbsp;·&nbsp;
                <span style="color:#2e7d32;">{prepared_count} above threshold — materials prepared</span>
            </div>
        </div>

        <h2 style="font-size:16px; color:#1a1a1a;">Top Matches This Week</h2>
        {above_cards if above_threshold else '<p style="color:#888; font-size:14px;">No listings met your threshold this week.</p>'}

        {below_section}
        {feedback_prompt}

        <div style="margin-top:32px; padding-top:16px; border-top:1px solid #e0e0e0; font-size:11px; color:#aaa;">
            Weekly Job Market Digest · Powered by Claude
        </div>
    </div>"""


# ── Send Email ────────────────────────────────────────────────────────────────

def send_digest(html):
    """Send the digest email via Resend."""
    resend.Emails.send({
        "from": "Job Digest <onboarding@resend.dev>",
        "to": "lee.abraham.e@gmail.com",
        "subject": f"Your Weekly Job Digest — {datetime.now().strftime('%d %b %Y')}",
        "html": html
    })
    print("  [Email] Digest sent successfully")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Starting digest agent...")
    today = datetime.now().strftime("%y%m%d")

    # Load scout results
    scout_path = f"output/scout_results_{today}.json"
    if not os.path.exists(scout_path):
        print(f"No scout results found at {scout_path}.")
        return

    with open(scout_path, "r") as f:
        all_listings = json.load(f)

    # Load preparer results if available
    preparer_path = f"output/preparer_results_{today}.json"
    prepared_listings = []
    if os.path.exists(preparer_path):
        with open(preparer_path, "r") as f:
            prepared_listings = json.load(f)

    print(f"  {len(all_listings)} total listings, {len(prepared_listings)} prepared")

    html = build_email_html(all_listings, prepared_listings)

    # Save HTML for inspection
    os.makedirs("output", exist_ok=True)
    html_path = f"output/digest_{today}.html"
    with open(html_path, "w") as f:
        f.write(html)
    print(f"  [HTML] Saved to {html_path}")

    send_digest(html)
    print("Digest agent complete.")


if __name__ == "__main__":
    main()
