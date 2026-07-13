#!/usr/bin/env python3
import argparse
import html
import json
import locale
import os
import re
import ssl
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import http.client
import xmlrpc.client

CODE_RE = re.compile(r"\bTSK-[A-Z0-9]+-\d+\b")
TAG_RE = re.compile(r"<[^>]+>")

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def configure_utf8_io():
    # Ensure terminal output can render non-ASCII text (e.g., Myanmar script).
    preferred = locale.getpreferredencoding(False) or ""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    return preferred.lower() == "utf-8"


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


class _SslTransport(xmlrpc.client.Transport):
    def __init__(self, context=None, use_datetime=False, use_builtin_types=False):
        super().__init__(use_datetime=use_datetime, use_builtin_types=use_builtin_types)
        self._context = context
        self.timeout = None

    def make_connection(self, host):
        if self._context is not None:
            return http.client.HTTPSConnection(host, timeout=self.timeout, context=self._context)
        return super().make_connection(host)


def _build_xmlrpc_server_proxy(url):
    if url.startswith("https://"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        transport = _SslTransport(context=context)
        return xmlrpc.client.ServerProxy(url, transport=transport)
    return xmlrpc.client.ServerProxy(url)


def xmlrpc_login(url, db, user, password):
    common = _build_xmlrpc_server_proxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, password, {})
    if not uid:
        raise RuntimeError("Authentication failed")
    models = _build_xmlrpc_server_proxy(f"{url}/xmlrpc/2/object")
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
        exact_dom = ["|", "|", ("login", "=", ident), ("email", "=", ident), ("name", "=", ident)]
        users = search_read(models, db, uid, password, "res.users", exact_dom, ["id", "name", "login", "email"], limit=1)
        if users:
            resolved[ident] = users[0]
            continue

        normalized_ident = re.sub(r"\s+", " ", (ident or "")).strip().lower()
        fallback_dom = ["|", "|", ("login", "ilike", ident), ("email", "ilike", ident), ("name", "ilike", ident)]
        fallback_users = search_read(models, db, uid, password, "res.users", fallback_dom, ["id", "name", "login", "email"], limit=5)
        matched = None
        if fallback_users:
            for user in fallback_users:
                candidate_name = re.sub(r"\s+", " ", (user.get("name") or "")).strip().lower()
                candidate_login = re.sub(r"\s+", " ", (user.get("login") or "")).strip().lower()
                candidate_email = re.sub(r"\s+", " ", (user.get("email") or "")).strip().lower()
                if candidate_name == normalized_ident or candidate_login == normalized_ident or candidate_email == normalized_ident:
                    matched = user
                    break
            if not matched:
                matched = fallback_users[0]
        resolved[ident] = matched
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
    candidates = [name, login, email]
    normalized = {}
    for key in candidates:
        if not key:
            continue
        normalized[re.sub(r"\s+", " ", str(key)).strip().lower()] = key
    for key in normalized:
        if key in role_map:
            return role_map[key]
    for key in role_map:
        normalized_key = re.sub(r"\s+", " ", str(key)).strip().lower()
        if normalized_key in normalized:
            return role_map[key]
    return "-"


def normalize_text(value):
    return (value or "").strip().lower()


def extract_skill_matches(text, developer_skills, skill_keywords):
    if not text:
        return []
    normalized = normalize_text(text)
    matched = []
    for developer, skills in (developer_skills or {}).items():
        for skill in skills or []:
            keywords = skill_keywords.get(skill, []) or []
            if any(keyword and keyword.lower() in normalized for keyword in keywords):
                matched.append((developer, skill))
                break
    return matched


def score_candidate(candidate, task_text, priority, open_task_count, in_progress_count, assignment_history, weights, developer_skills=None, skill_keywords=None):
    role = normalize_text(candidate.get("role"))
    developer_name = candidate.get("name")
    score = 0.0
    reasons = []

    developer_skills = developer_skills or {}
    skill_keywords = skill_keywords or {}

    skills = developer_skills.get(developer_name, []) or []
    matched_skills = []
    if task_text:
        normalized = normalize_text(task_text)
        for skill in skills:
            keywords = skill_keywords.get(skill, []) or []
            if any(keyword and keyword.lower() in normalized for keyword in keywords):
                matched_skills.append(skill)
    if matched_skills:
        score += weights.get("skill_match", 0) * len(matched_skills)
        reasons.append(f"skill match: {', '.join(matched_skills)}")

    if role and "senior" in role:
        priority_value = int(str(priority or "0").strip() or "0")
        if priority_value >= 3:
            score += weights.get("urgency", 0) * 2
            reasons.append("senior fit for high priority")

    if open_task_count is not None:
        score += weights.get("workload", 0) * max(0, 1 - open_task_count)
        reasons.append(f"workload bonus: {open_task_count} open")

    if in_progress_count and in_progress_count > 1:
        score -= weights.get("in_progress_penalty", 0) * (in_progress_count - 1)
        reasons.append(f"in-progress penalty: {in_progress_count}")

    history_count = assignment_history.get(developer_name, 0)
    if history_count:
        score += weights.get("fairness", 0) * max(0, 2 - history_count)
        reasons.append(f"fairness bonus: recent assignments {history_count}")

    return {
        "id": candidate.get("id"),
        "name": developer_name,
        "role": candidate.get("role"),
        "count": candidate.get("count", 0),
        "in_progress": candidate.get("in_progress", 0),
        "recent": candidate.get("recent", False),
        "score": round(score, 2),
        "reasons": reasons,
    }


