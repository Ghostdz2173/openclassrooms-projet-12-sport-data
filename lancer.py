"""Start local reporting and monitoring together."""
import subprocess
import sys

from pipeline import ROOT,RUNTIME


if __name__=="__main__":
    if not (RUNTIME/"sports.sqlite").exists():
        subprocess.run([sys.executable,"pipeline.py","demo"],cwd=ROOT,check=True)
    worker=subprocess.Popen([sys.executable,"pipeline.py","watch"],cwd=ROOT)
    try:
        raise SystemExit(subprocess.call([sys.executable,"-m","streamlit","run","app.py",
            "--server.address","127.0.0.1","--server.port","8512",
            "--browser.gatherUsageStats","false"],cwd=ROOT))
    finally:
        worker.terminate()
        worker.wait(timeout=10)
