from __future__ import annotations

import json
import math
from typing import Any

import numpy as np


def make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, np.generic):
        return make_json_safe(value.item())
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    return value


def json_dumps_safe(value: Any, **kwargs: Any) -> str:
    payload = make_json_safe(value)
    options = {"allow_nan": False, "default": str}
    options.update(kwargs)
    return json.dumps(payload, **options)
