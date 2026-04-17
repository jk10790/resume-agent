import os
import pytest
from resume_agent.config import GOOGLE_FOLDER_ID, RESUME_DOC_ID
from resume_agent.agents.resume_tailor import tailor_resume_for_job
from resume_agent.storage.google_docs import read_google_doc, write_to_google_doc, get_services
from resume_agent.storage.google_drive import get_subfolder_id_for_job, copy_doc_to_folder
from resume_agent.utils.diff import generate_diff_markdown


def test_tailor_resume():
    """Requires Google session (web app). Skips if session-based auth not available."""
    try:
        drive_service, docs_service = get_services()
    except Exception as e:
        pytest.skip(f"Google is session-only: {e}. Use the web app to tailor with Drive.")
    # Config
    resume_doc_id = RESUME_DOC_ID
    parent_folder_id = GOOGLE_FOLDER_ID
    job_title = "Senior Manager"
    company = "Capital One"

    print("📁 Preparing subfolder and copying resume...")
    subfolder_id = get_subfolder_id_for_job(parent_folder_id, job_title, company, drive_service=drive_service)
    tailored_doc_id = copy_doc_to_folder(resume_doc_id, subfolder_id, f"{job_title}_Tailored", drive_service=drive_service)

    print("📝 Reading copied resume content...")
    resume_text = read_google_doc(docs_service, tailored_doc_id)

    print("📄 Loading job description...")
    jd_path = "sample_jd.txt"
    if not os.path.exists(jd_path):
        raise FileNotFoundError(f"❌ Missing file: {jd_path}")
    with open(jd_path, "r") as f:
        jd_text = f.read()

    print("🤖 Tailoring resume with memory + LLM...")
    tailored_resume = tailor_resume_for_job(resume_text, jd_text)

    print("🪟 Writing markdown diff...")
    generate_diff_markdown(resume_text, tailored_resume, job_title, company)

    print("✍️ Writing tailored resume to Google Docs...")
    write_to_google_doc(tailored_doc_id, tailored_resume, docs_service=docs_service)

    print(f"✅ Tailored resume written to:\nhttps://docs.google.com/document/d/{tailored_doc_id}")


if __name__ == "__main__":
    test_tailor_resume()
