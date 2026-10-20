import time
from collections import OrderedDict


class InteractionLimiter:
    def __init__(self, capacity=12, period=10, max_keys=4096, clock=time.monotonic):
        self.capacity = capacity
        self.rate = capacity / period
        self.max_keys = max_keys
        self.clock = clock
        self.buckets = OrderedDict()

    def allow(self, key):
        now = self.clock()
        tokens, updated, notice = self.buckets.pop(key, (self.capacity, now, float('-inf')))
        tokens = min(self.capacity, tokens + max(0, now - updated) * self.rate)
        allowed = tokens >= 1
        if allowed:
            tokens -= 1
        notify = not allowed and now - notice >= 5
        self.buckets[key] = (tokens, now, now if notify else notice)
        while len(self.buckets) > self.max_keys:
            self.buckets.popitem(last=False)
        return allowed, notify
