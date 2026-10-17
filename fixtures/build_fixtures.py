#!/usr/bin/env python3
"""Build the voice-memo and long-meeting fixtures from scripted segments.

Ground truth is exactly the spoken text, so it is correct by construction.
Each task gets: <task>/<audio file>, <task>/truth.txt, <task>/key_phrases.txt.
Requires macOS `say` and ffmpeg.  python3 build_fixtures.py
"""

import os
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

VOICE_MEMO = [
    ("Daniel", "Quick standup notes before I forget. Marguerite finished the Stripe webhook retry logic yesterday, and it is now live in the Toronto region."),
    ("Samantha", "Right, and the only blocker is the Okta SAML certificate, which expires on December fourteenth. Rahul said he would rotate it by Wednesday."),
    ("Daniel", "Good. Also, the churn dashboard shows two hundred and thirty-one cancellations last month, mostly from the Starter plan."),
    ("Samantha", "I will pull the cohort analysis in Looker and share it in the growth channel. Can we push the Kafka upgrade to next sprint?"),
    ("Daniel", "Yes, let's move Kafka to sprint forty-two. That's everything. Talk tomorrow."),
]
VOICE_MEMO_PHRASES = [
    "Marguerite", "Stripe webhook", "Toronto", "Okta SAML",
    "December fourteenth|December 14|December 14th",
    "Rahul", "two hundred and thirty-one|231", "Starter plan", "cohort analysis",
    "Looker", "Kafka", "sprint forty-two|sprint 42",
]

S = "silence"
LONG_MEETING = [
    (S, 60),  # recording started before anyone spoke, as real meeting recordings do
    ("Daniel", "Okay, let's get started. This is the weekly operations review for the Halvorsen Logistics account. I have Karen from finance, Moira from customer success, and Rishi from the platform team. The agenda today is the warehouse migration, the invoicing dispute, the on-call rotation, and the roadmap for the first quarter. Let's begin with the migration."),
    ("Rishi", "Sure. We moved the Rotterdam warehouse onto the new inventory service on Tuesday night. The cutover took about four hours, which was longer than the two hours we planned, mostly because the barcode scanners needed a firmware update. We saw three hundred and twelve orders queue up during the window, and all of them were processed by Wednesday morning. No data was lost."),
    ("Daniel", "What caused the firmware issue? Was that on our side or the vendor's?"),
    ("Rishi", "The vendor's. Zebra pushed a version that changed how the scanners authenticate against the local gateway. We had tested with the previous firmware. Going forward, I want to pin firmware versions in the device management console so nothing updates in the middle of a migration."),
    (S, 12),
    ("Karen", "From the finance side, the delay mattered. Halvorsen has a service level agreement that credits them one and a half percent of the monthly fee for every hour of downtime beyond the planned window. So we owe them a credit of roughly three thousand eight hundred euros. I would like to issue that proactively instead of waiting for them to ask."),
    ("Daniel", "Agreed. Proactive is better. Moira, how is the relationship right now?"),
    ("Moira", "Honestly, it is a bit tense. Their operations director, Ingrid Solberg, emailed me twice this week. She is not upset about the migration as much as the invoicing dispute. They believe we double billed them for the pallet tracking add-on in August and September."),
    ("Karen", "We did, partially. The add-on was billed once at the account level and once at the warehouse level for the Gothenburg site. It is a configuration bug in the billing system. The overcharge is eleven thousand four hundred and fifty euros across the two months."),
    (S, 15),
    ("Daniel", "Okay. So we owe them two things: the downtime credit and the billing correction. Let's combine them into one letter so Ingrid sees a single clear number. Karen, can you draft that by Friday?"),
    ("Karen", "Yes. I will also add a line explaining the root cause and the fix, because their procurement team will ask. The fix is already in staging. Engineering estimates it ships in release four point seven."),
    ("Moira", "That would help a lot. I would also suggest we offer a quarterly business review in person. Ingrid mentioned she will be in Amsterdam in the second week of November, and we have people there."),
    ("Daniel", "Good idea. Let's schedule it. Next item, the on-call rotation. Rishi, you raised this last week."),
    ("Rishi", "Right. We currently have four engineers in the rotation, and two of them are in Singapore, which means the overnight pages for Europe land on the same two people every week. Burnout is a real risk. I am proposing we expand the rotation to seven people and move to a follow-the-sun model, with handoffs at nine in the morning Singapore time and nine in the morning Lisbon time."),
    (S, 20),
    ("Karen", "What does that cost? Adding three people to on-call means on-call stipends."),
    ("Rishi", "The stipend is four hundred dollars per person per rotation week. With seven people, each person is on call about once every seven weeks, so the total cost goes up only slightly, roughly two thousand dollars a quarter. I think that is cheap compared to losing an engineer."),
    ("Daniel", "I agree. Approved. Please write it up in the runbook and announce it in the engineering all hands. Last item, the roadmap. Moira, you wanted to raise customer requests."),
    ("Moira", "Yes. The top three requests from our enterprise customers this quarter are single sign-on with Microsoft Entra, an audit log export to Splunk, and the ability to set custom retention periods for shipment data. Halvorsen specifically asked for the retention feature, because Norwegian regulations require them to delete certain records after five years."),
    ("Rishi", "Retention is the hardest of the three. Our event store is append only, so deleting records means building a tombstoning process and rewriting compacted segments. I would estimate six to eight weeks for a careful version. Entra and Splunk are each about two weeks."),
    (S, 15),
    ("Daniel", "Then let's sequence it. Entra first, because it unblocks three deals in the pipeline. Splunk second. Retention third, but let's start the design document now so we are not surprised. Rishi, can you own the design document?"),
    ("Rishi", "Yes. I will have a draft by the twenty-first."),
    ("Karen", "Before we wrap, one more finance item. The cloud bill for September came in at forty-one thousand dollars, up nineteen percent from August. Most of the increase is egress from the Frankfurt region, because the analytics team started exporting raw telemetry to BigQuery every hour instead of every day."),
    ("Rishi", "That is on us. The hourly export was meant to be temporary while they debugged a dashboard. I can switch it back to daily and compress the files with Parquet, which should cut egress by at least seventy percent."),
    ("Moira", "Will the hourly data matter to any customers? Halvorsen's dashboard refreshes every fifteen minutes, but I believe that reads from the operational database, not the export."),
    ("Rishi", "Correct. Customer dashboards read from the read replica in Stockholm. The export only feeds internal analytics, so switching it to daily has no customer impact."),
    (S, 20),
    ("Daniel", "Okay, do it this week. Karen, can you set a budget alert at forty-five thousand so we catch this earlier next time?"),
    ("Karen", "Already done. I set alerts at eighty and one hundred percent of forecast, and they go to the finance channel and to you directly."),
    ("Moira", "One last thing from customer success. Our net promoter score for the quarter is forty-four, down from fifty-one. The comments mention slow support responses on weekends. If the on-call expansion goes through, could we also route weekend priority-one tickets to the on-call engineer?"),
    ("Rishi", "I would rather not mix support tickets into the paging system, because it trains people to ignore pages. But we could add a weekend support shift with a separate rotation. Let me draft both options in the runbook and we can decide next week."),
    ("Daniel", "That works. Let's make sure the decision is in the notes."),
    (S, 15),
    ("Daniel", "Great. To summarize the action items. Karen drafts the combined credit letter by Friday. Moira schedules the in-person review in Amsterdam. Rishi expands the on-call rotation to seven people and starts the retention design document. And I will update the roadmap slide for the board. Anything else? No? Okay, thanks everyone. Talk next week."),
]
LONG_MEETING_PHRASES = [
    "Halvorsen Logistics", "Rotterdam", "three hundred and twelve|312", "Zebra",
    "one and a half percent|1.5%|1.5 percent", "three thousand eight hundred euros|3,800 euros|€3,800",
    "Ingrid Solberg", "pallet tracking", "Gothenburg",
    "eleven thousand four hundred and fifty euros|11,450 euros|€11,450",
    "four point seven|4.7", "Amsterdam", "Singapore", "follow-the-sun|follow the sun",
    "Lisbon", "four hundred dollars|$400", "Microsoft Entra", "Splunk",
    "tombstoning", "twenty-first|21st",
    "forty-one thousand dollars|$41,000", "BigQuery", "Parquet", "Stockholm",
    "net promoter score", "forty-four|44",
]


