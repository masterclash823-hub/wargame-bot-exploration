"""Bounded, optional multi-provider failover for events and expeditions."""
import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import aiohttp
import config

REQUEST_TIMEOUT = 20
TOTAL_TIMEOUT = 60
COOLDOWN = 60
RETRYABLE = {404, 429, 500, 502, 503, 504}
AUTH_COOLDOWN = 900
MAX_OUTPUT_TOKENS = 2400
CHAT_ENDPOINTS = {
    'groq': 'https://api.groq.com/openai/v1/chat/completions',
    'mistral': 'https://api.mistral.ai/v1/chat/completions',
    'openrouter': 'https://openrouter.ai/api/v1/chat/completions',
}
_unavailable_until = {}
log = logging.getLogger(__name__)


class EventAIError(RuntimeError):
    def __init__(self, status=0, retry_after=0):
        # Never include response bodies, player prompts or credentials in errors.
        super().__init__(f'Event AI unavailable (status {status})')
        self.status,self.retry_after=status,retry_after


class EventAIOutputError(EventAIError):
    """A malformed or truncated response, distinct from a provider refusal."""
    def __init__(self):
        super().__init__(200)


def models():
    names=[config.GEMINI_MODEL,*config.GEMINI_FALLBACK_MODELS.split(',')]
    return list(dict.fromkeys(model for name in names if (model:=name.strip().removeprefix('models/'))))


@dataclass(frozen=True)
class Target:
    provider: str
    model: str
    api_key: str = field(repr=False)


def targets():
    """Try independent providers before a second model on the same account."""
    configured = {
        'gemini': (config.GEMINI_API_KEY, models()),
        'groq': (config.GROQ_API_KEY, [config.GROQ_EVENT_MODEL]),
        'mistral': (config.MISTRAL_API_KEY, [config.MISTRAL_EVENT_MODEL]),
        'openrouter': (config.OPENROUTER_API_KEY, [config.OPENROUTER_EVENT_MODEL]),
    }
    groups = []
    for provider in dict.fromkeys(s.strip().lower() for s in config.EVENT_AI_PROVIDERS.split(',')):
        if provider not in configured:
            continue
        key, names = configured[provider]
        if not key.strip():
            continue
        if provider == 'openrouter':
            names = [name for name in names if name == 'openrouter/free' or name.endswith(':free')]
            if not names:
                log.warning('Event AI: OpenRouter requires openrouter/free or a :free model; provider skipped')
        group = [Target(provider, name, key) for name in names if name]
        if group:
            groups.append(group)
    return [group[i] for i in range(max(map(len, groups), default=0)) for group in groups if i < len(group)]


def retry_delay(headers, payload):
    headers={str(k).lower():v for k,v in headers.items()}
    values=[headers.get('retry-after','0')]
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
        except (ValueError,TypeError):
            try:delay=max(delay,parsedate_to_datetime(value).timestamp()-time.time())
            except (ValueError,TypeError,OverflowError):pass
    # OpenRouter's account-level daily quota may give only an absolute reset time.
    if str(headers.get('x-ratelimit-remaining')) == '0':
        try:
            reset=float(headers.get('x-ratelimit-reset',0))
            if reset>1e12:reset/=1000
            if math.isfinite(reset):delay=max(delay,reset-time.time())
        except (ValueError,TypeError):pass
    return delay


async def _request(model,prompt,timeout):
    """One HTTP request, with cancellation and no hidden SDK retries."""
    url='https://generativelanguage.googleapis.com/v1beta/models/'+quote(model,safe='')+':generateContent'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(url,headers={'x-goog-api-key':config.GEMINI_API_KEY},
                                json={'contents':[{'role':'user','parts':[{'text':prompt}]}],
                                      'generationConfig':{'maxOutputTokens':MAX_OUTPUT_TOKENS}},
                                allow_redirects=False) as response:
            try:payload=await response.json(content_type=None)
            except ValueError:payload={}
            if response.status!=200:
                raise EventAIError(response.status,retry_delay(response.headers,payload))
    if not isinstance(payload,dict):raise EventAIOutputError()
    feedback=payload.get('promptFeedback',{})
    if isinstance(feedback,dict) and feedback.get('blockReason'):raise EventAIError(200)
    candidates=payload.get('candidates',[])
    if not isinstance(candidates,list) or not candidates or not isinstance(candidates[0],dict):
        raise EventAIOutputError()
    reason=candidates[0].get('finishReason')
    if reason=='MAX_TOKENS':raise EventAIOutputError()
    if reason not in (None,'STOP'):raise EventAIError(200)
    content=candidates[0].get('content',{})
    parts=content.get('parts',[]) if isinstance(content,dict) else []
    if not isinstance(parts,list):raise EventAIOutputError()
    text=''.join(p['text'] for p in parts if isinstance(p,dict) and isinstance(p.get('text'),str) and not p.get('thought')).strip()
    if not text:raise EventAIOutputError()
    return text


