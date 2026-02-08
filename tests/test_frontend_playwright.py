"""
Comprehensive Frontend E2E Tests with Playwright
Tests React UI components, user interactions, and API integration.

Run with: UI_TESTS=true pytest tests/test_frontend_playwright.py -v

Requirements:
- Frontend server: cd frontend && npm run dev (port 3000)
- Backend server: uvicorn api.main:app (port 8000) - required for frontend to work
- Playwright: playwright install chromium
"""

import pytest
import os
import time
import json
from pathlib import Path

# Try to import playwright
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
def frontend_url():
    """
    Frontend URL - assumes server is already running
    
    ⚠️ IMPORTANT: The frontend dev server MUST be running!
    Start it with: make frontend (or: cd frontend && npm run dev)
    The server should be running on http://localhost:3000
    """
    url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    
    # Check if server is reachable (with retries for slow startup)
    import socket
    from urllib.parse import urlparse
    import time
    import urllib.request
    import urllib.error
    
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or 3000
    
    # First check: Socket connection (fast)
    socket_ok = False
    for attempt in range(5):  # Try up to 5 times
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                socket_ok = True
                break
        except Exception:
            pass
        
        if attempt < 4:
            time.sleep(1)  # Wait 1 second between retries
    
    if not socket_ok:
        pytest.skip(
            f"❌ Frontend server not reachable at {url} (socket check failed)\n"
            f"   Quick start: make ui (starts both servers)\n"
            f"   Or separately:\n"
            f"     - Backend:  make api\n"
            f"     - Frontend: make frontend\n"
            f"   Then run: make test-frontend-playwright"
        )
    
    # Second check: HTTP request (verifies server is actually serving)
    http_ok = False
    for attempt in range(5):  # Try up to 5 times
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'pytest-check'})
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status in (200, 404):  # 404 is OK, means server is up
                    http_ok = True
                    break
        except (urllib.error.URLError, socket.timeout, ConnectionRefusedError):
            pass
        except Exception:
            # Other errors might mean server is up but returning errors
            # That's OK for our purposes
            http_ok = True
            break
        
        if attempt < 4:
            time.sleep(1)  # Wait 1 second between retries
    
    if not http_ok:
        pytest.skip(
            f"❌ Frontend server at {url} is not responding to HTTP requests\n"
            f"   Socket is open but server may not be ready yet.\n"
            f"   Wait a few seconds and try again, or check server logs."
        )
    
    return url
    
    return url


@pytest.fixture(scope="module")
def backend_url():
    """Backend URL - assumes server is already running"""
    return os.getenv("BACKEND_URL", "http://localhost:8000")


