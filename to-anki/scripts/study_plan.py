"""Study plan for a test, plus a check of the Anki settings that govern it. Reads a COPY of the collection.

Run with the pip `anki` package that matches the installed app:
  uv run -q --no-project --python 3.12 --with "anki==<version>" python -I study_plan.py \
      "<profile>/collection.anki2" <empty work dir> --exam-tag CMDA3654::exam::Quiz2 --test 2026-10-30 \
      [--budget 250] [--import <Name>_import.txt]

The daily budget comes from the user's study.json (default ~/anki-settings/settings/study.json):
  {"minutes_per_day": 45, "lectures_per_week": 11.5, "seconds_per_review": 10, "reviews_per_new_card": 10,
   "parent_deck": "School"}
reviews/day = minutes * 60 / seconds_per_review; new cards/day for ALL classes = reviews/day / reviews_per_new_card;
cards per lecture = new/day * 7 / lectures_per_week. The budget is shared: the total limit goes on the parent deck
that holds every course, and one course's test countdown uses part of it. --budget overrides reviews/day.

With --import, --exam-tag and --test default to the values deck.py saved in <Name>_import_plan.json (from
Deck(exam=..., test=...)), so a later session only needs the import file.

--import counts the notes in a deck file that aren't in the collection yet, so the plan is right before the
user imports. The output says which deck to put the limit on, the topic breakdown of the new cards, and every
setting that differs from the rules (with where to click). It never writes to the collection.

The rules (same as the user's anki-settings README):
  1. Overdue reviews -> 0 new cards/day until they're done.
  2. Test countdown: new/day = ceil(new cards left / (days until test - 1)). With 1 day or less, see every
     remaining new card today (the Today only tab), cheat-sheet cards suspended.
  3. Cap: new/day <= the total for all classes (each new card a day turns into ~10 reviews a day). Over it:
     suspend cheat-sheet cards and low-priority topics, or accept a heavier load.
  4. No test: no per-course number; the parent deck's total spreads new cards across classes.
"""
import argparse
import csv
import datetime
import json
import math
import os
import re
import shutil
from collections import Counter

from anki.collection import Collection

MIN_PER_NEW_CARD = 1  # first view plus its 15-minute repeat, rough
FSRS_HELPER = "759844606"


def card_count(row):
    """Cards a not-yet-imported row will create."""
    notetype, fields = row[0], row[4:]
    if notetype == "Basic+":
        return 1 + (1 if len(fields) > 4 and fields[4].strip() else 0)
    if notetype in ("Cloze+", "Image Occlusion"):
        return len(set(re.findall(r"\{\{c(\d+)::", fields[0]))) or 1
    return 1


def topic_of(tags):
    return next((t.split("::")[-1] for t in tags if "::topic::" in t.lower()), "untagged")


def is_cheat(tags):
    return any("::cheatsheet::" in t.lower() for t in tags)


