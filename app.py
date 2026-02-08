#!/usr/bin/env python3
"""
Resume Agent - Web UI
Streamlit-based web interface for resume tailoring and job application management.
"""

import streamlit as st
import sys
from pathlib import Path
from typing import Optional
import traceback
import time

# Import resume_agent early to apply warning filters
import resume_agent

from resume_agent.config import RESUME_DOC_ID, GOOGLE_FOLDER_ID, settings
from resume_agent.agents.jd_extractor import extract_clean_jd
from resume_agent.storage.google_docs import get_services
from resume_agent.tracking.application_tracker import (
    update_application_status, list_applications,
    get_statistics, search_applications, get_application
)
from resume_agent.services.llm_service import LLMService
from resume_agent.services.resume_workflow import (
    ResumeWorkflowService, 
    TailorResumeRequest, 
    TailorResumeResult,
    WorkflowStep
)
# Use multi-agent workflow if available
try:
    from resume_agent.services.multi_agent_workflow import MultiAgentWorkflowService
    USE_MULTI_AGENT = True
except ImportError:
    MultiAgentWorkflowService = ResumeWorkflowService
    USE_MULTI_AGENT = False
from resume_agent.utils.exceptions import ResumeAgentError, ConfigError, LLMError, GoogleAPIError
from resume_agent.utils.logger import logger

