"""Unified sleep utility.

All delay/sleep calls in the project should go through this module so that
throttling behaviour can be adjusted in one place.
"""

import math
import random
import time


def generate_lognormal(mean_value: float, sigma=1.0):
    """Generate a random number from a log-normal distribution with a specified mean (k) and standard deviation (sigma)."""
    mu = math.log(mean_value) - (sigma**2) / 2
    return random.lognormvariate(mu, sigma)


def wait(sleep_factor: float, forced_to_wait: bool = False) -> None:
    """Sleep for ``sleep_factor`` seconds.

    Args:
        sleep_factor: Multiplied by 1.0 to get the actual delay in seconds.
        forced_to_wait: If True, force the wait regardless of throttling
            overrides.
    """
    delay = 1.0

    if not forced_to_wait:
        from zhihu_cli.content.handlers.following import get_my_url_token

        delay = 0.0 if get_my_url_token() is not None else 1.0  # Don't delay if logged in

    time.sleep(delay * min(generate_lognormal(sleep_factor, 0.5), 3 * sleep_factor))
