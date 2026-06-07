# Desktop Copy Notes

Source project:
C:\Users\dell\Documents\Codex\2026-06-02\goal-xhs-to-web-research-1

Desktop copy:
C:\Users\dell\Desktop\xhs-trend-observer

Copied:
- trend_xhs_observer.cmd
- trend_xhs_observer.ps1
- requirements.txt
- README.md
- .env
- .env.example
- project_inventory.md
- scripts
- work
- outputs

Not copied:
- .xhs-profile*

Reason:
Browser profile folders are large cache/login-session folders and may be locked by Edge. The runner will recreate a desktop-local profile such as .xhs-profile-9222 when launched from this folder. The .env file was copied so the existing cookie/session config is preserved.

Run from here:
cd "C:\Users\dell\Desktop\xhs-trend-observer"
.\trend_xhs_observer.cmd -Doctor
