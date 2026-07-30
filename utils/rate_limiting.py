import asyncio
from playwright.async_api import async_playwright
from functools import wraps

class RateLimiter:
    def __init__(self, max_calls=10, period=60):
        self.max_calls = max_calls
        self.period = period
        self.tokens = []
    
    async def consume_token(self):
        while True:
            if len(self.tokens) < self.max_calls:
                self.tokens.append(asyncio.Event())
                await asyncio.sleep(self.period)
            else:
                token = self.tokens.pop(0)
                token.set()
                await token.wait()
    
    @wraps
    def decorator(func):
        rate_limiter = RateLimiter()

        async def wrapper(*args, **kwargs):
            await rate_limiter.consume_token()
            return await func(*args, **kwargs)
        
        return wrapper
