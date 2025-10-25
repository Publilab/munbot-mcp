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
    try:
        from mcp_utils.audit import audit_step
    except Exception:  # pragma: no cover
        def audit_step(_label):
            def _noop(fn):
                return fn
            return _noop


@audit_step("select_exact_block")
def select_exact_block(bloques: Iterable[Dict], hora_user: time, trace_id=None) -> Optional[Dict]:
    # La selección exacta ya la hace SQL
    return next(iter(bloques), None)
