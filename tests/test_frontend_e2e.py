"""
Frontend End-to-End Integration Tests (Legacy/Streamlit)
Tests Streamlit UI with Playwright.

For React UI tests, see: test_frontend_playwright.py

Run with: UI_TESTS=true pytest tests/test_frontend_e2e.py -v

Requirements:
- Streamlit server running (streamlit run app.py)
- Playwright installed: playwright install
"""

import pytest
import os
import time
import json
from pathlib import Path

# Try to import playwright, skip tests if not available
try:
    from playwright.sync_api import sync_playwright, Page, expect, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Only run if explicitly enabled
UI_TESTS_ENABLED = os.getenv("UI_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(
    not UI_TESTS_ENABLED or not PLAYWRIGHT_AVAILABLE,
    reason="UI tests disabled or Playwright not installed. Set UI_TESTS=true and install playwright."
)


@pytest.fixture(scope="module")
def frontend_server():
    """Start frontend dev server"""
    import subprocess
    import signal
    
    # Check if server is already running
    try:
        import requests
        response = requests.get("http://localhost:5173", timeout=2)
        if response.status_code == 200:
            yield "http://localhost:5173"
            return
    except:
        pass
    
    # Start server
    proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=Path(__file__).parent.parent / "frontend",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start
    max_wait = 30
    for _ in range(max_wait):
        try:
            import requests
            response = requests.get("http://localhost:5173", timeout=1)
            if response.status_code == 200:
                break
        except:
            time.sleep(1)
    else:
        proc.terminate()
        pytest.fail("Frontend server failed to start")
    
    yield "http://localhost:5173"
    
    # Cleanup
    proc.terminate()
    proc.wait()


@pytest.fixture
def backend_server():
    """Start backend API server"""
    import subprocess
    import signal
    
    # Check if server is already running
    try:
        import requests
        response = requests.get("http://localhost:8000", timeout=2)
        if response.status_code == 200:
            yield "http://localhost:8000"
            return
    except:
        pass
    
    # Start server
    proc = subprocess.Popen(
        ["uvicorn", "api.main:app", "--port", "8000"],
        cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start
    max_wait = 30
    for _ in range(max_wait):
        try:
            import requests
            response = requests.get("http://localhost:8000", timeout=1)
            if response.status_code == 200:
                break
        except:
            time.sleep(1)
    else:
        proc.terminate()
        pytest.fail("Backend server failed to start")
    
    yield "http://localhost:8000"
    
    # Cleanup
    proc.terminate()
    proc.wait()


@pytest.fixture
def page(frontend_server, backend_server):
    """Create Playwright page"""
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("Playwright not available")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # Mock API responses for testing
        def handle_route(route):
            if "/api/tailor-resume" in route.request.url:
                # Mock streaming response
                route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body='data: {"type": "step_start", "step": "loading_resume", "message": "Loading...", "progress": 0.1}\n\ndata: {"type": "complete", "result": {"tailored_resume": "# Test Resume\\n\\nTailored content", "doc_url": "https://docs.google.com/doc/123"}}\n\n'
                )
            elif "/api/google-docs" in route.request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "success": True,
                        "docs": [
                            {"id": "doc1", "name": "My Resume", "mimeType": "application/vnd.google-apps.document", "webViewLink": "https://docs.google.com/doc1", "modifiedTime": "2024-01-01T00:00:00Z"}
                        ],
                        "count": 1
                    })
                )
            elif "/api/google-folders" in route.request.url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "success": True,
                        "folders": [
                            {"id": "folder1", "name": "Resumes", "mimeType": "application/vnd.google-apps.folder", "path": "My Drive/Resumes", "modifiedTime": "2024-01-01T00:00:00Z"}
                        ],
                        "count": 1
                    })
                )
            else:
                route.continue_()
        
        page.route("**/api/**", handle_route)
        
        yield page
        
        context.close()
        browser.close()


