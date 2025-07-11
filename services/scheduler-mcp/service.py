from datetime import datetime, time
from typing import Iterable, Optional, Dict
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'mcp-core'))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from utils.audit import audit_step
except ImportError:
    from importlib import import_module
    audit_step = import_module('mcp_utils.audit').audit_step


@audit_step("select_exact_block")
def select_exact_block(bloques: Iterable[Dict], hora_user: time, trace_id=None) -> Optional[Dict]:
    for b in bloques:
        start_str, end_str = b["hora_rango"].split("-")
        start_dt = datetime.strptime(start_str, "%H:%M").time()
        end_dt = datetime.strptime(end_str, "%H:%M").time()
        if start_dt <= hora_user < end_dt:
            return b
    return None