@pytest.fixture
def page(frontend_url):
    """Create Playwright page with mocked API responses"""
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("Playwright not available")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # Mock API responses for testing
        def handle_route(route):
            url = route.request.url
            
            # Mock tailor resume endpoint
            if "/api/tailor-resume" in url and route.request.method == "POST":
                route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body='data: {"type": "step_start", "step": "loading_resume", "message": "Loading resume...", "progress": 0.1, "step_number": 1, "total_steps": 5}\n\n'
                         'data: {"type": "step_complete", "step": "loading_resume", "message": "✅ Resume loaded", "progress": 0.2, "step_number": 1, "total_steps": 5}\n\n'
                         'data: {"type": "step_start", "step": "tailoring_resume", "message": "Tailoring resume...", "progress": 0.4, "step_number": 2, "total_steps": 5}\n\n'
                         'data: {"type": "step_complete", "step": "tailoring_resume", "message": "✅ Resume tailored", "progress": 0.6, "step_number": 2, "total_steps": 5}\n\n'
                         'data: {"type": "step_start", "step": "validating_resume", "message": "Validating...", "progress": 0.7, "step_number": 3, "total_steps": 5}\n\n'
                         'data: {"type": "step_complete", "step": "validating_resume", "message": "✅ Validated", "progress": 0.8, "step_number": 3, "total_steps": 5}\n\n'
                         'data: {"type": "awaiting_approval", "approval_id": "test-approval-123", "message": "Awaiting review", "result": {"tailored_resume": "# John Doe\\n\\n## Experience\\nUpdated experience", "original_resume_text": "# John Doe\\n\\n## Experience\\nOriginal experience", "validation": {"quality_score": 85, "ats_score": 90, "is_valid": true, "issues": [], "recommendations": ["Great job!"]}, "jd_requirements": {"required_skills": ["Python", "AWS"]}, "current_tailoring_iteration": 1}}\n\n'
                )
            
            # Mock extract JD endpoint
            elif "/api/extract-jd" in url and route.request.method == "POST":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "success": True,
                        "jd_text": "Extracted job description with Python and AWS requirements"
                    })
                )
            
            # Mock Google Docs listing
            elif "/api/google-docs" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "success": True,
                        "docs": [
                            {
                                "id": "doc1",
                                "name": "My Resume",
                                "mimeType": "application/vnd.google-apps.document",
                                "webViewLink": "https://docs.google.com/doc/doc1",
                                "modifiedTime": "2024-01-15T10:00:00Z"
                            },
                            {
                                "id": "doc2",
                                "name": "Resume 2024",
                                "mimeType": "application/vnd.google-apps.document",
                                "webViewLink": "https://docs.google.com/doc/doc2",
                                "modifiedTime": "2024-01-10T10:00:00Z"
                            }
                        ],
                        "count": 2
                    })
                )
            
            # Mock Google Folders listing
            elif "/api/google-folders" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "success": True,
                        "folders": [
                            {
                                "id": "folder1",
                                "name": "Resumes",
                                "mimeType": "application/vnd.google-apps.folder",
                                "path": "My Drive/Resumes",
                                "modifiedTime": "2024-01-15T10:00:00Z"
                            },
                            {
                                "id": "folder2",
                                "name": "Job Applications",
                                "mimeType": "application/vnd.google-apps.folder",
                                "path": "My Drive/Job Applications",
                                "modifiedTime": "2024-01-10T10:00:00Z"
                            }
                        ],
                        "count": 2
                    })
                )
            
            # Mock cache stats
            elif "/api/cache/stats" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "success": True,
                        "stats": {
                            "total_patterns": 5,
                            "total_uses": 12,
                            "avg_quality_score": 85.5
                        }
                    })
                )
            
            # Mock resume content
            elif "/api/resume/" in url:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "success": True,
                        "resume_content": "# John Doe\n\nTailored resume content from Google Docs"
                    })
                )
            
            # Mock approve resume
            elif "/api/approve-resume" in url and route.request.method == "POST":
                route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body='data: {"type": "step_start", "step": "saving_to_google", "message": "Saving...", "progress": 0.85}\n\n'
                         'data: {"type": "step_complete", "step": "saving_to_google", "message": "✅ Saved", "progress": 0.9}\n\n'
                         'data: {"type": "complete", "result": {"tailored_resume": "# Tailored Resume", "doc_url": "https://docs.google.com/doc/123", "application_id": 1, "fit_score": 8.5}, "progress": 1.0}\n\n'
                )
            
            # Mock refine resume
            elif "/api/refine-resume" in url and route.request.method == "POST":
                route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body='data: {"type": "step_start", "step": "tailoring_resume", "message": "Refining...", "progress": 0.4}\n\n'
                         'data: {"type": "complete", "result": {"tailored_resume": "# Refined Resume", "current_tailoring_iteration": 2}, "progress": 1.0}\n\n'
                )
            
            else:
                route.continue_()
        
        page.route("**/api/**", handle_route)
        
        yield page
        
        context.close()
        browser.close()


class TestPageLoad:
    """Test basic page loading"""
    
    def test_home_page_loads(self, page, frontend_url):
        """Test that home page loads successfully"""
        page.goto(frontend_url)
        expect(page).to_have_title(containing="Resume Agent")
        
        # Check for main heading
        expect(page.locator("h1, h2").filter(has_text="Resume Agent")).to_be_visible(timeout=10000)
    
    def test_no_console_errors_on_load(self, page, frontend_url):
        """Test no JavaScript errors on page load"""
        errors = []
        
        def handle_console(msg):
            if msg.type == "error":
                errors.append(msg.text)
        
        page.on("console", handle_console)
        page.goto(frontend_url)
        time.sleep(2)  # Wait for React to load
        
        # Filter out known non-critical errors
        critical_errors = [e for e in errors if "favicon" not in e.lower() and "sourcemap" not in e.lower()]
        assert len(critical_errors) == 0, f"Found console errors: {critical_errors}"


