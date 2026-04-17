# test_fit_evaluator.py
# Requires Google session (web app) for get_services().

import os
import pytest
from dotenv import load_dotenv
from langchain_ollama import OllamaLLM
from resume_agent.config import OLLAMA_MODEL, RESUME_DOC_ID
from resume_agent.storage.google_docs import get_services, read_google_doc
from resume_agent.agents.jd_extractor import extract_clean_jd
from resume_agent.agents.fit_evaluator import evaluate_resume_fit

load_dotenv()

model = OllamaLLM(model=OLLAMA_MODEL)

def test_fit_evaluator():
    try:
        drive_service, docs_service = get_services()
    except Exception as e:
        pytest.skip(f"Google is session-only: {e}. Use the web app.")

    resume_text = read_google_doc(docs_service, RESUME_DOC_ID)
    print("\n📄 Loaded resume content\n")

    url = input("🔗 Paste job URL: ").strip()
    jd_text = extract_clean_jd(url, model)

    print("\n🎯 Evaluating resume against job description...\n")
    result = evaluate_resume_fit(model, resume_text, jd_text)
    print("\n📊 Final Fit Evaluation:\n")
    print(result)

if __name__ == "__main__":
    test_fit_evaluator()
