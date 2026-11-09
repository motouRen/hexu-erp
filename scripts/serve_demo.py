"""Serve only the isolated fictional demo, with one business worker and loopback binding."""
import argparse
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8767)
parser.add_argument("--https-proxy", action="store_true", help="Dedicated loopback listener behind an HTTPS-only tunnel")
parser.add_argument("--hostnames", default="127.0.0.1,localhost")
options = parser.parse_args()
if not 1024 <= options.port <= 65535:
    parser.error("port must be between 1024 and 65535")
root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
os.environ["ERP_DEMO_MODE"] = "1"
os.environ["ERP_DEBUG"] = "0" if options.https_proxy else "1"
os.environ["ERP_TRUST_PROXY"] = "0"
os.environ["ERP_ALLOWED_HOSTS"] = options.hostnames
from config.wsgi import application
from waitress import serve

serve(application, host="127.0.0.1", port=options.port, threads=1,
      url_scheme="https" if options.https_proxy else "http", max_request_body_size=2*1024*1024)
