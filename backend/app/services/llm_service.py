"""
LLM Service - Orchestrates LLM calls for reasoning and generation.
Supports multiple LLM providers.
"""

from app.core.config import settings
from typing import Dict, Any, List
import openai
import requests


class LLMService:
    def __init__(self):
        # initialize providers
        self.provider = getattr(settings, 'LLM_PROVIDER', 'openai')
        self.model = settings.OPENAI_MODEL
        if self.provider == 'openai':
            openai.api_key = settings.OPENAI_API_KEY
        # Ollama and Qwen do not need SDKs here; use HTTP

    def generate_case_summary(self, case_data: Dict[str, Any]) -> str:
        """Generate a summary of the case from raw data."""
        prompt = f"""
        Generate a concise support case summary based on this information:
        
        Customer: {case_data.get('customer')}
        Issue: {case_data.get('title')}
        Error Code: {case_data.get('error_code')}
        Application: {case_data.get('application')}
        
        Provide a 2-3 sentence summary suitable for a CRM note.
        """
        
        return self._call_llm(prompt)
    
    def generate_troubleshooting_steps(self, case_data: Dict[str, Any], context: Dict[str, Any]) -> str:
        """Generate recommended troubleshooting steps."""
        prompt = f"""
        Generate troubleshooting steps for this support case:
        
        Issue: {case_data.get('title')}
        Error Code: {case_data.get('error_code')}
        Application: {case_data.get('application')}
        
        Provide 3-5 clear, actionable troubleshooting steps.
        """
        
        return self._call_llm(prompt)
    
    def generate_next_steps(self, context: Dict[str, Any], relevant_docs: List[Dict[str, Any]]) -> str:
        """Generate recommended next steps based on context and knowledge."""
        doc_summary = "\n".join([f"- {d['title']}: {d['excerpt']}" for d in relevant_docs[:3]])
        
        prompt = f"""
        Based on the current support context and available documentation, what's the recommended next step?
        
        Current Case: {context.get('crm_case')}
        Customer: {context.get('customer')}
        Call Active: {context.get('call_active')}
        
        Relevant Documentation:
        {doc_summary}
        
        Provide 1-2 sentences with your recommendation.
        """
        
        return self._call_llm(prompt)
    
    def _call_llm(self, prompt: str) -> str:
        """Make a call to the selected LLM provider."""
        import time
        from app.core import logger
        start = time.time()
        provider = self.provider.lower()
        try:
            logger.info('llm_service', 'Sending request to LLM', {'provider': provider, 'model': self.model, 'prompt_preview': prompt[:400]})
            if provider == 'openai':
                resp_text = self._call_openai(prompt)
            elif provider == 'ollama':
                resp_text = self._call_ollama(prompt)
            elif provider == 'qwen':
                resp_text = self._call_qwen(prompt)
            else:
                raise ValueError(f'Unknown LLM provider: {provider}')
            duration = time.time() - start
            logger.info('llm_service', 'Received response from LLM', {'provider': provider, 'duration_s': duration})
            return resp_text
        except Exception as e:
            duration = time.time() - start
            logger.error('llm_service', 'LLM request failed', {'error': str(e), 'duration_s': duration})
            return f"(Unable to generate response: {str(e)})"

    def _call_openai(self, prompt: str) -> str:
        response = openai.ChatCompletion.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a helpful technical support assistant. Be concise and actionable."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )
        return response.choices[0].message.content

    def _call_ollama(self, prompt: str) -> str:
        """Call a local Ollama server (HTTP API)."""
        from app.core import logger
        url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
        endpoint = f"{url}/api/generate"
        payload = {
            'model': self.model,
            'prompt': prompt,
            'max_tokens': 500
        }
        try:
            r = requests.post(endpoint, json=payload, timeout=60)
            logger.debug('llm_service', 'Ollama response', {'status': r.status_code, 'text_preview': (r.text or '')[:400]})
            r.raise_for_status()
            data = r.json()
            # Ollama responses vary; attempt common paths
            if isinstance(data, dict):
                # newer Ollama: {'id':..., 'created':..., 'model':..., 'choices':[{'message':{'content':'...'}}]}
                choices = data.get('choices') or data.get('outputs')
                if choices and isinstance(choices, list):
                    first = choices[0]
                    # look for message.content
                    if isinstance(first, dict) and first.get('message') and first['message'].get('content'):
                        return first['message']['content']
                    # or output.text
                    if isinstance(first, dict) and first.get('text'):
                        return first.get('text')
                # fallback: join outputs
                if 'output' in data and isinstance(data['output'], list):
                    return '\n'.join([str(o) for o in data['output']])
            # fallback to full text
            return r.text
        except Exception as e:
            logger.error('llm_service', 'Ollama call failed', {'error': str(e), 'endpoint': endpoint})
            raise

    def _call_qwen(self, prompt: str) -> str:
        """Call a Qwen-compatible HTTP endpoint. Expects QWEN_API_URL and QWEN_API_KEY in config."""
        from app.core import logger
        url = getattr(settings, 'QWEN_API_URL', None)
        key = getattr(settings, 'QWEN_API_KEY', None)
        if not url or not key:
            raise RuntimeError('Qwen endpoint or key not configured')
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        payload = {
            'model': self.model,
            'prompt': prompt,
            'max_tokens': 500
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            logger.debug('llm_service', 'Qwen response', {'status': r.status_code, 'text_preview': (r.text or '')[:400]})
            r.raise_for_status()
            data = r.json()
            # parse common response shapes
            if isinstance(data, dict):
                if 'answer' in data:
                    return data['answer']
                if 'choices' in data and isinstance(data['choices'], list):
                    c = data['choices'][0]
                    if isinstance(c, dict) and c.get('text'):
                        return c.get('text')
            return r.text
        except Exception as e:
            logger.error('llm_service', 'Qwen call failed', {'error': str(e), 'endpoint': url})
            raise
