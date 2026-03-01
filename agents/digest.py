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


def build_listing_card(listing, index, prepared=False):
    """Build a compact HTML card for a single job listing."""
    role_fit = listing.get("role_fit", 0)
    skills_match = listing.get("skills_match", 0)

    folder_line = ""
    if prepared and listing.get("folder_url"):
        folder_line = f'<li>📁 <a href="{listing.get("folder_url")}" style="color:#1a56db;">Materials ready in Drive</a></li>'

    partial_line = '<li>⚠️ Limited listing data — review the original posting directly before applying</li>' if listing.get("partial_data") else ""
    red_flag_line = f'<li>⚠️ {listing.get("red_flags")}</li>' if listing.get("red_flags") else ""
    positive_line = f'<li>✅ {listing.get("standout_positives")}</li>' if listing.get("standout_positives") else ""
    company_line = f'<li>🏢 {listing.get("company_snapshot")}</li>' if listing.get("company_snapshot") else ""

    return f"""
    <div style="margin-bottom:16px; padding:12px 16px; border:1px solid #e0e0e0; border-radius:6px; font-family:Arial,sans-serif;">
        <div style="display:flex; justify-content:space-between; align-items:baseline;">
            <div>
                <span style="font-size:13px; color:#888;">#{index} &nbsp;</span>
                <span style="font-size:15px; font-weight:bold; color:#1a1a1a;">{listing.get('job_title', 'Unknown Role')}</span>
                <span style="font-size:13px; color:#666;"> — {listing.get('company', 'Unknown Company')}</span>
            </div>
            <div style="font-size:13px; font-weight:bold; color:{score_colour(role_fit)}; white-space:nowrap;">
                {role_fit}/10 &nbsp;·&nbsp; <span style="color:{score_colour(skills_match)};">{skills_match}/10</span>
            </div>
        </div>
        <ul style="margin:8px 0 0 0; padding-left:18px; font-size:13px; color:#444; line-height:1.6;">
            <li>🎯 Role: {listing.get('role_fit_rationale', '')}</li>
            <li>🛠️ Skills: {listing.get('skills_match_rationale', '')}</li>
            {company_line}
            {positive_line}
            {red_flag_line}
            {folder_line}
            {partial_line}
        </ul>
        <div style="margin-top:8px;">
            <a href="{listing.get('apply_url', '#')}" style="font-size:12px; color:#1a56db;">View listing →</a>
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

    above_cards = "".join([build_listing_card(l, i+1, prepared=True) for i, l in enumerate(above_threshold)])
    below_cards = "".join([build_listing_card(l, i+1, prepared=False) for i, l in enumerate(below_threshold)])

    below_section = ""
    if below_threshold:
        below_section = f"""
        <h2 style="font-size:16px; color:#888; margin-top:32px;">Below Threshold</h2>
        <p style="font-size:13px; color:#aaa;">Scored below {threshold}/10 — included for visibility only.</p>
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
