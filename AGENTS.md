# 사내 업로드 Codex Instructions

## Scope

This file applies to the `사내업로드` repository.

Keep this `AGENTS.md` tracked in Git. It is part of the source handoff so the
same project rules follow GitHub clones, MacBook work, and future Windows
workstations.

## Project Summary

This repository is a small internal incident-response file upload tool. It runs
as a Python Flask web app on a Windows PC, stores uploaded files under a
configured storage root, records upload metadata in CSV, and generates direct
browser download links.

This is not a general-purpose file transfer service. Keep the scope small.

## Default Workflow

- Inspect `git status --short --branch` before editing or committing.
- Keep changes scoped to this repository.
- Prefer simple CSV and file-system behavior over database-backed features.
- Do not add login, role management, recipient selection, expiry, or admin pages
  unless the user explicitly changes the project scope.
- Keep generated upload files, virtual environments, caches, logs, and private
  operating data out of Git.
- Before any GitHub push or Release work, check whether `README.md`,
  `RELEASE_NOTES.md`, and `CHANGELOG.md` still match the current behavior.
- When a version is ready for sharing, create or update the matching Git tag,
  GitHub Release, and Windows ZIP asset. Keep the Release body aligned with
  `RELEASE_NOTES.md` and `CHANGELOG.md`.

## Important Areas

- `app.py`: Flask routes, config loading, upload/download/delete logic, and CSV
  handling.
- `templates/` and `static/`: the single-page upload UI and network check mode.
- `tests/`: deterministic tests for upload, download, deletion, paths, links,
  and CSV behavior.
- `config.ini`: sample/default operational settings. Do not store real secrets.
- `data/upload_log.csv`: tracked initial upload CSV header only; operational
  records should not be treated as source history.
- `data/network_check_log.csv`: tracked initial network-check CSV header only;
  operational speed-test records should not be treated as source history.
- `tools/`: Windows Release ZIP build and verification helpers.
- `.github/workflows/release.yml`: Windows runner workflow that builds and
  uploads the executable ZIP asset.

## Validation Commands

Use the narrowest relevant check while developing, then run the full baseline
before calling work complete.

```powershell
python -m compileall app.py tests
python -m pytest -q
```

On macOS in this workspace, use:

```bash
.venv/bin/python -m compileall app.py tests tools
.venv/bin/python -m pytest -q
```

## README / Release Document Rules

- If features are added, changed, or removed, update `README.md` in the same
  change when the user-facing behavior changes.
- If install, run, setup, port, config keys, storage layout, CSV fields,
  deletion rules, download-link behavior, firewall notes, or limitations change,
  update `README.md` in the same change.
- If a change is release-facing, check `README.md`, `RELEASE_NOTES.md`, and
  `CHANGELOG.md` together.
- Update `CHANGELOG.md` with user-facing changes before pushing to GitHub.
- Keep `RELEASE_NOTES.md` aligned with the current release checklist and any
  future GitHub Release asset contract.
- Record the same user-facing behavior, validation commands, limitations, ZIP
  asset name, and SHA256 policy in `RELEASE_NOTES.md`.
- Do not document features that are not implemented. If a feature is planned but
  not implemented, label it as not implemented.
- Write README steps for users who are not comfortable with GitHub or
  development tooling yet. Prefer numbered, copyable steps over assumed
  background knowledge.
- Use sample values only. Never place real internal IPs, host names, accounts,
  passwords, customer data, uploaded files, private notes, or raw operational
  logs in README, release notes, changelog entries, commits, or final reports.

## Safety Rules

- Keep the app limited to internal trusted-network use.
- Treat upload data and memo text as operational data.
- Do not commit uploaded files or populated CSV records.
- Keep deletion behavior restricted by configured allowed IPs unless the user
  explicitly changes that policy.