# Page configuration
st.set_page_config(
    page_title="Resume Agent",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        margin-bottom: 1rem;
    }
    .success-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
        margin: 1rem 0;
    }
    .error-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #f8d7da;
        border: 1px solid #f5c6cb;
        color: #721c24;
        margin: 1rem 0;
    }
    .info-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d1ecf1;
        border: 1px solid #bee5eb;
        color: #0c5460;
        margin: 1rem 0;
    }
    .metric-card {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #1f77b4;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'llm_service' not in st.session_state:
    try:
        st.session_state.llm_service = LLMService()
    except Exception as e:
        st.session_state.llm_service = None
        st.session_state.llm_error = str(e)

if 'google_services' not in st.session_state:
    try:
        st.session_state.google_services = get_services()
    except Exception as e:
        st.session_state.google_services = None
        st.session_state.google_error = str(e)

if 'resume_text' not in st.session_state:
    st.session_state.resume_text = None

# Helper functions
def get_workflow_service():
    """Get or create workflow service (uses MultiAgentWorkflowService if available)"""
    if 'workflow_service' not in st.session_state:
        # Use multi-agent workflow for better results
        if USE_MULTI_AGENT:
            st.session_state.workflow_service = MultiAgentWorkflowService(
                llm_service=st.session_state.llm_service,
                google_services=st.session_state.google_services
            )
        else:
            st.session_state.workflow_service = ResumeWorkflowService(
                llm_service=st.session_state.llm_service,
                google_services=st.session_state.google_services
            )
    return st.session_state.workflow_service

def display_error(error: Exception):
    """Display error with fix instructions"""
    error_msg = str(error)
    fix_instructions = getattr(error, 'fix_instructions', None)
    
    st.error(f"**Error:** {error_msg}")
    if fix_instructions:
        st.info(f"**How to fix:**\n{fix_instructions}")

# Sidebar
with st.sidebar:
    st.title("📄 Resume Agent")
    st.markdown("---")
    
    # Configuration status
    st.subheader("Configuration")
    
    # LLM Provider
    llm_status = "✅" if st.session_state.llm_service else "❌"
    st.write(f"{llm_status} LLM Provider: {settings.llm_provider.upper() if st.session_state.llm_service else 'Not configured'}")
    
    if st.session_state.llm_service:
        model_name = st.session_state.llm_service.provider.get_model_name()
        st.caption(f"Model: {model_name}")
    
    # Google Services
    google_status = "✅" if st.session_state.google_services else "❌"
    st.write(f"{google_status} Google Services: {'Connected' if st.session_state.google_services else 'Not connected'}")
    
    if not st.session_state.google_services:
        if 'google_error' in st.session_state:
            st.error(st.session_state.google_error)
    
    st.markdown("---")
    
    # Navigation
    st.subheader("Navigation")
    page = st.radio(
        "Select Page",
        ["🏠 Home", "🎯 Evaluate Fit", "✂️ Tailor Resume", "📊 Applications", "⚙️ Settings"],
        label_visibility="collapsed",
        key="nav_radio"
    )

# Main content based on page selection
if page == "🏠 Home":
    st.markdown('<div class="main-header">📄 Resume Agent</div>', unsafe_allow_html=True)
    st.markdown("""
    **AI-powered resume tailoring and job application assistant**
    
    Use this tool to:
    - 🎯 **Evaluate** how well your resume matches a job description
    - ✂️ **Tailor** your resume to specific job postings
    - 📊 **Track** your job applications
    """)
    
    # Quick stats
    try:
        stats = get_statistics()
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Applications", stats.get('total', 0))
        with col2:
            st.metric("Active", stats.get('active', 0))
        with col3:
            st.metric("Average Fit Score", f"{stats.get('avg_fit_score', 0):.1f}")
        with col4:
            st.metric("Interviews", stats.get('interview', 0))
    except Exception as e:
        st.info("Application tracking not available. Start tracking applications to see statistics.")
    
    # Quick actions
    st.markdown("---")
    st.subheader("Quick Actions")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🎯 Evaluate Job Fit", use_container_width=True):
            st.session_state.nav_radio = "🎯 Evaluate Fit"
            st.rerun()
    
    with col2:
        if st.button("✂️ Tailor Resume", use_container_width=True):
            st.session_state.nav_radio = "✂️ Tailor Resume"
            st.rerun()

elif page == "🎯 Evaluate Fit":
    st.title("🎯 Evaluate Job Fit")
    st.markdown("Analyze how well your resume matches a job description")
    
    # Check prerequisites
    if not st.session_state.llm_service:
        st.error("LLM service not available. Please check your configuration.")
        if 'llm_error' in st.session_state:
            st.error(st.session_state.llm_error)
        st.stop()
    
    if not st.session_state.google_services:
        st.error("Google services not available. Please authenticate.")
        st.stop()
    
    # Input method
    input_method = st.radio(
        "Job Description Source",
        ["📄 URL", "📝 Paste Text"],
        horizontal=True
    )
    
    jd_text = None
    job_url = None
    
    if input_method == "📄 URL":
        job_url = st.text_input("Job Listing URL", placeholder="https://linkedin.com/jobs/view/...")
        if job_url:
            with st.spinner("Extracting job description..."):
                try:
                    jd_text = extract_clean_jd(job_url, st.session_state.llm_service)
                    st.success("Job description extracted successfully!")
                    with st.expander("View extracted job description"):
                        st.text_area("Job Description", jd_text, height=200, label_visibility="collapsed", key="view_jd_eval")
                except Exception as e:
                    display_error(e)
                    st.stop()
    else:
        jd_text = st.text_area("Paste Job Description", height=200)
    
    if st.button("Evaluate Fit", type="primary", use_container_width=True) and jd_text:
        # Load resume and evaluate
        workflow_service = get_workflow_service()
        
        with st.spinner("Loading resume from Google Docs..."):
            resume_text, error = workflow_service.load_resume()
            if error:
                st.error(error)
                st.stop()
        
        # Evaluate
        with st.spinner("Evaluating fit with AI..."):
            try:
                evaluation = workflow_service.evaluate_fit(resume_text, jd_text)
                
                # Display results
                st.markdown("---")
                st.subheader("📊 Evaluation Results")
                
                # Score with visual indicator
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    score_color = "green" if evaluation.score >= 7 else "orange" if evaluation.score >= 5 else "red"
                    st.markdown(f"""
                    <div style="text-align: center; padding: 2rem;">
                        <h2 style="color: {score_color}; font-size: 4rem; margin: 0;">{evaluation.score}/10</h2>
                        <p style="font-size: 1.2rem; margin-top: 0.5rem;">Fit Score</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                # Should apply
                apply_color = "green" if evaluation.should_apply else "red"
                apply_text = "✅ Yes, apply!" if evaluation.should_apply else "❌ Not recommended"
                st.markdown(f'<div class="info-box"><strong>Should Apply:</strong> <span style="color: {apply_color};">{apply_text}</span></div>', unsafe_allow_html=True)
                
                # Matching areas
                if evaluation.matching_areas:
                    st.subheader("✅ Matching Areas")
                    for area in evaluation.matching_areas:
                        st.success(f"• {area}")
                
                # Missing areas
                if evaluation.missing_areas:
                    st.subheader("❌ Missing or Unclear Areas")
                    for area in evaluation.missing_areas:
                        st.warning(f"• {area}")
                
                # Recommendations
                if evaluation.recommendations:
                    st.subheader("💡 Recommendations")
                    for rec in evaluation.recommendations:
                        st.info(f"• {rec}")
                
                # Reasoning
                if hasattr(evaluation, 'reasoning') and evaluation.reasoning:
                    with st.expander("📝 Detailed Reasoning"):
                        st.write(evaluation.reasoning)
                
            except Exception as e:
                display_error(e)

elif page == "✂️ Tailor Resume":
    st.title("✂️ Tailor Resume")
    st.markdown("Customize your resume to match a specific job description")
    
    # Check prerequisites - use conditional rendering instead of st.stop()
    if not st.session_state.llm_service:
        st.error("LLM service not available. Please check your configuration.")
        if 'llm_error' in st.session_state:
            st.error(st.session_state.llm_error)
    elif not st.session_state.google_services:
        st.error("Google services not available. Please authenticate.")
    else:
        # Only render the rest if prerequisites are met
        # Job details
        col1, col2 = st.columns(2)
        with col1:
            company = st.text_input("Company Name", placeholder="Google")
        with col2:
            job_title = st.text_input("Job Title", placeholder="Senior Software Engineer")
        
        # Job description input
        input_method = st.radio(
            "Job Description Source",
            ["📄 URL", "📝 Paste Text"],
            horizontal=True
        )
        
        jd_text = None
        job_url = None
        
        if input_method == "📄 URL":
            job_url = st.text_input("Job Listing URL", placeholder="https://linkedin.com/jobs/view/...")
            if job_url:
                with st.spinner("Extracting job description..."):
                    try:
                        jd_text = extract_clean_jd(job_url, st.session_state.llm_service)
                        st.success("Job description extracted successfully!")
                        with st.expander("View extracted job description"):
                            st.text_area("Job Description", jd_text, height=200, label_visibility="collapsed", key="view_jd_tailor")
                    except Exception as e:
                        display_error(e)
                        # Don't use st.stop() - just show error and continue
        else:
            jd_text = st.text_area("Paste Job Description", height=200)
        
        # Options
        evaluate_first = st.checkbox("Evaluate fit before tailoring", value=True)
        track_application = st.checkbox("Track this application", value=True)
        
        # Error display area
        if 'tailor_error' in st.session_state:
            st.error(f"❌ Error: {st.session_state.tailor_error}")
            if st.button("Clear Error", key="clear_tailor_error"):
                del st.session_state.tailor_error
        
        # Validate inputs before showing button
        can_proceed = jd_text and company and job_title
        
        # Initialize session state for tailoring process
        if 'tailoring_in_progress' not in st.session_state:
            st.session_state.tailoring_in_progress = False
        if 'tailoring_complete' not in st.session_state:
            st.session_state.tailoring_complete = False
        
        # Initialize button variable
        tailor_button = False
        
        if not can_proceed:
            st.info("⚠️ Please fill in all fields (Company, Job Title, and Job Description) to proceed.")
            st.button("Tailor Resume", type="primary", use_container_width=True, disabled=True, key="tailor_button_disabled")
            # Reset state if fields are not ready
            st.session_state.tailoring_in_progress = False
            st.session_state.tailoring_complete = False
        else:
            tailor_button = st.button("Tailor Resume", type="primary", use_container_width=True, key="tailor_button_active")
            if tailor_button:
                # Button was clicked - start the process
                st.session_state.tailoring_in_progress = True
                st.session_state.tailoring_complete = False
                # Clear any previous result and request
                if 'tailor_result' in st.session_state:
                    del st.session_state.tailor_result
                if 'tailor_request' in st.session_state:
                    del st.session_state.tailor_request
        
        # Check if we should show the tailoring process
        if st.session_state.tailoring_in_progress or tailor_button:
            logger.info("Tailor Resume button clicked", company=company, job_title=job_title, jd_length=len(jd_text) if jd_text else 0)
            
            # Clear any previous errors
            if 'tailor_error' in st.session_state:
                del st.session_state.tailor_error
            
            # Get workflow service
            workflow_service = get_workflow_service()
            
            # Step 1: Evaluate (optional)
            evaluation = None
            if evaluate_first:
                st.markdown("---")
                st.subheader("Step 1: Evaluating Fit")
                with st.spinner("Loading resume and evaluating fit..."):
                    try:
                        # Load resume first
                        resume_text, error = workflow_service.load_resume()
                        if error:
                            st.error(f"❌ {error}")
                            st.session_state.tailoring_in_progress = False
                        else:
                            # Evaluate fit
                            evaluation = workflow_service.evaluate_fit(resume_text, jd_text)
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.metric("Fit Score", f"{evaluation.score}/10")
                            with col2:
                                st.metric("Should Apply", "✅ Yes" if evaluation.should_apply else "❌ No")
                            
                            if not evaluation.should_apply and evaluation.score < 5:
                                st.warning("⚠️ Low fit score. Consider if you want to proceed with tailoring.")
                                continue_anyway = st.button("Continue Anyway", key="continue_low_fit")
                                if not continue_anyway:
                                    st.info("⏸️ Tailoring paused. Click 'Continue Anyway' to proceed.")
                                    st.session_state.tailoring_in_progress = False
                                    # Don't proceed with tailoring
                                    evaluation = None  # Mark that we should skip
                    except Exception as e:
                        display_error(e)
                        st.error(f"❌ Evaluation failed: {e}")
                        continue_anyway = st.button("Continue Anyway", key="continue_after_error")
                        if not continue_anyway:
                            st.info("⏸️ Tailoring paused. Click 'Continue Anyway' to proceed.")
                            st.session_state.tailoring_in_progress = False
                            # Don't proceed with tailoring
                            evaluation = None  # Mark that we should skip
            
            # Step 2 & 3: Tailor and Save (using workflow service with progressive updates)
            # Only proceed if evaluation passed or was skipped
            if evaluation is not None or not evaluate_first:
                st.markdown("---")
                st.subheader("Step 2: Tailoring Resume")
                
                # Check if we already have a completed result in session state
                if 'tailor_result' in st.session_state and st.session_state.tailor_result:
                    result = st.session_state.tailor_result
                    if result.current_step == WorkflowStep.COMPLETED:
                        # Show completed results
                        logger.info("Displaying completed results from session state")
                    elif result.current_step == WorkflowStep.ERROR:
                        # Show error
                        st.error(f"❌ {result.error}")
                        st.session_state.tailoring_in_progress = False
                        result = None
                    else:
                        # Workflow in progress - continue from where we left off
                        result = None  # Will re-execute
                else:
                    result = None
                
                # Execute workflow with progressive updates - one step per render
                if result is None or result.current_step != WorkflowStep.COMPLETED:
                    try:
                        # Create request (store in session state if not already there)
                        if 'tailor_request' not in st.session_state:
                            st.session_state.tailor_request = TailorResumeRequest(
                                company=company,
                                job_title=job_title,
                                jd_text=jd_text,
                                job_url=job_url,
                                evaluate_first=False,  # Already done if needed
                                track_application=track_application
                            )
                        request = st.session_state.tailor_request
                        
                        # Initialize or get current result
                        if result is None:
                            result = TailorResumeResult(current_step=WorkflowStep.LOADING_RESUME)
                            st.session_state.tailor_result = result
                        
                        # Define workflow steps in order
                        steps = [
                            (WorkflowStep.LOADING_RESUME, "📥 Loading resume from Google Docs..."),
                            (WorkflowStep.TAILORING_RESUME, "✂️ Tailoring resume with AI..."),
                            (WorkflowStep.SAVING_TO_GOOGLE, "💾 Saving to Google Docs..."),
                            (WorkflowStep.GENERATING_DIFF, "📝 Generating change log..."),
                        ]
                        
                        if track_application:
                            steps.append((WorkflowStep.TRACKING_APPLICATION, "📊 Tracking application..."))
                        
                        # Find where we are in the workflow
                        current_step = result.current_step
                        start_index = 0
                        for i, (step, _) in enumerate(steps):
                            if step == current_step:
                                start_index = i
                                break
                        
                        # Create progress container
                        progress_container = st.empty()
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        # Execute remaining steps sequentially with progress updates
                        total_steps = len(steps) - start_index
                        for i, (step, description) in enumerate(steps[start_index:], start=start_index):
                            # Update progress
                            progress = (i - start_index + 1) / total_steps
                            progress_bar.progress(progress)
                            status_text.info(f"**{description}**")
                            
                            logger.info(f"Executing workflow step: {step.value}")
                            result = workflow_service.execute_workflow_step(request, step, result)
                            
                            if result.error:
                                status_text.error(f"❌ **Error:** {result.error}")
                                result.current_step = WorkflowStep.ERROR
                                st.session_state.tailor_result = result
                                break
                            else:
                                # Determine next step
                                if i + 1 < len(steps):
                                    result.current_step = steps[i + 1][0]
                                else:
                                    result.current_step = WorkflowStep.COMPLETED
                                
                                status_text.success(f"✅ {description.replace('...', ' completed')}")
                                st.session_state.tailor_result = result
                                
                                # If this was the last step, we're done
                                if result.current_step == WorkflowStep.COMPLETED:
                                    progress_bar.progress(1.0)
                                    break
                        
                        # Clear progress indicators
                        progress_bar.empty()
                        status_text.empty()
                        progress_container.empty()
                        
                        logger.info("Workflow execution completed", step=result.current_step.value)
                        
                    except Exception as e:
                        error_msg = str(e)
                        error_traceback = traceback.format_exc()
                        logger.error(f"Tailor resume workflow error: {error_msg}", exc_info=True)
                        st.session_state.tailor_error = error_msg
                        display_error(e)
                        st.error(f"❌ **Error occurred:** {error_msg}")
                        with st.expander("🔍 View Full Error Details"):
                            st.code(error_traceback, language="python")
                        st.session_state.tailoring_in_progress = False
                        result = None
                
                # Render results
                if result and result.current_step == WorkflowStep.COMPLETED and not result.error:
                    logger.info("Rendering success UI")
                    # Success - show results
                    st.success("✅ Resume tailored successfully!")
                    
                    # Show tailored resume
                    st.markdown("---")
                    st.subheader("📄 Tailored Resume")
                    if result.tailored_resume:
                        display_text = result.tailored_resume[:10000] if len(result.tailored_resume) > 10000 else result.tailored_resume
                        if len(result.tailored_resume) > 10000:
                            st.info(f"📄 Showing first 10,000 characters of {len(result.tailored_resume)} total characters")
                        st.text_area("Tailored Resume", display_text, height=400, label_visibility="collapsed", key="tailored_resume_view")
                    
                    # Show Google Docs link
                    if result.doc_url:
                        st.markdown("---")
                        st.subheader("Step 3: Saved to Google Docs")
                        st.success(f"✅ Saved to Google Docs!")
                        st.markdown(f"[📄 Open in Google Docs]({result.doc_url})")
                        
                        if result.diff_path:
                            st.info(f"📝 Change log saved to: `{result.diff_path}`")
                        
                        if result.application_id:
                            st.success(f"✅ Application tracked (ID: {result.application_id})")
                    
                    # Mark complete
                    st.session_state.tailoring_complete = True
                    st.session_state.tailoring_in_progress = False
                elif result and result.error:
                    st.error(f"❌ {result.error}")
                    st.session_state.tailoring_in_progress = False

elif page == "📊 Applications":
    st.title("📊 Application Tracker")
    st.markdown("View and manage your job applications")
    
    # Search and filter
    col1, col2 = st.columns(2)
    with col1:
        search_query = st.text_input("🔍 Search", placeholder="Company name or job title...")
    with col2:
        status_filter = st.selectbox("Filter by Status", ["All", "Applied", "Interview", "Rejected", "Offer", "Withdrawn"])
    
    # Load applications
    try:
        if search_query:
            applications = search_applications(search_query)
        else:
            applications = list_applications()
        
        if status_filter != "All":
            applications = [app for app in applications if app.get('status', '').lower() == status_filter.lower()]
        
        if not applications:
            st.info("No applications found.")
        else:
            st.write(f"**Found {len(applications)} application(s)**")
            
            # Display applications
            for app in applications:
                with st.expander(f"**{app['job_title']}** at {app['company']} - {app.get('status', 'Applied')}"):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Fit Score", f"{app.get('fit_score', 'N/A')}/10")
                    with col2:
                        st.write(f"**Status:** {app.get('status', 'Applied')}")
                    with col3:
                        st.write(f"**Date:** {app.get('application_date', 'N/A')}")
                    
                    if app.get('job_url'):
                        st.markdown(f"[🔗 Job Posting]({app['job_url']})")
                    
                    if app.get('resume_doc_id'):
                        doc_url = f"https://docs.google.com/document/d/{app['resume_doc_id']}"
                        st.markdown(f"[📄 Tailored Resume]({doc_url})")
                    
                    # Update status
                    new_status = st.selectbox(
                        "Update Status",
                        ["Applied", "Interview", "Rejected", "Offer", "Withdrawn"],
                        index=["Applied", "Interview", "Rejected", "Offer", "Withdrawn"].index(app.get('status', 'Applied')) if app.get('status') in ["Applied", "Interview", "Rejected", "Offer", "Withdrawn"] else 0,
                        key=f"status_{app['id']}"
                    )
                    if new_status != app.get('status'):
                        if st.button("Update", key=f"update_{app['id']}"):
                            try:
                                update_application_status(app['id'], new_status)
                                st.success("Status updated!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to update: {e}")
            
            # Statistics
            st.markdown("---")
            st.subheader("📈 Statistics")
            try:
                stats = get_statistics()
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total", stats.get('total', 0))
                with col2:
                    st.metric("Active", stats.get('active', 0))
                with col3:
                    st.metric("Avg Fit Score", f"{stats.get('avg_fit_score', 0):.1f}")
                with col4:
                    st.metric("Interviews", stats.get('interview', 0))
            except Exception as e:
                st.warning(f"Could not load statistics: {e}")
    
    except Exception as e:
        st.error(f"Failed to load applications: {e}")

elif page == "⚙️ Settings":
    st.title("⚙️ Settings")
    st.markdown("Configure your Resume Agent")
    
    # LLM Settings
    st.subheader("🤖 LLM Configuration")
    st.write(f"**Provider:** {settings.llm_provider.upper()}")
    if st.session_state.llm_service:
        st.write(f"**Model:** {st.session_state.llm_service.provider.get_model_name()}")
        st.success("✅ LLM service is configured and ready")
    else:
        st.error("❌ LLM service not configured")
        if 'llm_error' in st.session_state:
            st.error(st.session_state.llm_error)
    
    # Google Settings
    st.subheader("📁 Google Services")
    if st.session_state.google_services:
        st.success("✅ Google services connected")
        st.write(f"**Resume Doc ID:** {RESUME_DOC_ID or 'Not set'}")
        st.write(f"**Folder ID:** {GOOGLE_FOLDER_ID or 'Not set'}")
        
        if st.button("🔄 Reload Resume"):
            workflow_service = get_workflow_service()
            resume_text, error = workflow_service.load_resume()
            if error:
                st.error(error)
            else:
                st.success(f"✅ Loaded {len(resume_text)} characters")
                st.session_state.resume_text = resume_text
    else:
        st.error("❌ Google services not connected")
        if 'google_error' in st.session_state:
            st.error(st.session_state.google_error)
        st.info("Run: `python -m resume_agent.storage.google_auth` to authenticate")
    
    # Configuration info
    st.markdown("---")
    st.subheader("📋 Configuration Details")
    with st.expander("View all settings"):
        st.json({
            "llm_provider": settings.llm_provider,
            "llm_model": getattr(settings, f"{settings.llm_provider}_model", "N/A"),
            "google_folder_id": GOOGLE_FOLDER_ID,
            "resume_doc_id": RESUME_DOC_ID,
        })

if __name__ == "__main__":
    # This is handled by Streamlit
    pass
