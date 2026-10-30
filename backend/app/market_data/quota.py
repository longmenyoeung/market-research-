"""Conservative rolling request budgets; independent of exchange write APIs."""
from collections import deque


class RollingBudget:
    def __init__(self, limit: int, seconds: float):
        self.limit, self.seconds = limit, seconds
        self.events = deque()

    def _expire(self, now: float) -> None:
        while self.events and self.events[0][0]+self.seconds <= now:
            self.events.popleft()

    def delay(self, weight: int, now: float) -> float:
        self._expire(now)
        used=sum(value for _,value in self.events)
        if used+weight <= self.limit:
            return 0
        for timestamp,value in self.events:
            used-=value
            if used+weight <= self.limit:
                return max(0,timestamp+self.seconds-now)
        return self.seconds

    def record(self, weight: int, now: float) -> None:
        self._expire(now)
        self.events.append((now,min(weight,self.limit)))

    def observe_usage(self, used: int, now: float) -> None:
        self._expire(now)
        known=sum(value for _,value in self.events)
        if used>known:
            self.record(min(used-known,self.limit),now)


def request_weight(path: str, params: dict | None = None) -> int:
    if path.endswith('/klines'):
        limit=int((params or {}).get('limit',500))
        return 1 if limit<100 else 2 if limit<500 else 5 if limit<=1000 else 10
    return {'/fapi/v1/ticker/24hr':40,'/fapi/v1/ticker/bookTicker':5,
            '/fapi/v1/premiumIndex':10,'/fapi/v1/depth':2}.get(path,1)
