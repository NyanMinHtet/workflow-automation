#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime
import xmlrpc.client


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


def main():
    ap = argparse.ArgumentParser(description="Generate daily to-do list message")
    ap.add_argument("--config", default="config/assign_from_viber.json")
    ap.add_argument("--dotenv", default=".env", help="Path to .env file")
    ap.add_argument("--by", default="NMH Ama @name", help="Signature line")
    ap.add_argument("--date", help="Override date (YYYY-MM-DD)")
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

    try:
        uid, models = xmlrpc_login(url, db, user, password)
    except Exception as e:
        eprint(f"Login failed: {e}")
        sys.exit(1)

    # Resolve stages for today/this week
    stage_names = cfg.get("todo_stage_names", [])
    if not stage_names:
        eprint("Config missing todo_stage_names")
        sys.exit(1)

    stages = search_read(
        models, db, uid, password,
        "project.task.type",
        [("name", "in", stage_names)],
        ["id", "name"],
    )
    stage_ids = [s["id"] for s in stages]
    if not stage_ids:
        eprint("No matching stages found")
        sys.exit(1)

    tasks = search_read(
        models, db, uid, password,
        "project.task",
        [("user_id", "=", uid), ("stage_id", "in", stage_ids)],
        ["id", "name", "code", "project_id", "stage_id"],
        order="project_id asc, code asc",
    )

    # Group by project name
    grouped = {}
    for t in tasks:
        project = t.get("project_id")
        pname = project[1] if project else "No Project"
        grouped.setdefault(pname, []).append(t)

    # Build header date
    if args.date:
        dt = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        dt = datetime.now()
    date_str = dt.strftime("%d %b %Y")

    print(f"{date_str} To Do List By {args.by}")
    print()

    for pname in sorted(grouped.keys()):
        print(pname)
        for t in grouped[pname]:
            code = t.get("code") or "-"
            name = t.get("name") or "-"
            print(f"{code} - {pname} - {name}")
        print()


if __name__ == "__main__":
    main()
