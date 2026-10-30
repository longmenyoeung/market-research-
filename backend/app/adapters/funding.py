"""Validated public historical funding, including observed settlement mark prices."""
from app.adapters.base import MarketDataError
from app.adapters.metrics import BASE, number, timestamp
from app.schemas.derivatives import FundingQuery


async def fetch_funding_history(adapter, query: FundingQuery) -> list[dict]:
    payload = await adapter._read(BASE+'/fapi/v1/fundingRate', {
        'symbol':query.symbol, 'startTime':str(query.start_time),
        'endTime':str(query.end_time), 'limit':str(query.limit)})
    try:
        if not isinstance(payload,list) or len(payload)>query.limit:
            raise ValueError()
        result=[]
        seen=set()
        for point in payload:
            if point['symbol'] != query.symbol:
                raise ValueError()
            time = timestamp(point['fundingTime'])
            rate = number(point['fundingRate'])
            mark = number(point['markPrice'],positive=True)
            rate_type = point.get('rateType','Regular')
            identity = (time,rate_type)
            if (not query.start_time <= time <= query.end_time or abs(rate)>1
                    or identity in seen or rate_type not in {'Regular','Special'}
                    or result and time<result[-1]['time']):
                raise ValueError()
            seen.add(identity)
            result.append(dict(time=time, rate=rate, mark_price=mark,rate_type=rate_type))
        return result
    except (KeyError,TypeError,ValueError) as exc:
        raise MarketDataError('invalid_upstream_data','Historical funding data is invalid or missing settlement mark prices.') from exc
