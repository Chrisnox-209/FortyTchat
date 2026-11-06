#!/usr/bin/env python3
"""Read-only local diagnostics; never scans or changes firewall rules."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.networking.diagnostics import probe_ports
print(json.dumps(probe_ports(), ensure_ascii=False, indent=2))
