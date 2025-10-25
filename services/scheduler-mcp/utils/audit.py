import os
import functools
import json
import logging
import inspect

logger = logging.getLogger("audit")


def audit_step(label):
    def wrapper(fn):
        @functools.wraps(fn)
        def inner(*args, **kw):
            if os.getenv("AUDIT_SCHEDULER_DEBUG", "false").lower() != "true":
                return fn(*args, **kw)

            trace_id = kw.get("trace_id")
            arg_names = inspect.getfullargspec(fn).args
            if trace_id is None and "sid" in arg_names:
                idx = arg_names.index("sid")
                if idx < len(args):
                    trace_id = args[idx]
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