def render(segments, out_path, rate=185):
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, (voice, val) in enumerate(segments):
            wav = os.path.join(tmp, f"{i:03d}.wav")
            if voice == S:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", str(val),
                                "-i", "anoisesrc=color=brown:amplitude=0.01:sample_rate=16000", wav], check=True)
            else:
                aiff = os.path.join(tmp, f"{i:03d}.aiff")
                subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", aiff, val], check=True)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-af", "apad=pad_dur=0.6",
                                "-ar", "16000", "-ac", "1", wav], check=True)
            parts.append(wav)
        listing = os.path.join(tmp, "list.txt")
        with open(listing, "w") as f:
            f.writelines(f"file '{p}'\n" for p in parts)
        # AAC in .m4a, like a phone voice memo
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
                        "-c:a", "aac", "-b:a", "64k", out_path], check=True)


def build(task, filename, segments, phrases, rate=185):
    d = os.path.join(HERE, task)
    os.makedirs(d, exist_ok=True)
    render(segments, os.path.join(d, filename), rate)
    with open(os.path.join(d, "truth.txt"), "w") as f:
        f.write(" ".join(t for v, t in segments if v != S) + "\n")
    with open(os.path.join(d, "key_phrases.txt"), "w") as f:
        f.write("\n".join(phrases) + "\n")
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          os.path.join(d, filename)], capture_output=True, text=True).stdout.strip()
    print(f"{task}: {filename} {float(dur):.0f}s, {len(phrases)} key phrases")


if __name__ == "__main__":
    build("voice-memo", "Standup, Oct 17.m4a", VOICE_MEMO, VOICE_MEMO_PHRASES)
    build("long-meeting", "ops-review.m4a", LONG_MEETING, LONG_MEETING_PHRASES, rate=160)
