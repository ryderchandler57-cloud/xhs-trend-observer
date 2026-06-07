# GitHub Publish Cleanup Report

Generated at: 2026-06-07

## Summary

This project was cleaned for GitHub publishing. Runtime secrets, browser login state, databases, collection outputs, media caches, and Python caches were removed from the project directory or moved outside the repository.

## Sensitive Files Moved

- `.env` was moved out of the project directory to an external user-local backup location.
- `.xhs-profile-9222/` was moved out of the project directory.
- `.xhs-profile-9223/` was moved out of the project directory.
- `.xhs-profile-9224/` was moved out of the project directory.
- `.xhs-profile-9225/` was moved out of the project directory.
- `.xhs-profile-9226/` was moved out of the project directory.

Note: the originally requested external path under `C:\Users\` was not writable in this environment, so the backup was stored under the current user's home directory instead.

## Runtime Data Removed

- `outputs/`
- `data/`
- `scripts/__pycache__/`
- `scripts/parsers/__pycache__/`
- `storage/__pycache__/`
- `work/empty.env`

The following runtime patterns are also ignored and should not be committed:

- `media/`
- `media_ocr_tmp/`
- `logs/`
- `.cache/`
- `cookies/`
- `sessions/`
- `.venv/`
- `.venv-ocr/`
- `node_modules/`
- `vendor/`
- `*.sqlite`
- `*.sqlite-journal`
- `*.cookie`
- `*.har`
- `*.log`
- `*.pyc`

## `.gitignore` Rules Added Or Confirmed

- Secrets: `.env`, `.env.*`, with `!.env.example`
- Browser profile and login state: `.xhs-profile*/`, `cookies/`, `sessions/`, `*.cookie`, `*.har`
- Runtime outputs: `outputs/`, `data/`, SQLite files
- Media and OCR cache: `media/`, `media_ocr_tmp/`
- Python cache and envs: `__pycache__/`, `*.pyc`, `.venv/`, `.venv-ocr/`
- Node/vendor/cache: `node_modules/`, `vendor/`, `.cache/`
- Logs: `logs/`, `*.log`
- OS/editor files: `.DS_Store`, `Thumbs.db`, `.vscode/`, `.idea/`

## Post-Cleanup Checks

- Project `.env` exists: no
- Project `.xhs-profile-*` exists: no
- Project SQLite files exist: no
- Project `outputs/` exists: no
- Project `data/` exists: no
- Real API key pattern found in scanned source/docs: no
- Real cookie/session/token pattern found in scanned source/docs: no

## Scan Notes

Filename scan still reports source-code false positives such as keyword/discovery scripts and cookie-session source files. These are code filenames, not secret material.

Content scan still reports placeholder and source/doc references such as `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `cookie`, and `token`. These are instructions or implementation references; no real key, cookie, token, session value, or XHS credential was found.

## GitHub Desktop Recommendation

Use **Keep this code private** when publishing from GitHub Desktop. The repository contains tooling for logged-in browser workflows, so a private backup is the safer default even after cleanup.
