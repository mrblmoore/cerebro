"""
Screenpipe Client - Connects to Screenpipe for desktop activity monitoring.
"""

from app.core.config import settings
import aiohttp
from typing import Dict, Any, List
import json


class ScreenpipeClient:
    def __init__(self):
        self.base_url = settings.SCREENPIPE_URL
    
    async def get_screenshots(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent screenshots from Screenpipe."""
        from app.core import logger
        async with aiohttp.ClientSession() as session:
            try:
                logger.info('screenpipe_client', 'Requesting screenshots', {'limit': limit, 'url': self.base_url + '/screenshots'})
                async with session.get(
                    f"{self.base_url}/screenshots",
                    params={"limit": limit}
                ) as resp:
                    data = await resp.json() if resp.status == 200 else []
                    logger.info('screenpipe_client', 'Received screenshots response', {'status': resp.status, 'count': len(data) if isinstance(data, list) else None})
                    return data
            except Exception as e:
                logger.error('screenpipe_client', 'Error fetching screenshots', {'error': str(e)})
                return []
    
    async def get_ocr(self, screenshot_id: str) -> str:
        """Get OCR text from a screenshot."""
        from app.core import logger
        async with aiohttp.ClientSession() as session:
            try:
                logger.info('screenpipe_client', 'Requesting OCR for screenshot', {'id': screenshot_id})
                async with session.get(
                    f"{self.base_url}/screenshots/{screenshot_id}/ocr"
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info('screenpipe_client', 'Received OCR', {'id': screenshot_id, 'len_chars': len(data.get('text',''))})
                        return data.get("text", "")
                    logger.warn('screenpipe_client', 'OCR returned non-200', {'status': resp.status})
                    return ""
            except Exception as e:
                logger.error('screenpipe_client', 'Error fetching OCR', {'error': str(e)})
                return ""
    
    async def get_active_window(self) -> Dict[str, Any]:
        """Get current active window information."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{self.base_url}/windows/active"
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return {}
            except Exception as e:
                print(f"Error fetching active window: {e}")
                return {}
    
    async def detect_applications(self) -> List[Dict[str, Any]]:
        """Detect active applications."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{self.base_url}/applications"
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return []
            except Exception as e:
                print(f"Error detecting applications: {e}")
                return []
