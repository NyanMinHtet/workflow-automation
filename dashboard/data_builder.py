#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / 'scripts'))

import assign_from_viber


def load_dashboard_payload():
    config_path = REPO_ROOT / 'config' / 'assign_from_viber.json'
    cfg = assign_from_viber.load_config(str(config_path))
    assign_from_viber.load_dotenv(str(REPO_ROOT / '.env'))

    url = os.getenv('ODOO_URL')
    db = os.getenv('ODOO_DB')
    user = os.getenv('ODOO_USER')
    password = os.getenv('ODOO_PASSWORD')
    if not all([url, db, user, password]):
        return {'status': 'missing-env', 'tickets': [], 'developers': []}

    try:
        uid, models = assign_from_viber.xmlrpc_login(url, db, user, password)
    except Exception as exc:
        return {'status': f'login-error: {exc}', 'tickets': [], 'developers': []}

    open_stage_names = cfg.get('open_stage_names', [])
    in_progress_stage_names = cfg.get('in_progress_stage_names', ['In Progress', 'IN PROGRESS'])
    preferred_map = cfg.get('preferred_developers', {})
    role_map = cfg.get('developer_roles', {})
    recent_days = int(cfg.get('recent_days', 7))

    tickets = []
    developers = []

    for code in ['TSK-BMKC-231', 'TSK-BMKC-237']:
        tasks = assign_from_viber.search_read(
            models, db, uid, password,
            'project.task',
            [('code', '=', code)],
            ['id', 'name', 'code', 'project_id', 'user_id', 'priority', 'stage_id', 'write_date', 'description'],
        )
        if not tasks:
            continue
        task = tasks[0]
        project = task.get('project_id')
        if not project:
            continue
        project_id, project_name = project
        priority = task.get('priority', '0')
        details = assign_from_viber.html_to_text(task.get('description')) or '(No description on task)'

        stage_id_list = assign_from_viber.resolve_stage_ids(
            models, db, uid, password,
            stage_names=open_stage_names,
            fallback_domain=[('fold', '=', False)],
        )
        if not stage_id_list:
            continue

        domain = [('project_id', '=', project_id), ('stage_id', 'in', stage_id_list), ('user_id', '!=', False)]
        open_tasks = assign_from_viber.search_read(models, db, uid, password, 'project.task', domain, ['id', 'user_id', 'write_date'])
        counts = {}
        recent = {}
        cutoff = assign_from_viber.datetime.now(assign_from_viber.timezone.utc) - assign_from_viber.timedelta(days=recent_days)
        for task_item in open_tasks:
            uid_pair = task_item.get('user_id')
            if not uid_pair:
                continue
            uid_val, _ = uid_pair
            counts[uid_val] = counts.get(uid_val, 0) + 1
            wdate = task_item.get('write_date')
            if wdate:
                try:
                    dt = assign_from_viber.datetime.strptime(wdate, '%Y-%m-%d %H:%M:%S')
                    if dt >= cutoff:
                        recent[uid_val] = True
                except Exception:
                    pass

        pref_key = assign_from_viber.pick_project_key(preferred_map, project_id, project_name)
        preferred_ids = None
        if pref_key:
            resolved = assign_from_viber.resolve_users(models, db, uid, password, preferred_map[pref_key])
            preferred_ids = [u['id'] for u in resolved.values() if u]

        candidate_ids = preferred_ids if preferred_ids else list(counts.keys())
        if preferred_ids:
            candidate_ids = list(dict.fromkeys(preferred_ids + list(counts.keys())))

        in_progress_stage_ids = assign_from_viber.resolve_stage_ids(
            models, db, uid, password,
            stage_names=in_progress_stage_names,
            fallback_domain=[('name', 'ilike', 'progress')],
        )
        in_progress_counts = {}
        if in_progress_stage_ids:
            in_progress_tasks = assign_from_viber.search_read(
                models, db, uid, password,
                'project.task',
                [('stage_id', 'in', in_progress_stage_ids), ('user_id', 'in', candidate_ids)],
                ['id', 'user_id'],
            )
            for t in in_progress_tasks:
                uid_pair = t.get('user_id')
                if not uid_pair:
                    continue
                uid_val = uid_pair[0]
                in_progress_counts[uid_val] = in_progress_counts.get(uid_val, 0) + 1

        cand_records = assign_from_viber.read(models, db, uid, password, 'res.users', candidate_ids, ['id', 'name', 'login', 'email'])
        candidates = []
        for u in cand_records:
            uid_val = u['id']
            candidates.append({
                'id': uid_val,
                'name': u['name'],
                'role': assign_from_viber.resolve_role(role_map, u),
                'open_tasks': counts.get(uid_val, 0),
                'in_progress_tasks': in_progress_counts.get(uid_val, 0),
                'recent_assignments': 1 if recent.get(uid_val, False) else 0,
            })

        developer_skills = cfg.get('developer_skills', {})
        skill_keywords = cfg.get('skill_keywords', {})
        scoring_weights = cfg.get('scoring_weights', {
            'skill_match': 3.0,
            'workload': 1.0,
            'fairness': 0.8,
            'urgency': 1.0,
            'in_progress_penalty': 1.0,
        })
        assignment_history = {}
        history_path = REPO_ROOT / 'config' / 'assign_history.json'
        if history_path.exists():
            with open(history_path, 'r', encoding='utf-8') as handle:
                history_data = json.load(handle)
                if isinstance(history_data, dict):
                    assignment_history = history_data

        scored_candidates = []
        for candidate in candidates:
            scored = assign_from_viber.score_candidate(
                candidate,
                details,
                priority,
                candidate['open_tasks'],
                candidate['in_progress_tasks'],
                assignment_history,
                scoring_weights,
                developer_skills=developer_skills,
                skill_keywords=skill_keywords,
            )
            scored_candidates.append(scored)

        scored_candidates.sort(key=lambda item: (-item['score'], item['name']))
        top_candidate = scored_candidates[0] if scored_candidates else None

        tickets.append({
            'code': code,
            'title': task.get('name'),
            'project': project_name,
            'priority': priority,
            'top_recommendation': top_candidate['name'] if top_candidate else '-',
            'top_score': str(top_candidate['score']) if top_candidate else '-',
            'is_urgent': int(str(priority or '0').strip() or '0') >= 3,
        })
        developers.extend(candidates)

    unique_developers = {}
    for dev in developers:
        key = dev['name']
        existing = unique_developers.get(key)
        if existing is None:
            unique_developers[key] = dev
            continue
        existing['open_tasks'] += dev['open_tasks']
        existing['in_progress_tasks'] += dev['in_progress_tasks']
        existing['recent_assignments'] += dev['recent_assignments']

    developer_list = list(unique_developers.values())
    developer_list.sort(key=lambda item: item['name'])
    return {'status': 'ready', 'tickets': tickets, 'developers': developer_list}
