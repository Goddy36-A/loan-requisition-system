"""Optional, transparent project-expiration check. Disabled by default.
Run by the user-installed Windows Task Scheduler task 'LoanRequisitionResearch_Autophagy'."""
import json, os, shutil, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

DELETE_PROJECT = False   # must be set True deliberately
RETENTION_DAYS = 30
TASK = "LoanRequisitionResearch_Autophagy"
ROOT = Path(__file__).resolve().parent
META = ROOT / ".project_meta.json"
REQUIRED = ["autophagy.py", "manage.py", "requirements.txt", "config/settings.py", "core/models.py"]

def log(msg):
    print(msg)
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "application.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} autophagy: {msg.strip()}\n")
    except OSError:
        pass

def problems(root: Path):
    root, p = root.resolve(), []
    if root.parent == root: p.append("target is a filesystem root")
    if not root.is_dir(): p.append("target is not a directory")
    protected = set()
    for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(k): protected.add(Path(os.environ[k]).resolve())
    drive = os.environ.get("SystemDrive")
    if drive: protected.add(Path(drive + "\\").resolve())
    home = Path.home().resolve(); protected |= {home, home.parent}
    for d in protected:
        if root == d or root in d.parents: p.append(f"target is or contains protected path {d}")
    for f in REQUIRED:
        if not (root / f).exists(): p.append(f"missing expected file {f}")
    try:
        if json.loads(META.read_text()).get("project") != "LoanRequisitionResearch":
            p.append("project marker mismatch in .project_meta.json")
    except Exception:
        p.append("cannot read .project_meta.json")
    return p

def main():
    test = "--test" in sys.argv
    meta = json.loads(META.read_text())
    created = datetime.fromisoformat(meta["created_at"])
    expires = created + timedelta(days=meta.get("retention_days", RETENTION_DAYS))
    expired = datetime.now() >= expires or test
    log("=" * 40 + "\nLOAN RESEARCH PROJECT EXPIRATION CHECK\n" + "=" * 40)
    log(f"Project: {ROOT}\nCreated: {created.date()}\nExpires: {expires.date()}\nDELETE_PROJECT: {DELETE_PROJECT}")
    if not expired:
        return log("Status: NOT EXPIRED. No action taken.")
    if test:
        bad = problems(ROOT)
        log("TEST MODE: simulating expiry. Nothing will be deleted.")
        log("Validation: " + ("OK" if not bad else "; ".join(bad)))
        log("Would delete: " + (str(ROOT) if DELETE_PROJECT and not bad else "nothing (disabled or invalid)"))
        return
    if not DELETE_PROJECT:
        return log("Project has expired. DELETE_PROJECT=False. No deletion performed.")
    bad = problems(ROOT)
    if bad:
        return log("REFUSING to delete: " + "; ".join(bad))
    if sys.stdin and sys.stdin.isatty():
        if input(f"Permanently delete {ROOT}? Type YES: ").strip() != "YES":
            return log("Cancelled by user.")
    log("Project expiration reached. Beginning validated cleanup.")
    failed = []
    shutil.rmtree(ROOT, onerror=lambda fn, path, exc: failed.append(path))
    # remove the scheduled task this project installed (it would only fail afterwards)
    subprocess.run(["powershell", "-NoProfile", "-Command",
        f"Unregister-ScheduledTask -TaskName {TASK} -Confirm:$false -ErrorAction SilentlyContinue"], capture_output=True)
    print(f"Cleanup finished. {len(failed)} item(s) could not be removed (e.g. locked .venv python.exe); delete manually.")

if __name__ == "__main__":
    main()
