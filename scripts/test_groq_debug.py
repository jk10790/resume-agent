#!/usr/bin/env python3
"""Debug script to test Groq configuration and connection"""

import sys
from pathlib import Path

print("=" * 60)
print("Groq Configuration Debug")
print("=" * 60)

# Check .env file
print("\n1. Checking .env file...")
env_path = Path(".env")
if env_path.exists():
    print(f"   ✅ .env file exists: {env_path.absolute()}")
    with open(env_path) as f:
        content = f.read()
        has_groq_key = "GROQ_API_KEY" in content
        has_provider = "LLM_PROVIDER" in content
        print(f"   ✅ GROQ_API_KEY in file: {has_groq_key}")
        print(f"   ✅ LLM_PROVIDER in file: {has_provider}")
        if "LLM_PROVIDER" in content:
            for line in content.split('\n'):
                if line.startswith('LLM_PROVIDER'):
                    print(f"   📝 {line}")
else:
    print(f"   ❌ .env file not found at: {env_path.absolute()}")

# Check config loading
print("\n2. Checking config loading...")
try:
    # Force reload
    if 'resume_agent.config' in sys.modules:
        del sys.modules['resume_agent.config']
    if 'resume_agent' in sys.modules:
        del sys.modules['resume_agent']
    
    from resume_agent.config import settings
    print(f"   ✅ Config loaded successfully")
    print(f"   📝 LLM Provider: {settings.llm_provider}")
    print(f"   📝 Groq API Key present: {bool(settings.groq_api_key)}")
    print(f"   📝 Groq Model: {settings.groq_model}")
    
    if not settings.groq_api_key:
        print("   ❌ GROQ_API_KEY is not set in settings!")
        sys.exit(1)
    
    if settings.llm_provider != "groq":
        print(f"   ⚠️  LLM_PROVIDER is '{settings.llm_provider}', not 'groq'")
        print("   💡 Make sure LLM_PROVIDER=groq is in .env file")
except Exception as e:
    print(f"   ❌ Error loading config: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test Groq provider
print("\n3. Testing Groq provider connection...")
try:
    from resume_agent.services.llm_providers import GroqProvider
    from langchain_core.messages import SystemMessage, HumanMessage
    
    provider = GroqProvider(
        api_key=settings.groq_api_key,
        model_name=settings.groq_model or "llama-3.3-70b-versatile"
    )
    print("   ✅ GroqProvider initialized")
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Say 'Hello, debug test!' and nothing else.")
    ]
    
    print("   📡 Making API call...")
    response = provider.invoke(messages)
    
    print(f"   ✅ Response received: {response[:100]}")
    print("\n" + "=" * 60)
    print("✅ All checks passed! Groq is working correctly.")
    print("=" * 60)
    
except Exception as e:
    print(f"   ❌ Error testing Groq: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
