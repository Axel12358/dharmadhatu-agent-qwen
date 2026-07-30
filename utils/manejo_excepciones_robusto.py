import asyncio
from playwright.async_api import async_playwright
from tenacity import retry, stop_after_attempt, wait_fixed

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def safe_request(page, url):
    try:
        await page.goto(url)
    except Exception as e:
        raise e

async def example_function():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await safe_request(page, 'https://example.com')
        await browser.close()

if __name__ == "__main__":
    asyncio.run(example_function())
