#!/bin/bash
# Run with: /bin/bash /Users/jm/stealth-scraper-mcp/setup-python311.command
set -euo pipefail

project=/Users/jm/stealth-scraper-mcp
cd -- "$project"
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/mcp-python311.XXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT

python311=''
for candidate in \
    /usr/local/opt/python@3.11/bin/python3.11 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11; do
    if [ -x "$candidate" ] && "$candidate" -c \
        'import ssl, venv, sys; assert sys.version_info[:2] == (3, 11)' 2>/dev/null; then
        python311="$candidate"
        break
    fi
done

if [ -z "$python311" ]; then
    # 3.11.9 is the final python.org 3.11 release with a macOS binary installer.
    installer_pkg="$work_dir/python-3.11.9-macos11.pkg"
    echo 'Downloading the official Python 3.11.9 universal2 installer...'
    /usr/bin/curl --fail --location --show-error --retry 2 \
        --connect-timeout 15 --max-time 600 \
        --output "$installer_pkg" \
        https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg
    /usr/sbin/pkgutil --check-signature "$installer_pkg"
    echo 'Installing Python. macOS Terminal may ask for your administrator password.'
    /usr/bin/sudo /usr/sbin/installer -pkg "$installer_pkg" -target /
    python311=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
fi

"$python311" -c \
    'import platform, ssl, sys, venv; assert sys.version_info[:2] == (3, 11); print(sys.executable, sys.version.split()[0], platform.machine())'

# Resolve and download every wheel BEFORE deleting the existing environment.
# No source archives are permitted, including transitive dependencies.
"$python311" -m venv "$work_dir/preflight"
"$work_dir/preflight/bin/python" -m pip install \
    --only-binary=:all: --upgrade pip
mkdir -p "$work_dir/wheels"
if ! "$work_dir/preflight/bin/python" -m pip download \
    --only-binary=:all: --dest "$work_dir/wheels" -r "$project/requirements.txt"; then
    echo 'Wheel download failed. The existing venv has been preserved.' >&2
    echo 'Read the pip error above for the unavailable wheel or network failure.' >&2
    exit 1
fi

# The deletion target is intentionally a fixed, absolute project path.
if [ -L "$project/venv" ]; then
    echo 'Refusing to replace venv because it is a symbolic link.' >&2
    exit 1
fi
rm -rf -- /Users/jm/stealth-scraper-mcp/venv
"$python311" -m venv "$project/venv"
source "$project/venv/bin/activate"
python -m pip install --no-index --find-links "$work_dir/wheels" \
    --only-binary=:all: -r "$project/requirements.txt"
python -m pip check
python -m playwright install chromium
python -u "$project/verify_setup.py"
python -m pip freeze > "$project/requirements-installed.txt"
echo 'Python 3.11 setup and browser verification passed.'
echo 'To activate in your current terminal: source /Users/jm/stealth-scraper-mcp/venv/bin/activate'
