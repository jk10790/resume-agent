# test_jd_extraction_agent.py

from langchain_ollama import OllamaLLM
from resume_agent.config import OLLAMA_MODEL
from resume_agent.agents.jd_extractor import extract_clean_jd

model = OllamaLLM(model=OLLAMA_MODEL)

url = input("Paste job description URL: ").strip()
jd_text = extract_clean_jd(url, model, max_retries=3)

print("\n🎯 Final extracted JD:\n")
print(jd_text[:1500])  # print preview
