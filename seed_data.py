"""Run `python manage.py seed_data` (this file exists for discoverability)."""
import subprocess, sys
subprocess.run([sys.executable, "manage.py", "seed_data"], check=True)
