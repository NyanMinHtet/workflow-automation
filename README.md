# Assign From Viber (Odoo Task Assigner)

This project provides a CLI script that reads Viber message text, extracts ticket codes like `TSK-XXX-123`, and assigns matching Odoo tasks to developers using load-based heuristics.

## What It Does

- Parses pasted text or a text file for task codes.
- Connects to Odoo over XML-RPC.
- Looks up tasks by `code`.
- Builds candidate assignees from preferred developers (or existing project assignees).
- Shows candidate workload: open tasks in the same project, in-progress task count, and recent activity flag.
- Suggests a top assignee and asks for confirmation.
- Assigns the task in Odoo (or prints intended changes in dry-run mode).

## Project Structure

- `scripts/assign_from_viber.py`: Main CLI script.
- `config/assign_from_viber.json`: Assignment and stage mapping configuration.
- `.env`: Odoo connection credentials (ignored by git).

## Requirements

- Python 3.8+ (stdlib only; no external package install required)
- Odoo instance with XML-RPC enabled and access to `project.task`, `project.task.type`, and `res.users`

## Environment Variables

Create a `.env` file in the repository root:

```env
ODOO_URL="https://your-odoo-host"
ODOO_DB="your_database"
ODOO_USER="your_user"
ODOO_PASSWORD="your_password"
```

You can also pass a custom env file path with `--dotenv`.

## Configuration

Default config path: `config/assign_from_viber.json`

Key fields:

- `open_stage_names`: Stage names considered "open" when counting workload.
- `todo_stage_names`: Present in config, currently not used by the script logic.
- `in_progress_stage_names`: Stage names used to count active in-progress tasks.
- `recent_days`: Number of days for recent-activity marker.
- `preferred_developers`: Mapping by project name, project ID (as string), or `default` to ordered preferred assignee identifiers (name/login/email).
- `developer_roles`: Optional display label for each developer in candidate output.

## Usage

### 1) Dry run from a text file

```bash
python3 scripts/assign_from_viber.py --input /path/to/messages.txt --dry-run
```

### 2) Real assignment from a text file

```bash
python3 scripts/assign_from_viber.py --input /path/to/messages.txt
```

### 3) Paste messages directly

```bash
python3 scripts/assign_from_viber.py
```

Then paste Viber messages and press `Ctrl-D`.

### 4) Use custom config or env file

```bash
python3 scripts/assign_from_viber.py \
  --config config/assign_from_viber.json \
  --dotenv .env
```

## Input Format

The script extracts codes matching:

```text
TSK-[A-Z0-9]+-[0-9]+
```

Example recognized values:

- `TSK-ERP-1024`
- `TSK-AB12-7`

## Behavior Notes

- If multiple tasks share the same code, the script prompts you to choose one.
- If no preferred developers resolve for a project, it falls back to current assignees with open tasks in that project.
- Candidate list is sorted by **most open project tasks first**.
- Before writing, it asks for confirmation of the suggested assignee.
- HTML task descriptions are converted to plain text for terminal display.

## Safety / Best Practice

- Run with `--dry-run` first, especially after changing configuration.
- Keep `.env` private; never commit real credentials.
- Use project-specific `preferred_developers` keys (project name or project ID string) when needed.

## Troubleshooting

- `Missing ODOO_URL...`: Ensure `.env` exists (or pass `--dotenv`) and all required keys are set.
- `Login failed`: Verify URL, DB, username, password, and user access rights.
- `No ticket codes found`: Confirm message text includes `TSK-...-number` pattern.
- Myanmar text appears broken: Use UTF-8 locale, e.g. `export LANG=en_US.UTF-8`.
