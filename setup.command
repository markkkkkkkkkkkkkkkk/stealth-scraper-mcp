#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ "${1:-}" != "--logged" ]; then
    /bin/bash "$0" --logged 2>&1 | tee setup.log
    exit 0
fi
trap 'status=$?; if [ "$status" -ne 0 ]; then echo "Setup stopped. See setup.log in the project folder."; fi' EXIT

if [ ! -x venv/bin/python ]; then
    if command -v python3.13 >/dev/null 2>&1; then
        python3.13 -m venv venv
    else
        echo "Python 3.13 is required to recreate this project's environment."
        exit 1
    fi
fi

echo "Checking Python and access to PyPI..."
venv/bin/python -u - <<'PY'
import platform
import sys
import urllib.error
import urllib.request

print('Python:', sys.version.split()[0], 'Architecture:', platform.machine())
for url in ('https://pypi.org/simple/fastmcp/', 'https://files.pythonhosted.org/'):
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            print(url, 'reachable')
    except urllib.error.HTTPError:
        # An HTTP response still establishes DNS, TLS and server connectivity.
        print(url, 'reachable')
    except Exception as exc:
        print('Network check failed:', url, exc)
        print('Run this file from macOS Terminal outside the restricted agent session.')
        print('If it fails there too, check your connection, VPN or proxy before retrying.')
        sys.exit(1)
PY

venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install --upgrade --prefer-binary --find-links offline-wheels -r requirements.txt
venv/bin/python -m playwright install chromium
venv/bin/python -m pip check
venv/bin/python -u verify_setup.py
venv/bin/python -m pip freeze > requirements-installed.txt
echo "Setup and browser verification completed successfully."