def html_to_text(value):
    if not value:
        return ""
    text = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("</p>", "\n").replace("</li>", "\n")
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    compact = []
    prev_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        compact.append(line)
        prev_blank = is_blank
    return "\n".join(compact).strip()


def resolve_stage_ids(models, db, uid, password, stage_names=None, fallback_domain=None):
    stage_names = stage_names or []
    if stage_names:
        stage_ids = search_read(
            models, db, uid, password,
            "project.task.type",
            [("name", "in", stage_names)],
            ["id", "name"],
        )
        ids = [s["id"] for s in stage_ids]
        if ids:
            return ids
    if fallback_domain:
        stage_ids = search_read(
            models, db, uid, password,
            "project.task.type",
            fallback_domain,
            ["id", "name"],
        )
        return [s["id"] for s in stage_ids]
    return []


def main():
    locale_is_utf8 = configure_utf8_io()
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
    if not locale_is_utf8:
        eprint("Warning: terminal locale is not UTF-8. Myanmar text may look broken. Try: export LANG=en_US.UTF-8")

    open_stage_names = cfg.get("open_stage_names", [])
    in_progress_stage_names = cfg.get("in_progress_stage_names", ["In Progress", "IN PROGRESS"])
    preferred_map = cfg.get("preferred_developers", {})
    role_map = cfg.get("developer_roles", {})
    recent_days = int(cfg.get("recent_days", 7))

    for code in codes:
        print(f"\n=== {code} ===")
        tasks = search_read(
            models, db, uid, password,
            "project.task",
            [("code", "=", code)],
            ["id", "name", "code", "project_id", "user_id", "priority", "stage_id", "write_date", "description"],
        )
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
        details = html_to_text(task.get("description")) or "(No description on task)"

        print(f"Task: {task['name']}")
        print(f"Project: {project_name} | Priority: {priority} | Current: {current_user_name}")
        print("Ticket description:")
        print(details)

        # Resolve open stages
        stage_id_list = resolve_stage_ids(
            models, db, uid, password,
            stage_names=open_stage_names,
            fallback_domain=[("fold", "=", False)],
        )
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
        if preferred_ids:
            candidate_ids = list(dict.fromkeys(preferred_ids + list(counts.keys())))
        if not candidate_ids:
            print("No candidates found")
            continue

        in_progress_stage_ids = resolve_stage_ids(
            models, db, uid, password,
            stage_names=in_progress_stage_names,
            fallback_domain=[("name", "ilike", "progress")],
        )
        in_progress_counts = {}
        if in_progress_stage_ids:
            in_progress_tasks = search_read(
                models, db, uid, password,
                "project.task",
                [("stage_id", "in", in_progress_stage_ids), ("user_id", "in", candidate_ids)],
                ["id", "user_id"],
            )
            for t in in_progress_tasks:
                uid_pair = t.get("user_id")
                if not uid_pair:
                    continue
                uid_val = uid_pair[0]
                in_progress_counts[uid_val] = in_progress_counts.get(uid_val, 0) + 1

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
                "in_progress": in_progress_counts.get(uid_val, 0),
                "recent": recent.get(uid_val, False),
            })

        developer_skills = cfg.get("developer_skills", {})
        skill_keywords = cfg.get("skill_keywords", {})
        scoring_weights = cfg.get("scoring_weights", {
            "skill_match": 3.0,
            "workload": 1.0,
            "fairness": 0.8,
            "urgency": 1.0,
            "in_progress_penalty": 1.0,
        })
        assignment_history = defaultdict(int)
        history_path = os.path.join(os.path.dirname(args.config), "assign_history.json")
        if os.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as f:
                history_data = json.load(f)
                if isinstance(history_data, dict):
                    assignment_history = defaultdict(int, history_data)

        scored_candidates = []
        for candidate in candidates:
            scored = score_candidate(
                candidate,
                details,
                priority,
                candidate["count"],
                candidate["in_progress"],
                assignment_history,
                scoring_weights,
                developer_skills=developer_skills,
                skill_keywords=skill_keywords,
            )
            scored_candidates.append(scored)

        scored_candidates.sort(key=lambda x: (-x["score"], x["name"]))

        print("Candidates (smart ranking):")
        for i, c in enumerate(scored_candidates, 1):
            rec = " recent" if any("recent" in reason.lower() for reason in c["reasons"]) else ""
            print(f"{i}. {c['name']} | {c['role']} | score: {c['score']} | reasons: {', '.join(c['reasons'])}{rec}")

        top = scored_candidates[0]
        if top.get("in_progress", 0) > 0:
            print(f"Warning: {top['name']} already has {top.get('in_progress', 0)} in-progress task(s).")
        yn = input(f"Assign to {top['name']}? [y/N] ").strip().lower()
        if yn != "y":
            sel = input("Enter candidate number to assign (or blank to skip): ").strip()
            if not sel:
                print("Skipped")
                continue
            try:
                selected = scored_candidates[int(sel) - 1]
                top = selected
            except Exception:
                print("Invalid selection, skipped")
                continue

        if args.dry_run:
            print(f"Dry-run: would assign task {task['id']} to {top['name']}")
            continue

        ok = write(models, db, uid, password, "project.task", [task["id"]], {"user_id": top["id"]})
        if ok:
            assignment_history[top["name"]] = assignment_history.get(top["name"], 0) + 1
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(dict(assignment_history), f, indent=2)
            print(f"Assigned to {top['name']}")
        else:
            print("Assign failed")

if __name__ == "__main__":
    main()