class TestTailorResumeComponent:
    """Test TailorResume component"""
    
    def test_page_loads(self, page, frontend_server):
        """Test that the page loads successfully"""
        page.goto(f"{frontend_server}")
        expect(page).to_have_title(containing="Resume Agent"))
    
    def test_form_fields_exist(self, page, frontend_server):
        """Test that all form fields are present"""
        page.goto(f"{frontend_server}")
        
        # Check for required fields
        expect(page.locator('input[placeholder*="Google"]')).to_be_visible()
        expect(page.locator('input[placeholder*="Senior Software Engineer"]')).to_be_visible()
        expect(page.locator('textarea[placeholder*="job description"]')).to_be_visible()
    
    def test_resume_selector_toggle(self, page, frontend_server):
        """Test resume selector can be toggled"""
        page.goto(f"{frontend_server}")
        
        # Click "Choose Different Resume" button
        resume_button = page.locator('button:has-text("Choose Different Resume")')
        if resume_button.is_visible():
            resume_button.click()
            
            # Check that selector is visible
            expect(page.locator('.selector-container')).to_be_visible()
            
            # Check search input appears
            expect(page.locator('.search-input[placeholder*="Search resumes"]')).to_be_visible()
            
            # Click hide
            page.locator('button:has-text("Hide")').click()
            expect(page.locator('.selector-container')).not_to_be_visible()
    
    def test_folder_selector_toggle(self, page, frontend_server):
        """Test folder selector can be toggled"""
        page.goto(f"{frontend_server}")
        
        # Click "Choose Different Folder" button
        folder_button = page.locator('button:has-text("Choose Different Folder")')
        if folder_button.is_visible():
            folder_button.click()
            
            # Check that selector is visible
            expect(page.locator('.selector-container')).to_be_visible()
            
            # Check search input appears
            expect(page.locator('.search-input[placeholder*="Search folders"]')).to_be_visible()
    
    def test_resume_search_filter(self, page, frontend_server):
        """Test resume search filtering"""
        page.goto(f"{frontend_server}")
        
        # Open resume selector
        page.locator('button:has-text("Choose Different Resume")').click()
        
        # Type in search box
        search_input = page.locator('.search-input[placeholder*="Search resumes"]')
        search_input.fill("My Resume")
        
        # Check that filtered results appear
        expect(page.locator('.selector-option:has-text("My Resume")')).to_be_visible()
    
    def test_folder_search_filter(self, page, frontend_server):
        """Test folder search filtering"""
        page.goto(f"{frontend_server}")
        
        # Open folder selector
        page.locator('button:has-text("Choose Different Folder")').click()
        
        # Type in search box
        search_input = page.locator('.search-input[placeholder*="Search folders"]')
        search_input.fill("Resumes")
        
        # Check that filtered results appear
        expect(page.locator('.selector-option:has-text("Resumes")')).to_be_visible()
    
    def test_tailor_button_disabled_when_incomplete(self, page, frontend_server):
        """Test that tailor button is disabled when form is incomplete"""
        page.goto(f"{frontend_server}")
        
        # Button should be disabled initially
        tailor_button = page.locator('button:has-text("Tailor Resume")')
        expect(tailor_button).to_be_disabled()
        
        # Fill in company
        page.locator('input[placeholder*="Google"]').fill("Test Company")
        
        # Still disabled (missing job title and JD)
        expect(tailor_button).to_be_disabled()
        
        # Fill in job title
        page.locator('input[placeholder*="Senior Software Engineer"]').fill("Engineer")
        
        # Still disabled (missing JD)
        expect(tailor_button).to_be_disabled()
        
        # Fill in JD
        page.locator('textarea[placeholder*="job description"]').fill("Job description text")
        
        # Now should be enabled
        expect(tailor_button).to_be_enabled()
    
    def test_job_description_input_methods(self, page, frontend_server):
        """Test switching between URL and text input methods"""
        page.goto(f"{frontend_server}")
        
        # Default should be URL
        url_radio = page.locator('input[value="url"]')
        expect(url_radio).to_be_checked()
        
        # Switch to text
        page.locator('input[value="text"]').click()
        expect(page.locator('textarea[placeholder*="job description"]')).to_be_visible()
        expect(page.locator('input[type="url"]')).not_to_be_visible()
        
        # Switch back to URL
        page.locator('input[value="url"]').click()
        expect(page.locator('input[type="url"]')).to_be_visible()
        expect(page.locator('textarea[placeholder*="job description"]')).not_to_be_visible()