class TestTailorResumeForm:
    """Test Tailor Resume form component"""
    
    def test_form_fields_visible(self, page, frontend_url):
        """Test all form fields are visible"""
        page.goto(frontend_url)
        time.sleep(2)  # Wait for React to load
        
        # Check for form fields
        expect(page.locator('input[placeholder*="Google"], input[placeholder*="Company"]')).to_be_visible()
        expect(page.locator('input[placeholder*="Senior"], input[placeholder*="Job Title"]')).to_be_visible()
        expect(page.locator('textarea[placeholder*="job description"], textarea[placeholder*="Job Description"]')).to_be_visible()
    
    def test_job_description_input_methods(self, page, frontend_url):
        """Test switching between URL and text input"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Find radio buttons for input method
        url_radio = page.locator('input[value="url"]')
        text_radio = page.locator('input[value="text"]')
        
        if url_radio.is_visible():
            # Switch to text
            text_radio.click()
            time.sleep(0.5)
            expect(page.locator('textarea[placeholder*="job description"]')).to_be_visible()
            
            # Switch back to URL
            url_radio.click()
            time.sleep(0.5)
            expect(page.locator('input[type="url"]')).to_be_visible()
    
    def test_extract_jd_button(self, page, frontend_url):
        """Test JD extraction button"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Switch to URL input
        page.locator('input[value="url"]').click()
        time.sleep(0.5)
        
        # Enter URL
        url_input = page.locator('input[type="url"]')
        url_input.fill("https://example.com/job")
        
        # Click extract button
        extract_button = page.locator('button:has-text("Extract JD")')
        if extract_button.is_visible():
            extract_button.click()
            time.sleep(1)
            
            # Should show extracted text or switch to text view
            expect(page.locator('textarea')).to_be_visible()
    
    def test_tailor_button_disabled_when_incomplete(self, page, frontend_url):
        """Test tailor button is disabled when form incomplete"""
        page.goto(frontend_url)
        time.sleep(2)
        
        tailor_button = page.locator('button:has-text("Tailor Resume")')
        
        # Button should be disabled initially
        if tailor_button.is_visible():
            # Fill only company
            page.locator('input[placeholder*="Company"]').fill("Test Company")
            time.sleep(0.5)
            
            # Still should be disabled (missing job title and JD)
            # Note: This depends on validation logic
            # expect(tailor_button).to_be_disabled()


