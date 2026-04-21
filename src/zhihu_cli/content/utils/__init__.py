import time
import uuid


def generate_trace_context() -> tuple[int, str]:
    return int(time.time() * 1000), str(uuid.uuid4())
