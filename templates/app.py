"""
Meeting Notes Formatter — Phone-Notes Paste Page
---------------------------------------------------
For meetings that happen with no automatic transcript (in-person,
notes taken on a phone). Ronald pastes his rough notes here after
the meeting; this reformats them into Key Points / Decisions /
Next Actions and writes them straight into the matching Meeting
Log row in Airtable.
"""

import os
import logging
import json
from datetime import date
from flask import Flask, render_template, request, jsonify
from pyairtable import Api
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("notes_formatter")

app = Flask(__name__)

AIRTABLE_TOKEN   = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE_ID = os.environ["AIRTABLE_BASE_ID"]
MEETING_LOG_TABLE = os.environ.get("MEETING_LOG_TABLE", "Meeting Log")
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

table = Api(AIRTABLE_TOKEN).table(AIRTABLE_BASE_ID, MEETING_LOG_TABLE)
claude = Anthropic(api_key=ANTHROPIC_API_KEY)

FORMAT_PROMPT = """You are helping format rough, informal meeting notes into a clean
structured recap for an embassy staff record. Given the raw notes below, extract:

1. Key Points — the main topics discussed
2. Decisions / Resolutions — anything explicitly agreed or decided
3. Next Actionable Items — concrete follow-up actions, with who's responsible if mentioned

If the raw notes don't clearly contain one of these categories, leave that list empty —
do not invent content that wasn't in the notes.

RAW NOTES:
{raw_notes}

Respond with ONLY valid JSON, no other text:
{{
  "key_points": ["...", "..."],
  "decisions": ["...", "..."],
  "next_actions": ["...", "..."]
}}"""


@app.route("/")
def index():
    today_str = date.today().isoformat()
    formula = f"IS_SAME({{Date}}, '{today_str}', 'day')"
    try:
        records = table.all(formula=formula)
    except Exception as e:
        log.error(f"Airtable fetch error: {e}")
        records = []

    meetings = []
    for r in records:
        f = r.get("fields", {})
        org = f.get("Organization", [])
        org_label = ", ".join(org) if isinstance(org, list) else (org or "")
        unmatched = f.get("Unmatched Guests", "")
        label_parts = [p for p in [org_label, unmatched] if p]
        label = f.get("Meeting ID", r["id"])
        if label_parts:
            label += " — " + "; ".join(label_parts)
        meetings.append({"id": r["id"], "label": label})

    return render_template("index.html", meetings=meetings, today=today_str)


@app.route("/submit", methods=["POST"])
def submit():
    record_id = request.form.get("meeting_id")
    raw_notes = request.form.get("raw_notes", "").strip()

    if not record_id or not raw_notes:
        return render_template("result.html", success=False,
                                message="Please select a meeting and enter some notes.")

    try:
        message = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": FORMAT_PROMPT.format(raw_notes=raw_notes)}]
        )
        text = message.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.split("```")[0].strip()
        parsed = json.loads(text)
    except Exception as e:
        log.error(f"Claude formatting error: {e}")
        return render_template("result.html", success=False,
                                message="Something went wrong formatting the notes. Nothing was saved — try again, or send this to Tresor.")

    key_points = parsed.get("key_points", [])
    decisions = parsed.get("decisions", [])
    next_actions = parsed.get("next_actions", [])

    notes_text = ""
    if key_points:
        notes_text += "Key Points:\n" + "\n".join(f"- {p}" for p in key_points)
    if decisions:
        if notes_text:
            notes_text += "\n\n"
        notes_text += "Decisions / Resolutions:\n" + "\n".join(f"- {d}" for d in decisions)

    next_actions_text = "\n".join(f"- {a}" for a in next_actions)

    try:
        table.update(record_id, {
            "Meeting Notes": notes_text,
            "Follow-up / Next Action": next_actions_text
        })
    except Exception as e:
        log.error(f"Airtable write error: {e}")
        return render_template("result.html", success=False,
                                message="Notes were formatted but couldn't be saved to Airtable. Send this to Tresor.")

    return render_template("result.html", success=True, key_points=key_points,
                            decisions=decisions, next_actions=next_actions)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
