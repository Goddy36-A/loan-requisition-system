"""One-command Windows setup: python setup_and_run.py [--no-seed] [--reset-db] [--install-expiration]
[--disable-expiration] [--expiration-status] [--test-expiration]"""
import argparse, json, os, subprocess, sys, venv
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VPY = ROOT / ".venv" / "Scripts" / "python.exe"
META = ROOT / ".project_meta.json"
TASK = "LoanRequisitionResearch_Autophagy"

def ps(script):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True)
def q(s): return "'" + str(s).replace("'", "''") + "'"
def run(*a): subprocess.run([str(x) for x in a], cwd=ROOT, check=True)

def install_requirements():
    """Only the very first setup needs internet (to download Django). Once installed, pip finds nothing to download,
    and if the network is down on a later start we simply carry on with what is already installed."""
    try:
        run(VPY, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "requirements.txt")
    except subprocess.CalledProcessError:
        if subprocess.run([str(VPY), "-c", "import django"], cwd=ROOT).returncode == 0:
            print("Could not reach the package index; continuing with the packages that are already installed.")
        else:
            sys.exit("First-time setup needs an internet connection once, to install Django. Connect, then run this again.")

def ensure_meta():
    if not META.exists():  # created once; never reset
        META.write_text(json.dumps({"project": "LoanRequisitionResearch",
            "created_at": datetime.now().isoformat(timespec="seconds"), "retention_days": 30}, indent=4))
    return json.loads(META.read_text())

def task_installed():
    return ps(f"Get-ScheduledTask -TaskName {TASK} -ErrorAction SilentlyContinue | Select -Expand TaskName").stdout.strip() == TASK

def status():
    m = ensure_meta(); c = datetime.fromisoformat(m["created_at"])
    print("=" * 40 + "\nEXPIRATION STATUS\n" + "=" * 40)
    print(f"Project: {ROOT}\nCreated: {c.date()}\nExpires: {(c + timedelta(days=m['retention_days'])).date()}")
    print(f"Retention: {m['retention_days']} days")
    src = (ROOT / "autophagy.py").read_text()
    print("DELETE_PROJECT:", "True" if "DELETE_PROJECT = True" in src else "False")
    print("Windows Task Scheduler:", "INSTALLED" if task_installed() else "NOT INSTALLED", f"\nTask: {TASK}\nSchedule: Daily")

def install_task():
    print("WARNING\n\nYou are enabling unattended project expiration.\nWindows Task Scheduler will periodically execute autophagy.py\n"
          "even when Django is not running.\n\nIf DELETE_PROJECT=True and the retention period has expired,\n"
          "the entire project directory may be permanently deleted.\n\nThis may include:\n- Source code\n- SQLite database\n"
          "- Uploaded documents\n- Logs\n- Virtual environment\n- Documentation\n- Configuration\n")
    if input("Continue? [yes/no] ").strip().lower() != "yes": return print("Cancelled.")
    if not VPY.exists(): sys.exit("Run python setup_and_run.py once first to create .venv")
    script = (f"$a=New-ScheduledTaskAction -Execute {q(VPY)} -Argument {q(chr(34)+str(ROOT/'autophagy.py')+chr(34))} -WorkingDirectory {q(ROOT)};"
              "$t=New-ScheduledTaskTrigger -Daily -At 12:00; $s=New-ScheduledTaskSettingsSet -StartWhenAvailable;"
              f"Register-ScheduledTask -TaskName {TASK} -Action $a -Trigger $t -Settings $s -Force "
              "-Description 'Loan research project expiration check (see README)'")
    r = ps(script); print("Task installed (replaces any existing one)." if r.returncode == 0 else r.stderr)
    print("Note: deletion stays OFF until you set DELETE_PROJECT = True in autophagy.py.")

def disable_task():
    ps(f"Unregister-ScheduledTask -TaskName {TASK} -Confirm:$false -ErrorAction SilentlyContinue")
    print("Autophagy disabled.\nWindows scheduled task removed.\nProject files were not deleted.")

def main():
    ap = argparse.ArgumentParser()
    for f in ("no-seed", "reset-db", "install-expiration", "disable-expiration", "expiration-status", "test-expiration"):
        ap.add_argument("--" + f, action="store_true")
    a = ap.parse_args()
    if os.name != "nt": sys.exit("This project is Windows-only.")
    if sys.version_info < (3, 10): sys.exit("Python 3.10+ required.")
    ensure_meta()
    if a.expiration_status: return status()
    if a.disable_expiration: return disable_task()
    if a.install_expiration: return install_task()
    if a.test_expiration: return run(sys.executable, "autophagy.py", "--test")
    for d in ("logs", "media", "static"): (ROOT / d).mkdir(exist_ok=True)
    if not VPY.exists(): venv.create(ROOT / ".venv", with_pip=True)
    install_requirements()
    if a.reset_db and (ROOT / "db.sqlite3").exists(): (ROOT / "db.sqlite3").unlink()  # metadata untouched
    run(VPY, "manage.py", "migrate")
    if not a.no_seed: run(VPY, "manage.py", "seed_data")
    run(VPY, "manage.py", "check")
    print("\nOpen http://127.0.0.1:8000/  (admin: /admin/)\nSign-in accounts:\n"
          "admin/Admin@12345  customer1/Customer@12345  loanofficer/LoanOfficer@12345\n"
          "creditofficer/CreditOfficer@12345  manager/Manager@12345  researcher/Researcher@12345\n"
          "(Forgot one? python manage.py seed_data --reset-passwords)\n")
    run(VPY, "manage.py", "runserver", "127.0.0.1:8000")

if __name__ == "__main__":
    main()
