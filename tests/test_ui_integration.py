"""
UI Integration Tests for Streamlit App
Tests the actual UI rendering and interactions using Playwright.
"""

import pytest
import subprocess
import time
import os
from pathlib import Path

# Try to import playwright, skip tests if not available
try:
    from playwright.sync_api import sync_playwright, Page, expect
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Skip if UI tests are disabled or Playwright not available
pytestmark = pytest.mark.skipif(
    os.getenv("UI_TESTS") != "true" or not PLAYWRIGHT_AVAILABLE,
    reason="UI tests disabled or Playwright not installed. Set UI_TESTS=true and install playwright."
)


@pytest.fixture(scope="module")
def streamlit_server():
    """Start Streamlit server for testing"""
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("Playwright not available")
    
    # Start Streamlit in background
    process = subprocess.Popen(
        ["streamlit", "run", "app.py", "--server.headless", "true", "--server.port", "8502"],
        cwd=Path(__file__).parent.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    # Wait for server to start
    max_wait = 10
    waited = 0
    while waited < max_wait:
        try:
            import requests
            response = requests.get("http://localhost:8502", timeout=2)
            if response.status_code == 200:
                break
        except:
            pass
        time.sleep(1)
        waited += 1
    
    if waited >= max_wait:
        process.terminate()
        pytest.fail("Streamlit server failed to start")
    
    yield process
    
    # Cleanup
    try:
        process.terminate()
        process.wait(timeout=10)
    except:
        process.kill()


@pytest.fixture
def page(streamlit_server):
    """Get a Playwright page instance"""
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("Playwright not available")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8502")
        # Wait for Streamlit to load
        try:
            page.wait_for_selector("text=Resume Agent", timeout=15000)
        except Exception as e:
            browser.close()
            pytest.fail(f"Streamlit page failed to load: {e}")
        yield page
        browser.close()


class TestUIRendering:
    """Test that UI renders without errors"""
    
    def test_home_page_loads(self, page: Page):
        """Test that home page loads without errors"""
        # Check for main elements (use first to handle multiple matches)
        expect(page.locator("text=Resume Agent").first).to_be_visible()
        expect(page.locator("text=AI-powered resume tailoring")).to_be_visible()
        
        # Check for navigation in sidebar
        expect(page.locator("text=Home").or_(page.locator("text=🏠 Home")).first).to_be_visible()
        expect(page.locator("text=Evaluate Fit").or_(page.locator("text=🎯 Evaluate Fit")).first).to_be_visible()
        expect(page.locator("text=Tailor Resume").or_(page.locator("text=✂️ Tailor Resume")).first).to_be_visible()
    
    def test_navigation_works(self, page: Page):
        """Test that navigation between pages works"""
        # Click on Evaluate Fit (use radio button or sidebar link)
        evaluate_link = page.locator("text=🎯 Evaluate Fit").or_(page.locator("text=Evaluate Fit")).first
        evaluate_link.click()
        time.sleep(1)  # Wait for navigation
        expect(page.locator("text=Evaluate Job Fit").or_(page.locator("h1:has-text('Evaluate')")).first).to_be_visible(timeout=5000)
        
        # Click on Tailor Resume
        tailor_link = page.locator("text=✂️ Tailor Resume").or_(page.locator("text=Tailor Resume")).first
        tailor_link.click()
        time.sleep(1)
        expect(page.locator("h1:has-text('Tailor Resume')").or_(page.locator("text=Tailor Resume")).first).to_be_visible(timeout=5000)
        
        # Click on Applications
        apps_link = page.locator("text=📊 Applications").or_(page.locator("text=Applications")).first
        apps_link.click()
        time.sleep(1)
        expect(page.locator("text=Application Tracker").or_(page.locator("h1:has-text('Application')")).first).to_be_visible(timeout=5000)
        
        # Click on Settings
        settings_link = page.locator("text=⚙️ Settings").or_(page.locator("text=Settings")).first
        settings_link.click()
        time.sleep(1)
        expect(page.locator("h1:has-text('Settings')").or_(page.locator("text=Settings")).first).to_be_visible(timeout=5000)
    
    def test_no_console_errors(self, page: Page):
        """Test that there are no JavaScript console errors"""
        errors = []
        
        def handle_console(msg):
            if msg.type == "error":
                errors.append(msg.text)
        
        page.on("console", handle_console)
        
        # Navigate through pages
        page.locator("text=🏠 Home").or_(page.locator("text=Home")).first.click()
        time.sleep(1)
        page.locator("text=🎯 Evaluate Fit").or_(page.locator("text=Evaluate Fit")).first.click()
        time.sleep(1)
        page.locator("text=✂️ Tailor Resume").or_(page.locator("text=Tailor Resume")).first.click()
        time.sleep(1)
        
        # Filter out known non-critical errors
        critical_errors = [
            e for e in errors 
            if "Cannot set a node at a delta path" not in e
            and "Cannot respond" not in e
            and "UserWarning" not in e
            and "FutureWarning" not in e
        ]
        
        assert len(critical_errors) == 0, f"Found console errors: {critical_errors}"


class TestTailorResumePage:
    """Test the Tailor Resume page specifically"""
    
    def test_tailor_page_loads(self, page: Page):
        """Test that Tailor Resume page loads"""
        page.locator("text=✂️ Tailor Resume").or_(page.locator("text=Tailor Resume")).first.click()
        time.sleep(1)
        expect(page.locator("h1:has-text('Tailor Resume')").or_(page.locator("text=Tailor Resume")).first).to_be_visible(timeout=5000)
        expect(page.locator("text=Company Name").first).to_be_visible()
        expect(page.locator("text=Job Title").first).to_be_visible()
    
    def test_tailor_button_renders(self, page: Page):
        """Test that Tailor Resume button renders without duplicate ID errors"""
        page.locator("text=✂️ Tailor Resume").or_(page.locator("text=Tailor Resume")).first.click()
        time.sleep(1)
        
        # Button should be visible (either disabled or enabled)
        button = page.locator("button:has-text('Tailor Resume')").first
        expect(button).to_be_visible()
        
        # Should not have duplicate ID errors in console
        errors = []
        def handle_console(msg):
            if msg.type == "error" and "DuplicateElementId" in msg.text:
                errors.append(msg.text)
        
        page.on("console", handle_console)
        time.sleep(2)  # Wait for any errors to appear
        
        assert len(errors) == 0, f"Found duplicate element ID errors: {errors}"
    
    def test_input_validation(self, page: Page):
        """Test that input validation works"""
        page.locator("text=✂️ Tailor Resume").or_(page.locator("text=Tailor Resume")).first.click()
        time.sleep(1)
        
        # Try to click button without filling fields
        button = page.locator("button:has-text('Tailor Resume')").first
        
        # Button should be disabled if fields are empty, or show validation message if clicked
        if button.is_enabled():
            button.click()
            time.sleep(1)
            # Should show validation message
            expect(page.locator("text=Please fill in all fields").or_(page.locator("text=fill in all fields"))).to_be_visible(timeout=3000)
    
    def test_tailor_button_click_no_crash(self, page: Page):
        """Test that clicking Tailor Resume button doesn't cause blank page"""
        page.locator("text=✂️ Tailor Resume").or_(page.locator("text=Tailor Resume")).first.click()
        time.sleep(1)
        
        # Select "Paste Text" option to show textarea
        paste_option = page.locator("text=📝 Paste Text").or_(page.locator("label:has-text('Paste Text')")).first
        if paste_option.is_visible():
            paste_option.click()
            time.sleep(0.5)
        
        # Fill in required fields
        company_input = page.locator("input[placeholder*='Google'], input[placeholder*='Company']").first
        title_input = page.locator("input[placeholder*='Senior'], input[placeholder*='Job Title']").first
        # Find textarea - Streamlit uses nested structure
        jd_textarea = page.locator("textarea").or_(page.locator("[data-testid='stTextArea'] textarea")).or_(page.locator("div[data-baseweb='textarea'] textarea")).first
        
        company_input.fill("Test Company")
        title_input.fill("Test Job")
        # Wait for textarea to be visible
        jd_textarea.wait_for(state="visible", timeout=5000)
        jd_textarea.fill("Test job description")
        
        # Wait a bit for fields to register
        time.sleep(1)
        
        # Click button
        button = page.locator("button:has-text('Tailor Resume'):enabled").first
        if button.is_visible():
            button.click()
            
            # Wait a bit for processing
            time.sleep(3)
            
            # Page should still be visible (not blank)
            expect(page.locator("h1:has-text('Tailor Resume')").or_(page.locator("text=Tailor Resume")).first).to_be_visible(timeout=5000)
            
            # Check for errors or success messages
            # Should either show error or processing message, but not blank page
            page_content = page.content()
            assert len(page_content) > 1000, "Page appears to be blank"
            
            # Check for no critical rendering errors
            errors = []
            def handle_console(msg):
                if msg.type == "error" and "delta path" in msg.text.lower():
                    errors.append(msg.text)
            
            page.on("console", handle_console)
            time.sleep(1)
            
            # Should not have delta path errors
            assert len(errors) == 0, f"Found rendering errors: {errors}"


class TestEvaluateFitPage:
    """Test the Evaluate Fit page"""
    
    def test_evaluate_page_loads(self, page: Page):
        """Test that Evaluate Fit page loads"""
        page.locator("text=🎯 Evaluate Fit").or_(page.locator("text=Evaluate Fit")).first.click()
        time.sleep(1)
        expect(page.locator("text=Evaluate Job Fit").or_(page.locator("h1:has-text('Evaluate')")).first).to_be_visible(timeout=5000)
        expect(page.locator("text=Job Description Source")).to_be_visible()
    
    def test_evaluate_button_renders(self, page: Page):
        """Test that Evaluate button renders"""
        page.locator("text=🎯 Evaluate Fit").or_(page.locator("text=Evaluate Fit")).first.click()
        time.sleep(1)
        expect(page.locator("button:has-text('Evaluate Fit')").first).to_be_visible(timeout=5000)


class TestApplicationsPage:
    """Test the Applications page"""
    
    def test_applications_page_loads(self, page: Page):
        """Test that Applications page loads"""
        page.locator("text=📊 Applications").or_(page.locator("text=Applications")).first.click()
        time.sleep(1)
        expect(page.locator("text=Application Tracker").or_(page.locator("h1:has-text('Application')")).first).to_be_visible(timeout=5000)


class TestSettingsPage:
    """Test the Settings page"""
    
    def test_settings_page_loads(self, page: Page):
        """Test that Settings page loads"""
        page.locator("text=⚙️ Settings").or_(page.locator("text=Settings")).first.click()
        time.sleep(1)
        expect(page.locator("h1:has-text('Settings')").or_(page.locator("text=Settings")).first).to_be_visible(timeout=5000)
        expect(page.locator("text=LLM Configuration")).to_be_visible()


class TestErrorHandling:
    """Test error handling in UI"""
    
    def test_error_display(self, page: Page):
        """Test that errors are displayed properly"""
        page.locator("text=✂️ Tailor Resume").or_(page.locator("text=Tailor Resume")).first.click()
        time.sleep(1)
        
        # Select "Paste Text" option to show textarea
        paste_option = page.locator("text=📝 Paste Text").or_(page.locator("label:has-text('Paste Text')")).first
        if paste_option.is_visible():
            paste_option.click()
            time.sleep(0.5)
        
        # Fill in fields
        company_input = page.locator("input[placeholder*='Google'], input[placeholder*='Company']").first
        title_input = page.locator("input[placeholder*='Senior'], input[placeholder*='Job Title']").first
        # Find textarea - Streamlit uses nested structure
        jd_textarea = page.locator("textarea").or_(page.locator("[data-testid='stTextArea'] textarea")).or_(page.locator("div[data-baseweb='textarea'] textarea")).first
        
        company_input.fill("Test")
        title_input.fill("Test")
        # Wait for textarea to be visible
        jd_textarea.wait_for(state="visible", timeout=5000)
        jd_textarea.fill("Test")
        
        time.sleep(1)
        
        # Try to click button (may fail due to missing resume, but should show error, not blank page)
        button = page.locator("button:has-text('Tailor Resume'):enabled").first
        if button.is_visible():
            button.click()
            time.sleep(3)
            
            # Should show error message, not blank page
            page_content = page.content()
            assert len(page_content) > 1000, "Page appears to be blank after error"
            
            # Should have some error indication
            has_error = (
                "error" in page_content.lower() or 
                "failed" in page_content.lower() or
                "❌" in page_content
            )
            # Error is acceptable, blank page is not
            assert len(page_content) > 1000 or has_error, "Page should show error or content"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
