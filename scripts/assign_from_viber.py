#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
import xmlrpc.client

CODE_RE = re.compile(r"\bTSK-[A-Z0-9]+-\d+\b")

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def prompt_multiline():
    print("Paste Viber messages. End with Ctrl-D:")
    return sys.stdin.read()


def extract_codes(text):
    return sorted(set(CODE_RE.findall(text)))


def xmlrpc_login(url, db, user, password):
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, password, {})
    if not uid:
        raise RuntimeError("Authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return uid, models


def search_read(models, db, uid, password, model, domain, fields, limit=None, order=None):
    kwargs = {"fields": fields}
    if limit is not None:
        kwargs["limit"] = limit
    if order is not None:
        kwargs["order"] = order
    return models.execute_kw(db, uid, password, model, "search_read", [domain], kwargs)


def read(models, db, uid, password, model, ids, fields):
    return models.execute_kw(db, uid, password, model, "read", [ids], {"fields": fields})


def write(models, db, uid, password, model, ids, values):
    return models.execute_kw(db, uid, password, model, "write", [ids, values])


def resolve_users(models, db, uid, password, identifiers):
    resolved = {}
    for ident in identifiers:
        dom = ["|", "|", ("login", "=", ident), ("email", "=", ident), ("name", "=", ident)]
        users = search_read(models, db, uid, password, "res.users", dom, ["id", "name", "login", "email"], limit=1)
        if users:
            resolved[ident] = users[0]
        else:
            resolved[ident] = None
    return resolved


def pick_project_key(preferred_map, project_id, project_name):
    if project_name in preferred_map:
        return project_name
    pid = str(project_id)
    if pid in preferred_map:
        return pid
    if "default" in preferred_map:
        return "default"
    return None


def resolve_role(role_map, user_rec):
    if not role_map:
        return "-"
    name = user_rec.get("name")
    login = user_rec.get("login")
    email = user_rec.get("email")
    for key in (name, login, email):
        if key and key in role_map:
            return role_map[key]
    return "-"


def main():
    ap = argparse.ArgumentParser(description="Assign Odoo tasks from Viber messages")
    ap.add_argument("--config", default="config/assign_from_viber.json")
    ap.add_argument("--input", help="Path to text file containing Viber messages")
    ap.add_argument("--dotenv", default=".env", help="Path to .env file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_dotenv(args.dotenv)
    cfg = load_config(args.config)
    url = os.getenv("ODOO_URL")
    db = os.getenv("ODOO_DB")
    user = os.getenv("ODOO_USER")
    password = os.getenv("ODOO_PASSWORD")
    if not all([url, db, user, password]):
        eprint("Missing ODOO_URL, ODOO_DB, ODOO_USER, or ODOO_PASSWORD env vars")
        sys.exit(1)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = prompt_multiline()

    codes = extract_codes(text)
    if not codes:
        eprint("No ticket codes found")
        sys.exit(1)

    try:
        uid, models = xmlrpc_login(url, db, user, password)
    except Exception as e:
        eprint(f"Login failed: {e}")
        sys.exit(1)
    me = read(models, db, uid, password, "res.users", [uid], ["name", "login"])
    if me:
        print(f"Login OK: {me[0].get('name')} ({me[0].get('login')})")

    open_stage_names = cfg.get("open_stage_names", [])
    preferred_map = cfg.get("preferred_developers", {})
    role_map = cfg.get("developer_roles", {})
    recent_days = int(cfg.get("recent_days", 7))

    for code in codes:
        print(f"\n=== {code} ===")
        tasks = search_read(
            models, db, uid, password,
            "project.task",
            [("code", "=", code)],
            ["id", "name", "code", "project_id", "user_id", "priority", "stage_id", "write_date"],
        )
        print("taskss",tasks)
        if not tasks:
            print("No task found")
            continue
        if len(tasks) > 1:
            print("Multiple tasks found:")
            for i, t in enumerate(tasks, 1):
                pname = t["project_id"][1] if t.get("project_id") else "-"
                uname = t["user_id"][1] if t.get("user_id") else "-"
                print(f"{i}. {t['id']} | {t['name']} | Project: {pname} | Assigned: {uname}")
            sel = input("Select task number: ").strip()
            try:
                task = tasks[int(sel) - 1]
            except Exception:
                print("Invalid selection, skipping")
                continue
        else:
            task = tasks[0]

        project = task.get("project_id")
        if not project:
            print("Task has no project; skipping")
            continue
        project_id, project_name = project
        current_user = task.get("user_id")
        current_user_name = current_user[1] if current_user else "-"
        priority = task.get("priority", "0")

        # Resolve open stages
        stage_id_list = []
        if open_stage_names:
            stage_ids = search_read(
                models, db, uid, password,
                "project.task.type",
                [("name", "in", open_stage_names)],
                ["id", "name"],
            )
            stage_id_list = [s["id"] for s in stage_ids]
        if not stage_id_list:
            # Fallback: use non-folded stages as open
            stage_ids = search_read(
                models, db, uid, password,
                "project.task.type",
                [("fold", "=", False)],
                ["id", "name", "fold"],
            )
            stage_id_list = [s["id"] for s in stage_ids]
            if not stage_id_list:
                print("No open stages found; skipping")
                continue

        # Count open tasks per developer in project
        domain = [("project_id", "=", project_id), ("stage_id", "in", stage_id_list), ("user_id", "!=", False)]
        open_tasks = search_read(
            models, db, uid, password,
            "project.task",
            domain,
            ["id", "user_id", "write_date"],
        )
        counts = {}
        recent = {}
        cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
        for t in open_tasks:
            uid_pair = t.get("user_id")
            if not uid_pair:
                continue
            uid_val, uname = uid_pair
            counts[uid_val] = counts.get(uid_val, 0) + 1
            wdate = t.get("write_date")
            if wdate:
                try:
                    dt = datetime.strptime(wdate, "%Y-%m-%d %H:%M:%S")
                    if dt >= cutoff:
                        recent[uid_val] = True
                except Exception:
                    pass

        # Preferred developers
        pref_key = pick_project_key(preferred_map, project_id, project_name)
        preferred_ids = None
        if pref_key:
            resolved = resolve_users(models, db, uid, password, preferred_map[pref_key])
            preferred_ids = [u["id"] for u in resolved.values() if u]
            missing = [k for k, v in resolved.items() if v is None]
            if missing:
                print("Preferred devs not found:", ", ".join(missing))

        # Candidate list
        candidate_ids = preferred_ids if preferred_ids else list(counts.keys())
        if not candidate_ids:
            print("No candidates found")
            continue

        # Build candidate info
        cand_records = read(models, db, uid, password, "res.users", candidate_ids, ["id", "name", "login", "email"]) 
        candidates = []
        for u in cand_records:
            uid_val = u["id"]
            candidates.append({
                "id": uid_val,
                "name": u["name"],
                "login": u.get("login"),
                "email": u.get("email"),
                "role": resolve_role(role_map, u),
                "count": counts.get(uid_val, 0),
                "recent": recent.get(uid_val, False),
            })

        # Sort by most tasks in project
        candidates.sort(key=lambda x: (-x["count"], x["name"]))

        print(f"Task: {task['name']}")
        print(f"Project: {project_name} | Priority: {priority} | Current: {current_user_name}")
        print("Candidates (most project tasks first):")
        for i, c in enumerate(candidates, 1):
            rec = " recent" if c["recent"] else ""
            print(f"{i}. {c['name']} | {c['role']} | open tasks in project: {c['count']}{rec}")

        top = candidates[0]
        yn = input(f"Assign to {top['name']}? [y/N] ").strip().lower()
        if yn != "y":
            sel = input("Enter candidate number to assign (or blank to skip): ").strip()
            if not sel:
                print("Skipped")
                continue
            try:
                top = candidates[int(sel) - 1]
            except Exception:
                print("Invalid selection, skipped")
                continue

        if args.dry_run:
            print(f"Dry-run: would assign task {task['id']} to {top['name']}")
            continue

        ok = write(models, db, uid, password, "project.task", [task["id"]], {"user_id": top["id"]})
        if ok:
            print(f"Assigned to {top['name']}")
        else:
            print("Assign failed")

if __name__ == "__main__":
    main()
