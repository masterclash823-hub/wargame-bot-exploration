"""Bounded Gemini failover for event drafts, decisions and expeditions."""
import asyncio
import logging
import math
import time
from urllib.parse import quote

import aiohttp
import config

REQUEST_TIMEOUT = 20
TOTAL_TIMEOUT = 60
COOLDOWN = 60
RETRYABLE = {404, 429, 500, 502, 503, 504}
_unavailable_until = {}
log = logging.getLogger(__name__)


class EventAIError(RuntimeError):
    def __init__(self, status=0, retry_after=0):
        # Never include response bodies, player prompts or credentials in errors.
        super().__init__(f'Event AI unavailable (status {status})')
        self.status,self.retry_after=status,retry_after


def models():
    names=[config.GEMINI_MODEL,*config.GEMINI_FALLBACK_MODELS.split(',')]
    return list(dict.fromkeys(model for name in names if (model:=name.strip().removeprefix('models/'))))


def retry_delay(headers, payload):
    values=[headers.get('Retry-After','0')]
    if isinstance(payload,dict) and isinstance(payload.get('error'),dict):
        details=payload['error'].get('details',[])
        for detail in details if isinstance(details,list) else []:
            if isinstance(detail,dict) and str(detail.get('@type','')).endswith('RetryInfo'):
                values.append(str(detail.get('retryDelay','0')).removesuffix('s'))
    delay=0
    for value in values:
        try:
            seconds=float(value)
            if math.isfinite(seconds):delay=max(delay,seconds)
        except (ValueError,TypeError):pass
    return delay


async def _request(model,prompt,timeout):
    """One HTTP request, with cancellation and no hidden SDK retries."""
    url='https://generativelanguage.googleapis.com/v1beta/models/'+quote(model,safe='')+':generateContent'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(url,headers={'x-goog-api-key':config.GEMINI_API_KEY},
                                json={'contents':[{'role':'user','parts':[{'text':prompt}]}]},
                                allow_redirects=False) as response:
            try:payload=await response.json(content_type=None)
            except ValueError:payload={}
            if response.status!=200:
                raise EventAIError(response.status,retry_delay(response.headers,payload))
    candidates=payload.get('candidates',[]) if isinstance(payload,dict) else []
    if not candidates or candidates[0].get('finishReason') not in (None,'STOP'):
        raise EventAIError(200)  # Empty, blocked or truncated output is not a quota failure.
    parts=candidates[0].get('content',{}).get('parts',[])
    text=''.join(p['text'] for p in parts if isinstance(p.get('text'),str) and not p.get('thought')).strip()
    if not text:raise EventAIError(200)
    return text


async def generate_text(prompt):
    """Try each configured model once; all failures leave game fallback to callers."""
    deadline=time.monotonic()+TOTAL_TIMEOUT
    for model in models():
        now=time.monotonic()
        # Sharing cooldowns keeps later stages and /event all off an exhausted model.
        key=(config.GEMINI_API_KEY,model)
        if _unavailable_until.get(key,0)>now:continue
        remaining=deadline-now
        if remaining<=0:break
        timeout=min(REQUEST_TIMEOUT,remaining)
        try:
            return await asyncio.wait_for(_request(model,prompt,timeout),timeout=timeout)
        except EventAIError as exc:
            if exc.status not in RETRYABLE:raise
            status=exc.status;delay=max(COOLDOWN,exc.retry_after)
        except (TimeoutError,aiohttp.ClientError):
            status='connection/timeout';delay=COOLDOWN
        _unavailable_until[key]=time.monotonic()+delay
        log.warning('Event AI model %s unavailable (%s); trying next configured model',model,status)
    raise EventAIError()
