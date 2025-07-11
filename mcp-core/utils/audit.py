import os
import functools
import json
import logging
import inspect

logger = logging.getLogger("audit")
ENABLED = os.getenv("AUDIT_SCHEDULER_DEBUG", "false").lower() == "true"


def audit_step(label):
    def wrapper(fn):
        if not ENABLED:
            return fn

        @functools.wraps(fn)
        def inner(*args, **kw):
            trace_id = kw.get("trace_id")
            if trace_id is None and args:
                trace_id = getattr(args[0], "sid", None)
            arg_names = inspect.getfullargspec(fn).args
            payload = {
                "step": label,
                "trace_id": trace_id,
                "args": {k: v for k, v in zip(arg_names, args)},
                "kwargs": kw,
            }
            logger.debug(json.dumps(payload, default=str))
            out = fn(*args, **kw)
            payload.update({"return": out})
            logger.debug(json.dumps(payload, default=str))
            return out

        return inner

    return wrapper
