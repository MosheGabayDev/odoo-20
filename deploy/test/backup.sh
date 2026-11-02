#!/bin/bash
# Always follows the current release; the controller owns all three locks.
set -euo pipefail
exec python3 /opt/odoo20-test/release.py backup
