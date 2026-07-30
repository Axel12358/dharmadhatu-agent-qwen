import asyncio
from functools import wraps

class RateLimiter:
    """Limitador de tasa de llamadas usando asyncio.Semaphore."""
    
    def __init__(self, max_calls=10, period=60):
        """
        Args:
            max_calls: Máximo de llamadas permitidas en el periodo.
            period: Periodo en segundos para resetear el contador.
        """
        self.max_calls = max_calls
        self.period = period
        self.semaphore = asyncio.Semaphore(max_calls)
        self.tokens = []
    
    async def acquire(self):
        """Adquiere un permiso, esperando si es necesario."""
        await self.semaphore.acquire()
        asyncio.create_task(self._release_after_delay())
    
    async def _release_after_delay(self):
        """Libera el semáforo después del periodo."""
        await asyncio.sleep(self.period)
        self.semaphore.release()

def limit_rate(max_calls=10, period=60):
    """Decorador para limitar la tasa de llamadas de una función asíncrona."""
    limiter = RateLimiter(max_calls, period)
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            await limiter.acquire()
            return await func(*args, **kwargs)
        return wrapper
    return decorator