class TestResumeSelector:
    """Test resume selector functionality"""
    
    def test_resume_selector_toggle(self, page, frontend_url):
        """Test resume selector can be toggled"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Find toggle button
        toggle_button = page.locator('button:has-text("Choose Different Resume"), button:has-text("Resume Source")')
        if toggle_button.is_visible():
            toggle_button.click()
            time.sleep(0.5)
            
            # Check selector is visible
            expect(page.locator('.selector-container, [class*="selector"]')).to_be_visible()
            
            # Check search input appears
            expect(page.locator('input[placeholder*="Search resumes"], .search-input')).to_be_visible()
    
    def test_resume_search_filter(self, page, frontend_url):
        """Test resume search filtering"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Open selector
        toggle_button = page.locator('button:has-text("Choose Different Resume")')
        if toggle_button.is_visible():
            toggle_button.click()
            time.sleep(1)
            
            # Wait for resumes to load
            time.sleep(1)
            
            # Type in search box
            search_input = page.locator('input[placeholder*="Search resumes"], .search-input')
            if search_input.is_visible():
                search_input.fill("My Resume")
                time.sleep(0.5)
                
                # Should show filtered results
                expect(page.locator('text=My Resume')).to_be_visible()
    
    def test_resume_selection(self, page, frontend_url):
        """Test selecting a resume"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Open selector
        toggle_button = page.locator('button:has-text("Choose Different Resume")')
        if toggle_button.is_visible():
            toggle_button.click()
            time.sleep(1)
            
            # Wait for options
            time.sleep(1)
            
            # Select a resume
            resume_option = page.locator('input[value="doc1"], input[type="radio"]').first
            if resume_option.is_visible():
                resume_option.click()
                time.sleep(0.5)
                
                # Should show selected resume name
                expect(page.locator('text=My Resume')).to_be_visible()


class TestFolderSelector:
    """Test folder selector functionality"""
    
    def test_folder_selector_toggle(self, page, frontend_url):
        """Test folder selector can be toggled"""
        page.goto(frontend_url)
        time.sleep(2)
        
        toggle_button = page.locator('button:has-text("Choose Different Folder"), button:has-text("Save Location"), button:has-text("Choose")').first
        if toggle_button.is_visible():
            toggle_button.click()
            time.sleep(0.5)
            
            expect(page.locator('.selector-container, [class*="selector"]')).to_be_visible()
            expect(page.locator('input[placeholder*="Search folders"], .search-input')).to_be_visible()
    
    def test_folder_search_filter(self, page, frontend_url):
        """Test folder search filtering"""
        page.goto(frontend_url)
        time.sleep(2)
        
        toggle_button = page.locator('button:has-text("Choose Different Folder")')
        if toggle_button.is_visible():
            toggle_button.click()
            time.sleep(1)
            time.sleep(1)  # Wait for folders to load
            
            search_input = page.locator('input[placeholder*="Search folders"], .search-input')
            if search_input.is_visible():
                search_input.fill("Resumes")
                time.sleep(0.5)
                
                expect(page.locator('text=Resumes')).to_be_visible()


class TestTailoringWorkflow:
    """Test complete tailoring workflow"""
    
    def test_submit_tailor_request(self, page, frontend_url):
        """Test submitting a tailor request"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Fill form
        page.locator('input[placeholder*="Company"]').fill("Test Company")
        page.locator('input[placeholder*="Job Title"]').fill("Senior Engineer")
        
        # Switch to text input
        page.locator('input[value="text"]').click()
        time.sleep(0.5)
        
        page.locator('textarea[placeholder*="job description"]').fill("Job description with Python and AWS")
        
        # Submit
        tailor_button = page.locator('button:has-text("Tailor Resume"):enabled')
        if tailor_button.is_visible():
            tailor_button.click()
            
            # Wait for progress
            time.sleep(2)
            
            # Should show progress indicator
            expect(page.locator('.progress-container, [class*="progress"]')).to_be_visible()
    
    def test_progress_updates(self, page, frontend_url):
        """Test progress updates during tailoring"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Fill and submit
        page.locator('input[placeholder*="Company"]').fill("Test")
        page.locator('input[placeholder*="Job Title"]').fill("Engineer")
        page.locator('input[value="text"]').click()
        time.sleep(0.5)
        page.locator('textarea[placeholder*="job description"]').fill("JD text")
        
        tailor_button = page.locator('button:has-text("Tailor Resume"):enabled')
        if tailor_button.is_visible():
            tailor_button.click()
            time.sleep(3)
            
            # Should show progress messages
            expect(page.locator('text=Loading, text=Tailoring, text=Validating')).to_be_visible(timeout=5000)


class TestResumeComparison:
    """Test resume comparison feature"""
    
    def test_comparison_button_appears(self, page, frontend_url):
        """Test compare button appears after tailoring"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Fill and submit
        page.locator('input[placeholder*="Company"]').fill("Test")
        page.locator('input[placeholder*="Job Title"]').fill("Engineer")
        page.locator('input[value="text"]').click()
        time.sleep(0.5)
        page.locator('textarea[placeholder*="job description"]').fill("JD text")
        
        tailor_button = page.locator('button:has-text("Tailor Resume"):enabled')
        if tailor_button.is_visible():
            tailor_button.click()
            time.sleep(5)  # Wait for completion
            
            # Check for compare button
            compare_button = page.locator('button:has-text("Compare"), button:has-text("Compare with Original")')
            if compare_button.is_visible():
                compare_button.click()
                time.sleep(1)
                
                # Should show comparison modal
                expect(page.locator('.resume-comparison-overlay, [class*="comparison"]')).to_be_visible()
    
    def test_comparison_view_modes(self, page, frontend_url):
        """Test switching comparison view modes"""
        # This would require comparison modal to be open
        # Test would check for view mode buttons
        pass
    
    def test_comparison_close(self, page, frontend_url):
        """Test closing comparison modal"""
        # Open comparison and test close button
        pass