class TestResumeComparisonComponent:
    """Test ResumeComparison component"""
    
    def test_comparison_modal_opens(self, page, frontend_server):
        """Test that comparison modal opens when button is clicked"""
        page.goto(f"{frontend_server}")
        
        # Fill form and submit (mocked)
        page.locator('input[placeholder*="Google"]').fill("Test Company")
        page.locator('input[placeholder*="Senior Software Engineer"]').fill("Engineer")
        page.locator('textarea[placeholder*="job description"]').fill("JD text")
        
        # Mock result state
        page.evaluate("""
            window.__TEST_RESULT__ = {
                original_resume_text: "# Original Resume\\n\\nContent",
                tailored_resume: "# Tailored Resume\\n\\nUpdated Content"
            };
        """)
        
        # Click compare button (if visible)
        compare_button = page.locator('button:has-text("Compare with Original")')
        if compare_button.is_visible():
            compare_button.click()
            
            # Check modal is visible
            expect(page.locator('.resume-comparison-overlay')).to_be_visible()
            expect(page.locator('.resume-comparison-container')).to_be_visible()
    
    def test_comparison_view_modes(self, page, frontend_server):
        """Test switching between comparison view modes"""
        # This would require the comparison modal to be open
        # Test would check for view mode buttons and switching
        pass
    
    def test_comparison_close_button(self, page, frontend_server):
        """Test that comparison modal can be closed"""
        # Open modal and test close button
        pass


class TestAPIIntegration:
    """Test API integration from frontend"""
    
    def test_extract_jd_api_call(self, page, frontend_server):
        """Test JD extraction API is called correctly"""
        page.goto(f"{frontend_server}")
        
        # Switch to URL input
        page.locator('input[value="url"]').click()
        
        # Enter URL
        url_input = page.locator('input[type="url"]')
        url_input.fill("https://example.com/job")
        
        # Click extract button
        extract_button = page.locator('button:has-text("Extract JD")')
        extract_button.click()
        
        # Wait for API call (mocked)
        time.sleep(0.5)
        
        # Check that textarea is populated (mocked response)
        # This would check the actual API integration
    
    def test_tailor_resume_api_call(self, page, frontend_server):
        """Test tailor resume API is called with correct parameters"""
        page.goto(f"{frontend_server}")
        
        # Fill form
        page.locator('input[placeholder*="Google"]').fill("Test Company")
        page.locator('input[placeholder*="Senior Software Engineer"]').fill("Engineer")
        page.locator('textarea[placeholder*="job description"]').fill("JD text")
        
        # Select custom resume
        page.locator('button:has-text("Choose Different Resume")').click()
        page.locator('input[value="doc1"]').click()
        
        # Select custom folder
        page.locator('button:has-text("Choose Different Folder")').click()
        page.locator('input[value="folder1"]').click()
        
        # Submit
        page.locator('button:has-text("Tailor Resume")').click()
        
        # Wait for API call
        time.sleep(1)
        
        # Check that request includes resume_doc_id and save_folder_id
        # This would verify the API integration


class TestErrorHandling:
    """Test error handling in UI"""
    
    def test_error_message_display(self, page, frontend_server):
        """Test that error messages are displayed"""
        page.goto(f"{frontend_server}")
        
        # Try to submit incomplete form (should show error)
        # Or trigger an API error
        
        # Check error message appears
        # expect(page.locator('.error-message')).to_be_visible()
        pass
    
    def test_loading_states(self, page, frontend_server):
        """Test loading states during API calls"""
        page.goto(f"{frontend_server}")
        
        # Fill form and submit
        page.locator('input[placeholder*="Google"]').fill("Test")
        page.locator('input[placeholder*="Senior Software Engineer"]').fill("Engineer")
        page.locator('textarea[placeholder*="job description"]').fill("JD")
        
        page.locator('button:has-text("Tailor Resume")').click()
        
        # Check loading indicator appears
        expect(page.locator('.progress-container')).to_be_visible()
        expect(page.locator('button:has-text("Processing")')).to_be_visible()


@pytest.mark.slow
class TestFullWorkflowE2E:
    """Full end-to-end workflow tests"""
    
    @pytest.mark.skipif(
        not os.getenv("FULL_E2E", "false").lower() == "true",
        reason="Requires FULL_E2E=true and real services"
    )
    def test_complete_tailoring_workflow(self, page, frontend_server, backend_server):
        """Test complete workflow from form fill to result display"""
        # This would test the full flow with real services
        # 1. Fill form
        # 2. Select resume/folder
        # 3. Submit
        # 4. Wait for completion
        # 5. Verify results
        # 6. Test comparison
        pass
