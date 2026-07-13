# Workflow Automation Conversation Summary

## Overview
This project evolved from a simple CLI-based Odoo task assignment script into a smarter, configurable assignment workflow and then into a local, tablet-friendly dashboard for quick review and assignment support.

## Main Goals
- Turn the assignment flow into a smarter recommendation system without relying on AI APIs.
- Make the system usable from an iPad-style dashboard.
- Fix runtime issues in the assignment script and ensure preferred developers remain visible in the candidate list.

## Core Components
- Python assignment script: [scripts/assign_from_viber.py](scripts/assign_from_viber.py)
- Configuration: [config/assign_from_viber.json](config/assign_from_viber.json)
- Regression tests: [tests/test_smart_assign.py](tests/test_smart_assign.py)
- Dashboard UI: [dashboard/index.html](dashboard/index.html)
- Dashboard server: [dashboard/server.py](dashboard/server.py)
- Dashboard data builder: [dashboard/data_builder.py](dashboard/data_builder.py)

## What Was Implemented
- Added a rule-based scoring engine for candidate ranking.
- Supported configurable weights for skill match, workload, fairness, urgency, and in-progress penalties.
- Improved preferred-developer resolution so users like Htet Aung Win and Yin Kyae Wai are not excluded incorrectly.
- Added a local web dashboard with a touch-friendly layout.
- Connected the dashboard to live Odoo-backed data through the same assignment logic.
- Added SSL-safe XML-RPC handling so the script and dashboard can work with self-signed Odoo HTTPS certificates.

## Verification
- Regression tests were run successfully:
  - `python3 -m unittest discover -s tests -p 'test_*.py'`
  - Result: 5 tests passed.
- Dashboard API was verified:
  - `curl -s http://127.0.0.1:8000/api/dashboard`
  - Result: returned a live JSON payload with ticket and developer recommendations.

## Current Status
The workflow automation system now includes:
- a smarter assignment engine,
- a configurable recommendation model,
- a local dashboard for review on a tablet,
- and a working connection to Odoo data for real assignment recommendations.