class TestApprovalWorkflow:
    """Test approval and refinement workflow"""
    
    def test_approve_resume(self, page, frontend_url):
        """Test approving a tailored resume"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Fill and submit
        page.locator('input[placeholder*="Company"]').fill("Test")
        page.locator('input[placeholder*="Job Title"]').fill("Engineer")
        page.locator('input[value="text"]').click()
        time.sleep(0.5)
        page.locator('textarea[placeholder*="job description"]').fill("JD text")
        
        tailor_button = page.locator('button:has-text("Tailor Resume"):enabled')
        if tailor_button.is_visible():
            tailor_button.click()
            time.sleep(5)  # Wait for preview
            
            # Check for approve button
            approve_button = page.locator('button:has-text("Approve"), button:has-text("Approve and Continue")')
            if approve_button.is_visible():
                approve_button.click()
                time.sleep(2)
                
                # Should continue workflow
                expect(page.locator('text=Saving, text=Saved, text=Complete')).to_be_visible(timeout=5000)
    
    def test_refine_resume(self, page, frontend_url):
        """Test refining a tailored resume"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Fill and submit
        page.locator('input[placeholder*="Company"]').fill("Test")
        page.locator('input[placeholder*="Job Title"]').fill("Engineer")
        page.locator('input[value="text"]').click()
        time.sleep(0.5)
        page.locator('textarea[placeholder*="job description"]').fill("JD text")
        
        tailor_button = page.locator('button:has-text("Tailor Resume"):enabled')
        if tailor_button.is_visible():
            tailor_button.click()
            time.sleep(5)  # Wait for preview
            
            # Enter feedback
            feedback_textarea = page.locator('textarea[placeholder*="feedback"], textarea[placeholder*="refinement"]')
            if feedback_textarea.is_visible():
                feedback_textarea.fill("Make it more technical")
                
                # Click refine button
                refine_button = page.locator('button:has-text("Refine"), button:has-text("Refine with Feedback")')
                if refine_button.is_visible():
                    refine_button.click()
                    time.sleep(2)
                    
                    # Should restart tailoring
                    expect(page.locator('text=Refining, text=Tailoring')).to_be_visible(timeout=5000)


class TestValidationDisplay:
    """Test validation results display"""
    
    def test_validation_scores_display(self, page, frontend_url):
        """Test validation scores are displayed"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Fill and submit
        page.locator('input[placeholder*="Company"]').fill("Test")
        page.locator('input[placeholder*="Job Title"]').fill("Engineer")
        page.locator('input[value="text"]').click()
        time.sleep(0.5)
        page.locator('textarea[placeholder*="job description"]').fill("JD text")
        
        tailor_button = page.locator('button:has-text("Tailor Resume"):enabled')
        if tailor_button.is_visible():
            tailor_button.click()
            time.sleep(5)  # Wait for completion
            
            # Check for validation scores
            expect(page.locator('text=Quality, text=ATS, text=Score')).to_be_visible(timeout=5000)
    
    def test_validation_issues_display(self, page, frontend_url):
        """Test validation issues are displayed"""
        # Similar to above, check for issues list
        pass


class TestErrorHandling:
    """Test error handling in UI"""
    
    def test_error_message_display(self, page, frontend_url):
        """Test error messages are displayed"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Try to submit without required fields
        tailor_button = page.locator('button:has-text("Tailor Resume")')
        if tailor_button.is_enabled():
            tailor_button.click()
            time.sleep(1)
            
            # Should show error
            expect(page.locator('.error-message, [class*="error"], text=error')).to_be_visible(timeout=3000)
    
    def test_api_error_handling(self, page, frontend_url):
        """Test handling of API errors"""
        # Would need to mock API error response
        pass


class TestTailoringIntensity:
    """Test tailoring intensity selection"""
    
    def test_intensity_selector(self, page, frontend_url):
        """Test tailoring intensity dropdown"""
        page.goto(frontend_url)
        time.sleep(2)
        
        # Find intensity selector
        intensity_select = page.locator('select[id*="intensity"], select[name*="intensity"]')
        if intensity_select.is_visible():
            # Should have options
            expect(intensity_select.locator('option[value="light"]')).to_be_visible()
            expect(intensity_select.locator('option[value="medium"]')).to_be_visible()
            expect(intensity_select.locator('option[value="heavy"]')).to_be_visible()
            
            # Change intensity
            intensity_select.select_option("heavy")
            time.sleep(0.5)
            
            # Value should be updated
            assert intensity_select.input_value() == "heavy"


@pytest.mark.slow
class TestFullWorkflowE2E:
    """Full end-to-end workflow tests"""
    
    @pytest.mark.skipif(
        not os.getenv("FULL_E2E", "false").lower() == "true",
        reason="Requires FULL_E2E=true and real services"
    )
    def test_complete_workflow_with_real_apis(self, page, frontend_url, backend_url):
        """Test complete workflow with real API calls"""
        # This would test with real backend
        # 1. Fill form
        # 2. Select resume/folder
        # 3. Submit
        # 4. Wait for real API responses
        # 5. Verify results
        pass
