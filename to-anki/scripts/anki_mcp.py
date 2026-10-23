"""Talk to a running Anki through the "Anki MCP Server" add-on (AnkiWeb 124672614, default http://127.0.0.1:3141/).

    anki_mcp.py ping                                  exit 0 and print the server version if Anki is reachable
    anki_mcp.py tools                                 list the add-on's tools (read-only ones marked "ro")
    anki_mcp.py call <tool> '<json args>'             call one tool and print its result as JSON
    anki_mcp.py push <new_import.txt> [<old_import.txt>] [--dry-run]
                                                      apply a revised import file to the live collection

push matches each row of the new file to a note in the collection by its first field: the new text first,
then the text of the same row in the old file (rows keep their order between versions). It diffs the match
against the live note and sends only the fields that differ, adds tags that are missing, and removes only
tags that the old row had and the new row dropped, so tags the user added stay. Rows past the end of the
old file that match nothing are added as new notes. Anything else that matches nothing is an error, and
nothing is written until every row is resolved. Updating a note keeps its review history.

Standard library only: run it with plain `python3 -I`.
"""
import csv
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

URL = os.environ.get("ANKI_MCP_URL", "http://127.0.0.1:3141/")
KEY = os.environ.get("ANKI_MCP_KEY", "")  # the add-on's http_api_key, if the user set one
BATCH = 50


