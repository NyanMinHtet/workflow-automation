import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "assign_from_viber.py"
DASHBOARD_MODULE_PATH = REPO_ROOT / "dashboard" / "data_builder.py"

spec = importlib.util.spec_from_file_location("assign_from_viber", MODULE_PATH)
assign_from_viber = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assign_from_viber)

spec = importlib.util.spec_from_file_location("dashboard_data_builder", DASHBOARD_MODULE_PATH)
dashboard_data_builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard_data_builder)


class SmartAssignTests(unittest.TestCase):
    def test_skill_match_outweighs_basic_workload(self):
        weights = {
            "skill_match": 3.0,
            "workload": 1.0,
            "fairness": 1.0,
            "urgency": 0.5,
            "in_progress_penalty": 1.0,
        }
        developer_skills = {
            "Alice": ["reporting", "api"],
            "Bob": ["workflow"],
        }
        skill_keywords = {
            "reporting": ["report", "excel", "xlsx", "pivot"],
            "workflow": ["workflow", "approval"],
        }
        candidate = {
            "id": 1,
            "name": "Alice",
            "role": "senior odoo developer",
        }
        other = {
            "id": 2,
            "name": "Bob",
            "role": "mid odoo developer",
        }

        alice_score = assign_from_viber.score_candidate(
            candidate,
            "Need a report export for invoices",
            priority="2",
            open_task_count=4,
            in_progress_count=1,
            assignment_history={},
            weights=weights,
            developer_skills=developer_skills,
            skill_keywords=skill_keywords,
        )
        bob_score = assign_from_viber.score_candidate(
            other,
            "Need a report export for invoices",
            priority="2",
            open_task_count=0,
            in_progress_count=0,
            assignment_history={},
            weights=weights,
            developer_skills=developer_skills,
            skill_keywords=skill_keywords,
        )

        self.assertGreater(alice_score["score"], bob_score["score"])

    def test_high_priority_favors_senior_dev(self):
        weights = {
            "skill_match": 2.0,
            "workload": 1.0,
            "fairness": 0.5,
            "urgency": 1.0,
            "in_progress_penalty": 1.0,
        }
        developer_skills = {
            "Alice": ["workflow"],
            "Bob": ["workflow"],
        }
        skill_keywords = {
            "workflow": ["workflow", "approval"],
        }
        senior = {"id": 1, "name": "Alice", "role": "senior odoo developer"}
        mid = {"id": 2, "name": "Bob", "role": "mid odoo developer"}

        senior_score = assign_from_viber.score_candidate(
            senior,
            "Urgent approval workflow change",
            priority="3",
            open_task_count=2,
            in_progress_count=0,
            assignment_history={},
            weights=weights,
            developer_skills=developer_skills,
            skill_keywords=skill_keywords,
        )
        mid_score = assign_from_viber.score_candidate(
            mid,
            "Urgent approval workflow change",
            priority="3",
            open_task_count=0,
            in_progress_count=0,
            assignment_history={},
            weights=weights,
            developer_skills=developer_skills,
            skill_keywords=skill_keywords,
        )

        self.assertGreater(senior_score["score"], mid_score["score"])

    def test_scored_candidate_keeps_workload_metadata(self):
        candidate = {
            "id": 7,
            "name": "Alice",
            "role": "senior odoo developer",
            "count": 2,
            "in_progress": 3,
            "recent": True,
        }
        scored = assign_from_viber.score_candidate(
            candidate,
            "Need a report export",
            priority="2",
            open_task_count=2,
            in_progress_count=3,
            assignment_history={},
            weights={"skill_match": 3.0, "workload": 1.0, "fairness": 0.5, "urgency": 1.0, "in_progress_penalty": 1.0},
            developer_skills={"Alice": ["reporting"]},
            skill_keywords={"reporting": ["report", "excel"]},
        )

        self.assertEqual(scored["count"], 2)
        self.assertEqual(scored["in_progress"], 3)
        self.assertTrue(scored["recent"])

    def test_resolve_users_matches_case_insensitive_names(self):
        class DummyModels:
            def execute_kw(self, db, uid, password, model, method, args, kwargs):
                domain = args[0]
                if model == "res.users" and method == "search_read":
                    if domain == ["|", "|", ("login", "=", "Htet Aung WIN"), ("email", "=", "Htet Aung WIN"), ("name", "=", "Htet Aung WIN")]:
                        return []
                    if domain == ["|", "|", ("login", "ilike", "Htet Aung WIN"), ("email", "ilike", "Htet Aung WIN"), ("name", "ilike", "Htet Aung WIN")]:
                        return [{"id": 99, "name": "Htet Aung Win", "login": "htet", "email": "htet@example.com"}]
                return []

        resolved = assign_from_viber.resolve_users(DummyModels(), "db", 1, "pw", ["Htet Aung WIN"])
        self.assertEqual(resolved["Htet Aung WIN"]["name"], "Htet Aung Win")

    def test_https_login_configures_unverified_context(self):
        class DummyCommon:
            def authenticate(self, db, user, password, context):
                return 1

        def fake_server_proxy(url, **kwargs):
            self.assertTrue(url.startswith("https://odoo.local/xmlrpc/2/"))
            transport = kwargs["transport"]
            self.assertFalse(transport._context.check_hostname)
            self.assertEqual(transport._context.verify_mode, 0)
            return DummyCommon()

        with patch.object(assign_from_viber.xmlrpc.client, "ServerProxy", side_effect=fake_server_proxy):
            assign_from_viber.xmlrpc_login("https://odoo.local", "db", "user", "pw")

    def test_dashboard_ticket_codes_are_loaded_live_from_odoo(self):
        class DummyModels:
            def execute_kw(self, db, uid, password, model, method, args, kwargs):
                if model == "project.task" and method == "search_read":
                    return [{"code": "TSK-LIVE-001"}]
                return []

        with patch.object(dashboard_data_builder.assign_from_viber, "resolve_stage_ids", return_value=[7, 8]):
            codes = dashboard_data_builder.get_dashboard_ticket_codes(
                {"open_stage_names": ["New"], "dashboard_ticket_codes": ["TSK-STATIC-001"]},
                DummyModels(),
                "db",
                1,
                "pw",
            )

        self.assertEqual(codes, ["TSK-LIVE-001"])


if __name__ == "__main__":
    unittest.main()