def common_deck(names):
    parts = [n.split("::") for n in names]
    out = []
    for level in zip(*parts):
        if len(set(level)) != 1:
            break
        out.append(level[0])
    return "::".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("collection")
    ap.add_argument("work")
    ap.add_argument("--exam-tag", help="default: from <import>_plan.json")
    ap.add_argument("--test", help="YYYY-MM-DD; default: from <import>_plan.json; omit when no test is scheduled")
    ap.add_argument("--budget", type=int, help="reviews/day for all classes (default: from study.json, else 250)")
    ap.add_argument("--study", default=os.path.expanduser("~/anki-settings/settings/study.json"))
    ap.add_argument("--import", dest="import_file")
    a = ap.parse_args()
    if a.import_file:
        saved = os.path.splitext(a.import_file)[0] + "_plan.json"
        if os.path.exists(saved):
            with open(saved, encoding="utf-8") as f:
                plan = json.load(f)
            a.exam_tag = a.exam_tag or plan.get("exam_tag")
            a.test = a.test or plan.get("test")
    if not a.exam_tag:
        ap.error("--exam-tag is required (or --import with a saved _plan.json)")
    study = {"seconds_per_review": 10, "reviews_per_new_card": 10, "parent_deck": "School"}
    if os.path.exists(a.study):
        with open(a.study, encoding="utf-8") as f:
            study.update(json.load(f))
    if a.budget is None:
        a.budget = round(study["minutes_per_day"] * 60 / study["seconds_per_review"]) if "minutes_per_day" in study else 250
    cap = a.budget // study["reviews_per_new_card"]
    per_lecture = round(cap * 7 / study["lectures_per_week"]) if study.get("lectures_per_week") else None

    dst = os.path.join(a.work, "plan.anki2")
    for ext in ("", "-wal"):
        if os.path.exists(a.collection + ext):
            shutil.copy(a.collection + ext, dst + ext)
    col = Collection(dst)
    scope = f'"tag:{a.exam_tag}" -is:suspended'
    topics, decks, sizes = Counter(), set(), Counter()
    new_left = cheat_new = 0
    for cid in col.find_cards(f"{scope} is:new"):
        card = col.get_card(cid)
        tags = card.note().tags
        topics[topic_of(tags)] += 1
        cheat_new += is_cheat(tags)
        new_left += 1
    for cid in col.find_cards(f'"tag:{a.exam_tag}"'):
        card = col.get_card(cid)
        decks.add(col.decks.name(card.did))
        if card.queue != -1:  # suspended cards cost no reviews
            sizes[topic_of(card.note().tags)] += 1
    overdue = len(col.find_cards(f"{scope} is:review prop:due<0"))
    introduced_today = len(col.find_cards(f'"tag:{a.exam_tag}" introduced:1'))
    pending = 0
    if a.import_file:
        known = set(col.db.list("select guid from notes"))
        with open(a.import_file, encoding="utf-8") as f:
            for row in csv.reader((line for line in f if not line.startswith("#")), delimiter="\t"):
                tags = row[2].split()
                if row[3] in known or a.exam_tag.lower() not in [t.lower() for t in tags]:
                    continue
                n = card_count(row)
                topics[topic_of(tags)] += n
                cheat_new += n if is_cheat(tags) else 0
                sizes[topic_of(tags)] += n
                new_left += n
                pending += n
                decks.add(row[1])

    target = common_deck(sorted(decks)) if decks else None
    rollover = col.get_preferences().scheduling.rollover
    today = (datetime.datetime.now() - datetime.timedelta(hours=rollover)).date()

    # ---------- plan ----------
    parent = study["parent_deck"]
    has_parent = bool(col.decks.id_for_name(parent))
    ceiling = math.ceil(cap / 2)
    course_deck = None
    if target and target.startswith(parent + "::"):
        course_deck = "::".join(target.split("::")[:len(parent.split("::")) + 1])
    print(f"Budget: {a.budget} reviews/day for all classes -> {cap} new cards/day in total"
          + (f", about {per_lecture} cards per lecture." if per_lecture else "."))
    print(f"Total limit: {cap}/day on the This deck tab of {parent!r}"
          + ("" if has_parent else f" (no such deck yet: create {parent!r} and drag every course deck into it)")
          + ". Study it to mix every class; with Limits start from top on, studying one class or test also "
          "stays within it.")
    print(f"Per-course ceiling: {ceiling}/day as the preset's New cards/day, so one class with a big backlog "
          "can't take the whole total (a test countdown below raises its own deck's limit).")
    print(f"New cards for {a.exam_tag}: {new_left} ({pending} still to import, {cheat_new} on the cheat sheet), "
          f"{overdue} overdue reviews.")
    print(f"Set this test's limits on: {target or '(no deck yet)'}")
    print(f"By topic (active cards / new cards; target about {per_lecture} per lecture):" if per_lecture else
          "By topic (active cards / new cards):")
    for t, n in sizes.most_common():
        heavy = per_lecture and n > 1.5 * per_lecture
        print(f"  {t:36} {n:4} / {topics[t]:<4}" + ("  heavy for one lecture: trim if it is one" if heavy else ""))
    if overdue:
        print(f"PLAN: 0 new cards/day until the {overdue} overdue reviews are done (rule 1).")
    elif not a.test:
        print(f"PLAN: no test date, so no per-course limit: the {cap}/day total on {parent!r} spreads new cards "
              "across classes (rule 4).")
    else:
        days = (datetime.date.fromisoformat(a.test) - today).days
        if days <= 1:
            todo = new_left - cheat_new
            print(f"PLAN: test in {max(days, 0)} day(s). Suspend the {cheat_new} cheat-sheet cards if a sheet is allowed, "
                  f"then set Today only to {todo + introduced_today} ({todo} left + {introduced_today} already started today) "
                  f"on {target!r}"
                  + (f", and at least that on {course_deck!r} and {parent!r} too, or their daily caps stop it "
                     "(Today only resets tomorrow)" if course_deck else "")
                  + f" (rule 2), and see them all today, highest-yield topics first: "
                  f"about {todo * MIN_PER_NEW_CARD / 60:.1f} hours. The morning of the test, filtered deck "
                  f'"tag:{a.exam_tag}" rated:2:1 with rescheduling off.')
        else:
            need = math.ceil(new_left / (days - 1))
            if need <= cap:
                raise_course = f" Also set {course_deck!r} to {need} on its This deck tab, since the {ceiling}/day " \
                    "course ceiling would cap it." if course_deck and need > ceiling else ""
                print(f"PLAN: {need} new cards/day on this deck's This deck tab: all seen by the day before the test, "
                      f"{days} days away (rule 2).{raise_course} That's {need} of the {cap}/day total; other classes "
                      f"share the rest until the test. About {need * MIN_PER_NEW_CARD} minutes of new cards a day.")
            else:
                without = math.ceil((new_left - cheat_new) / (days - 1))
                print(f"PLAN: {need}/day needed in {days - 1} days, over the {cap}/day total for all classes (rule 3). "
                      + (f"Suspending the {cheat_new} cheat-sheet cards makes it {without}/day"
                         + (", which fits." if without <= cap else "; still over, so also suspend low-priority topics "
                            "or accept a heavier load.") if cheat_new else
                         "Suspend low-priority topics or accept a heavier load."))

    # ---------- settings check ----------
    did = col.decks.id_for_name(target) if target else None
    conf = col.decks.config_dict_for_deck_id(did) if did else col.decks.get_config(1)
    deck = col.decks.get(did) if did else None
    where = f'Deck options for {target or "the deck"} (preset "{conf["name"]}")'
    issues = []
    if not col.get_config("fsrs", False):
        issues.append(f"FSRS is off. Turn it on: {where} > FSRS.")
    if not 0.85 <= conf.get("desiredRetention", 0.9) <= 0.95:
        issues.append(f"Desired retention {conf['desiredRetention']:.0%}; use 90%: {where} > FSRS.")
    for label, steps, section in (("Learning", conf["new"]["delays"], "New Cards"),
                                  ("Relearning", conf["lapse"]["delays"], "Lapses")):
        if any(s >= 1440 for s in steps) or len(steps) > 2:
            issues.append(f"{label} steps {steps} (minutes); use one step under a day, e.g. 15m: {where} > {section}.")
    if conf["rev"]["perDay"] < 9999:
        issues.append(f"Maximum reviews/day {conf['rev']['perDay']}; keep it 9999 so due cards are never hidden: "
                      f"{where} > Daily Limits.")
    if deck and deck.get("reviewLimit") is not None and deck["reviewLimit"] < 9999:
        issues.append(f"This deck review limit {deck['reviewLimit']}; clear it or set 9999: {where} > Daily Limits > This deck.")
    bury = {"new": conf["new"].get("bury"), "review": conf["rev"].get("bury"), "interday": conf.get("buryInterdayLearning")}
    if not all(bury.values()):
        issues.append(f"Burying off for {', '.join(k for k, v in bury.items() if not v)} siblings; turn all three on: "
                      f"{where} > Burying.")
    if conf.get("newGatherPriority") not in (3, 4):
        issues.append(f"New card gather order isn't random; use Random notes: {where} > Display Order.")
    if conf["lapse"].get("leechAction") != 1:
        issues.append(f"Leech action suspends cards; use Tag Only and rewrite leeches: {where} > Lapses.")
    reviews = col.db.scalar("select count(*) from revlog where type < 4")
    if not conf.get("fsrsParams6") and reviews >= 400:
        issues.append(f"FSRS still on default parameters with {reviews} reviews; click Optimize Current Preset: {where} > FSRS.")
    if not col.get_config("applyAllParentLimits", False):
        issues.append("Limits start from top is off, so studying one class or test directly skips the total: "
                      f"{where} > Daily Limits > Limits start from top (on).")
    pid = col.decks.id_for_name(parent)
    if pid and col.decks.get(pid).get("newLimit") != cap:
        issues.append(f"{parent!r} should hold the total: Deck options for {parent!r} > Daily Limits > New cards/day > "
                      f"This deck = {cap}.")
    if conf["new"]["perDay"] != ceiling:
        issues.append(f"Preset New cards/day is {conf['new']['perDay']}; set the per-course ceiling {ceiling}: "
                      f"{where} > Daily Limits > Preset.")
    if not os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(a.collection)), "addons21", FSRS_HELPER)):
        issues.append(f"FSRS Helper isn't installed (Advance before tests): Tools > Add-ons > Get Add-ons > {FSRS_HELPER}.")
    limits = {k: deck.get(k) for k in ("newLimit", "newLimitToday") if deck and deck.get(k) is not None}
    print(f"Current new-card limits: preset {conf['new']['perDay']}/day" + (f", this deck {limits}" if limits else ""))
    print("SETTINGS: all match the rules." if not issues else "SETTINGS to change:")
    for i in issues:
        print("  -", i)
    col.close()


if __name__ == "__main__":
    main()