async def _chat_request(target,prompt,timeout):
    body={'model':target.model,'messages':[{'role':'user','content':prompt}],'stream':False}
    if target.provider=='groq':
        body['max_completion_tokens']=MAX_OUTPUT_TOKENS
        if target.model.startswith('openai/gpt-oss-'):
            body.update(reasoning_effort='low',include_reasoning=False)
    else:
        body['max_tokens']=MAX_OUTPUT_TOKENS
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(CHAT_ENDPOINTS[target.provider],
                                headers={'Authorization':'Bearer '+target.api_key},
                                json=body,allow_redirects=False) as response:
            try:payload=await response.json(content_type=None)
            except ValueError:payload={}
            if response.status!=200:
                raise EventAIError(response.status,retry_delay(response.headers,payload))
    choices=payload.get('choices',[]) if isinstance(payload,dict) else []
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
        raise EventAIOutputError()
    reason=choices[0].get('finish_reason')
    if reason=='content_filter':raise EventAIError(200)
    if reason!='stop':raise EventAIOutputError()
    message=choices[0].get('message',{})
    if not isinstance(message,dict):raise EventAIOutputError()
    if message.get('refusal'):raise EventAIError(200)
    text=message.get('content')
    if isinstance(text,list):
        text=''.join(p['text'] for p in text if isinstance(p,dict) and p.get('type')=='text' and isinstance(p.get('text'),str))
    if not isinstance(text,str) or not text.strip():
        raise EventAIOutputError()
    return text.strip()


async def generate_text(prompt, *, validate=None):
    """Bound the whole operation; optional validation retries malformed game output."""
    deadline=time.monotonic()+TOTAL_TIMEOUT
    candidates=targets()
    for index,target in enumerate(candidates):
        now=time.monotonic()
        # Sharing cooldowns keeps later stages and /event all off an exhausted model.
        key=(target.provider,target.api_key,target.model)
        provider_key=(target.provider,target.api_key,'*')
        if max(_unavailable_until.get(key,0),_unavailable_until.get(provider_key,0))>now:continue
        remaining=deadline-now
        if remaining<=0:break
        # Reserve time for independent providers even when the first service hangs.
        slots=sum(max(_unavailable_until.get((t.provider,t.api_key,t.model),0),
                      _unavailable_until.get((t.provider,t.api_key,'*'),0))<=now for t in candidates[index:])
        timeout=min(REQUEST_TIMEOUT,remaining/max(1,slots))
        try:
            request=_request(target.model,prompt,timeout) if target.provider=='gemini' else _chat_request(target,prompt,timeout)
            text=await asyncio.wait_for(request,timeout=timeout)
            if validate:
                try:validate(text)
                except (ValueError,TypeError,KeyError):
                    log.warning('Event AI provider %s model %s returned invalid game data; trying next model',target.provider,target.model)
                    continue
            return text
        except EventAIOutputError:
            log.warning('Event AI provider %s model %s returned incomplete output; trying next model',target.provider,target.model)
            continue
        except EventAIError as exc:
            if exc.status==200:raise  # Never route a refused/filtered response to another provider.
            if exc.status in (400,401,402,403):
                _unavailable_until[provider_key]=time.monotonic()+max(AUTH_COOLDOWN,exc.retry_after)
                log.warning('Event AI provider %s unavailable (status %s); trying another provider',target.provider,exc.status)
                continue
            if exc.status not in RETRYABLE:raise
            status=exc.status;delay=max(COOLDOWN,exc.retry_after)
        except (TimeoutError,aiohttp.ClientError):
            status='connection/timeout';delay=COOLDOWN
        _unavailable_until[key]=time.monotonic()+delay
        log.warning('Event AI provider %s model %s unavailable (%s); trying next configured model',target.provider,target.model,status)
    raise EventAIError()
