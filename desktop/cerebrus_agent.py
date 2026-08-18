#!/usr/bin/env python3
"""
Cerebrus Desktop Agent - Entry point for the Tauri desktop application.
This communicates with the FastAPI backend and manages the sidebar UI.
"""

import sys
import json
import asyncio
import aiohttp
from typing import Dict, Any


class CerebruDesktopAgent:
    def __init__(self, api_url: str = "http://localhost:8000"):
        self.api_url = api_url
        self.context = {}
    
    async def get_current_context(self) -> Dict[str, Any]:
        """Fetch current context from backend."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.api_url}/api/context/current") as resp:
                    if resp.status == 200:
                        self.context = await resp.json()
                        return self.context
            except Exception as e:
                print(f"Error fetching context: {e}")
        return {}
    
    async def report_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Report an event to the backend."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.api_url}/api/events/",
                    json=event_data
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
            except Exception as e:
                print(f"Error reporting event: {e}")
        return {}
    
    async def search_knowledge(self, query: str) -> Dict[str, Any]:
        """Search the knowledge base."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{self.api_url}/api/knowledge/search",
                    params={"query": query}
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
            except Exception as e:
                print(f"Error searching knowledge: {e}")
        return {}
    
    def format_context_display(self) -> str:
        """Format context for display in the UI."""
        lines = ["=== Cerebrus Context ===\n"]
        
        if self.context.get("crm_case"):
            lines.append(f"📋 Case: {self.context.get('crm_case')}")
            lines.append(f"👤 Customer: {self.context.get('customer')}")
        
        if self.context.get("call_active"):
            lines.append("☎️  CALL ACTIVE")
        
        if self.context.get("remote_session_active"):
            lines.append(f"🔗 Remote Session: {self.context.get('remote_host', 'Connected')}")
        
        if self.context.get("active_application"):
            lines.append(f"📱 Application: {self.context.get('active_application')}")
        
        return "\n".join(lines)


async def main():
    agent = CerebruDesktopAgent()
    
    print("🤖 Cerebrus Desktop Agent Starting...")
    
    # Get initial context
    context = await agent.get_current_context()
    print(agent.format_context_display())
    
    # Simulate monitoring loop (in production, this would use native event listeners)
    print("\n💡 Example: Monitoring desktop for events...")
    print("(Waiting for events from API, Screenpipe, or browser extension)")


if __name__ == "__main__":
    asyncio.run(main())
