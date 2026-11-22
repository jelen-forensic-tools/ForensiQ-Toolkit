# Test helper: behaves like a long ALEAPP run that also starts a child process
import subprocess
import sys
import time

print("Processing started.", flush=True)
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(60)