class Client:
    def __init__(self):
        self.session = None
        self.info = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "to-anki", "version": "1"}})["serverInfo"]
        self._rpc("notifications/initialized", notify=True)

    def _rpc(self, method, params=None, notify=False):
        body = {"jsonrpc": "2.0", "method": method, **({} if notify else {"id": 1})}
        if params is not None:
            body["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["mcp-session-id"] = self.session
        if KEY:
            headers["Authorization"] = f"Bearer {KEY}"
        req = urllib.request.Request(URL, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            self.session = r.headers.get("mcp-session-id") or self.session
            text = r.read().decode()
        if notify:
            return None
        # The server answers as server-sent events: the JSON-RPC reply is on a "data:" line.
        data = [ln[5:] for ln in text.splitlines() if ln.startswith("data:")]
        reply = json.loads(data[-1] if data else text)
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply["result"]

    def call(self, tool, **args):
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        if res.get("isError"):
            raise RuntimeError(f"{tool}: {res['content'][0]['text'][:500]}")
        return res.get("structuredContent") or json.loads(res["content"][0]["text"])


def norm(field):
    """Compare fields the way they render: Anki's editor rewrites HTML when a note is opened and saved
    (adds <tbody>, turns spaces into &nbsp;, swaps entity forms), which isn't a real change."""
    field = re.sub(r"</?tbody>", "", field)
    return re.sub(r"\s+", " ", html.unescape(field).replace("\xa0", " ")).strip()


def read_import(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.reader((ln for ln in f if not ln.startswith("#")), delimiter="\t"))


def live_notes(c, decks):
    """{(note type, first field): note} for every note in the given decks."""
    ids = []
    for deck in decks:
        offset = 0
        while True:
            page = c.call("find_notes", query=f'"deck:{deck}"', limit=500, offset=offset)
            ids += page["noteIds"]
            if not page.get("hasMore"):
                break
            offset += len(page["noteIds"])
    by_key = {}
    for i in range(0, len(ids), BATCH):
        for n in c.call("notes_info", notes=ids[i:i + BATCH])["notes"]:
            fields = sorted(n["fields"].items(), key=lambda kv: kv[1]["order"])
            n["values"] = [v["value"] for _, v in fields]
            key = (n["modelName"], norm(n["values"][0]))
            if key in by_key:
                raise SystemExit(f"two notes share a first field, can't match safely: {key[1][:80]!r}")
            by_key[key] = n
    return by_key


def push(new_path, old_path=None, dry_run=False):
    new, old = read_import(new_path), read_import(old_path) if old_path else []
    if len(old) > len(new):
        raise SystemExit(f"old file has {len(old)} notes, new has {len(new)}: removed notes need manual handling")
    c = Client()
    names = {m: c.call("model_field_names", model_name=m)["field_names"] for m in {r[0] for r in new}}
    live = live_notes(c, sorted({r[1] for r in new}))

    updates, tag_add, tag_remove, adds, missing = [], {}, {}, [], []
    for i, row in enumerate(new):
        model, deck, tags = row[0], row[1], row[2].split()
        fields = row[4:4 + len(names[model])]
        prev = old[i] if i < len(old) else None
        note = live.get((model, norm(fields[0]))) or (prev and live.get((model, norm(prev[4]))))
        if note is None:
            (adds if prev is None else missing).append((model, deck, tags, fields))
            continue
        changed = {name: v for name, v, cur in zip(names[model], fields, note["values"]) if norm(v) != norm(cur)}
        if changed:
            updates.append({"id": note["noteId"], "fields": changed})
        have = set(note["tags"])
        for t in set(tags) - have:
            tag_add.setdefault(t, []).append(note["noteId"])
        for t in (set(prev[2].split()) - set(tags) if prev else set()) & have:
            tag_remove.setdefault(t, []).append(note["noteId"])

    if missing:
        for m in missing:
            print("NOT FOUND:", m[0], "|", m[3][0][:100])
        raise SystemExit(f"{len(missing)} rows match no note; nothing was written")
    print(f"{len(new)} rows: {len(updates)} notes to update, {len(adds)} to add, "
          f"tags to add {sum(map(len, tag_add.values()))}, tags to remove {sum(map(len, tag_remove.values()))}")
    for u in updates:
        print("  update", u["id"], sorted(u["fields"]))
    for a in adds:
        print("  add", a[0], "|", a[3][0][:80])
    if dry_run:
        return

    for i in range(0, len(updates), BATCH):
        c.call("update_notes", notes=updates[i:i + BATCH])
    for t, ids in tag_add.items():
        c.call("tag_add", notes=ids, tags=t)
    for t, ids in tag_remove.items():
        c.call("tag_remove", notes=ids, tags=t)
    for model, deck, tags, fields in adds:
        c.call("add_note", deck_name=deck, model_name=model, tags=tags,
               fields=dict(zip(names[model], fields)))

    # Read everything back: each row must now exist with exactly the file's fields.
    live = live_notes(c, sorted({r[1] for r in new}))
    bad = [r[4][:80] for r in new
           if (n := live.get((r[0], norm(r[4])))) is None
           or list(map(norm, n["values"])) != list(map(norm, r[4:4 + len(names[r[0]])]))]
    print("verified: all rows match the file" if not bad else f"MISMATCH after push: {bad}")
    sys.exit(1 if bad else 0)


def main(argv):
    if not argv or argv[0] not in ("ping", "tools", "call", "push"):
        raise SystemExit(__doc__)
    try:
        if argv[0] == "ping":
            info = Client().info
            print(info["name"], info.get("version", ""))
        elif argv[0] == "tools":
            for t in Client()._rpc("tools/list", {})["tools"]:
                ro = "ro" if t.get("annotations", {}).get("readOnlyHint") else "  "
                print(f'{t["name"]:24} {ro} {t.get("description", "").split(". ")[0][:100]}')
        elif argv[0] == "call":
            print(json.dumps(Client().call(argv[1], **json.loads(argv[2] if len(argv) > 2 else "{}")), indent=1))
        else:
            files = [a for a in argv[1:] if a != "--dry-run"]
            push(*files, dry_run="--dry-run" in argv)
    except (urllib.error.URLError, ConnectionError) as e:
        raise SystemExit(f"Anki MCP server not reachable at {URL} ({e}). Is Anki open with the add-on enabled?")


if __name__ == "__main__":
    main(sys.argv[1:])
