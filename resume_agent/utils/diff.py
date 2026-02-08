import difflib
import os
from datetime import datetime
from pathlib import Path
from .logger import logger

def generate_diff_markdown(original_text, new_text, job_title, company):
    """Generate markdown diff and save to change_logs directory"""
    diff = difflib.unified_diff(
        original_text.splitlines(),
        new_text.splitlines(),
        fromfile="Original Resume",
        tofile="Tailored Resume",
        lineterm=""
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Use project root for change_logs
    project_root = Path(__file__).parent.parent.parent
    folder = project_root / "change_logs"
    os.makedirs(folder, exist_ok=True)

    filename = f"{company}_{job_title}_diff_{timestamp}.md".replace(" ", "_")
    path = folder / filename

    with open(path, "w") as f:
        f.write("\n".join(diff))

    logger.info("Diff saved", path=str(path), job_title=job_title, company=company)
    return str(path)
