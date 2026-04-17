#!/usr/bin/env python3
"""
Resume Agent - Main CLI Entry Point

A unified interface for resume tailoring, job fit evaluation, and application tracking.
"""

import argparse
import sys

# Import resume_agent early to apply warning filters
import resume_agent

from resume_agent.config import RESUME_DOC_ID, GOOGLE_FOLDER_ID, settings
from resume_agent.agents.resume_tailor import tailor_resume_for_job
from resume_agent.agents.fit_evaluator import evaluate_resume_fit
from resume_agent.agents.jd_extractor import extract_clean_jd
from resume_agent.storage.google_docs import read_google_doc, write_to_google_doc
from resume_agent.storage.google_drive import get_subfolder_id_for_job, copy_doc_to_folder
from resume_agent.utils.diff import generate_diff_markdown
from resume_agent.tracking.application_tracker import (
    add_application, update_application_status, list_applications,
    get_statistics, search_applications, get_application
)
from resume_agent.services.llm_service import LLMService
from resume_agent.services.resume_versioning import ResumeVersionService
from resume_agent.utils.logger import logger
from resume_agent.utils.exceptions import ResumeAgentError, ConfigError
from resume_agent.utils.progress import track_operation, print_success, print_error, print_info, print_table
from resume_agent.models.resume import Resume, JobDescription
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

def _require_google_services():
    """Google Drive/Docs require the web app (sign in with Google). CLI cannot use file-based auth."""
    console.print("[yellow]Google Drive/Docs are available only via the web app.[/yellow]")
    console.print("Sign in with Google at the Resume Agent web app to tailor, save, and use Drive.")
    raise SystemExit(1)

def cmd_evaluate(args):
    """Evaluate resume fit for a job."""
    logger.info("Starting fit evaluation")
    console.print(Panel.fit("🎯 Evaluating Job Fit", style="bold blue"))
    
    try:
        with track_operation("Initializing services"):
            llm_service = LLMService()  # Uses provider from settings
            try:
                from resume_agent.storage.google_docs import get_services
                _, docs_service = get_services()
            except Exception:
                _require_google_services()
        
        # Load resume
        with track_operation("Loading resume from Google Docs"):
            logger.info("Loading resume from Google Docs")
            resume_text = read_google_doc(docs_service, RESUME_DOC_ID)
        
        # Extract JD from URL or use provided text
        if args.url:
            console.print(f"\n[cyan]📄 Extracting job description from:[/cyan] {args.url}")
            logger.info("Extracting job description from URL", url=args.url)
            jd_text = extract_clean_jd(args.url, llm_service)
        elif args.jd_file:
            logger.info("Loading job description from file", file=args.jd_file)
            with open(args.jd_file, 'r') as f:
                jd_text = f.read()
        else:
            logger.error("No job description source provided")
            print_error("Must provide either --url or --jd-file")
            return 1
        
        # Evaluate fit using structured output
        with track_operation("Evaluating fit with AI"):
            logger.info("Evaluating fit")
            evaluation = evaluate_resume_fit(llm_service, resume_text, jd_text)
        
        # Display results with Rich
        console.print("\n")
        console.print(Panel(evaluation.to_display_string(), title="Fit Evaluation Result", border_style="green"))
        
        return 0
        
    except ResumeAgentError as e:
        logger.error("Evaluation failed", error=e)
        print_error(f"Evaluation failed: {e}")
        return 1

def cmd_tailor(args):
    """Tailor resume for a job."""
    logger.info("Starting resume tailoring")
    console.print(Panel.fit("✂️ Tailoring Resume", style="bold magenta"))
    
    try:
        with track_operation("Initializing services"):
            llm_service = LLMService()  # Uses provider from settings
            try:
                from resume_agent.storage.google_docs import get_services
                _, docs_service = get_services()
            except Exception:
                _require_google_services()
            version_service = ResumeVersionService()
        
        # Get job details
        if args.url:
            console.print(f"\n[cyan]📄 Extracting job description from:[/cyan] {args.url}")
            logger.info("Extracting job description", url=args.url)
            jd_text = extract_clean_jd(args.url, llm_service)
            # Try to extract company/title from URL or prompt user
            company = args.company or console.input("[yellow]Company name:[/yellow] ").strip()
            job_title = args.title or console.input("[yellow]Job title:[/yellow] ").strip()
        elif args.jd_file:
            logger.info("Loading job description from file", file=args.jd_file)
            with open(args.jd_file, 'r') as f:
                jd_text = f.read()
            company = args.company or console.input("[yellow]Company name:[/yellow] ").strip()
            job_title = args.title or console.input("[yellow]Job title:[/yellow] ").strip()
        else:
            print_error("Must provide either --url or --jd-file")
            return 1
        
        # Create folder and copy resume
        with track_operation(f"Creating folder for {company} - {job_title}"):
            subfolder_id = get_subfolder_id_for_job(GOOGLE_FOLDER_ID, job_title, company)
            tailored_doc_id = copy_doc_to_folder(
                RESUME_DOC_ID, subfolder_id, f"{job_title}_Tailored"
            )
        
        # Read original resume
        with track_operation("Reading original resume"):
            resume_text = read_google_doc(docs_service, tailored_doc_id)
            original_resume = Resume(
                content=resume_text,
                doc_id=RESUME_DOC_ID,
                source="google_docs"
            )
        
        # Save original version
        job_desc = JobDescription(
            title=job_title,
            company=company,
            url=args.url,
            content=jd_text
        )
        version_service.save_version(original_resume, job=job_desc, notes="Original resume")
        
        # Tailor resume
        with track_operation("Tailoring resume with AI"):
            tailored_resume_text = tailor_resume_for_job(resume_text, jd_text, llm_service)
        
        # Save tailored version
        tailored_resume = Resume(
            content=tailored_resume_text,
            doc_id=tailored_doc_id,
            source="google_docs"
        )
        version_service.save_version(
            tailored_resume,
            job=job_desc,
            notes="Tailored resume"
        )
        
        # Generate diff
        with track_operation("Generating change log"):
            generate_diff_markdown(resume_text, tailored_resume_text, job_title, company)
        
        # Write tailored resume
        with track_operation("Writing tailored resume to Google Docs"):
            write_to_google_doc(tailored_doc_id, tailored_resume_text)
        
        print_success(f"Tailored resume created!")
        console.print(f"[cyan]📄 Resume:[/cyan] https://docs.google.com/document/d/{tailored_doc_id}")
        
        # Optionally track application
        if args.track:
            fit_score = None
            if args.evaluate_first:
                console.print("\n[yellow]🎯 Evaluating fit first...[/yellow]")
                evaluation = evaluate_resume_fit(llm_service, resume_text, jd_text)
                fit_score = evaluation.score if hasattr(evaluation, 'score') else None
            
            app_id = add_application(
                job_title=job_title,
                company=company,
                job_url=args.url,
                fit_score=fit_score,
                resume_doc_id=tailored_doc_id
            )
            print_success(f"Application tracked (ID: {app_id})")
        
        return 0
        
    except ResumeAgentError as e:
        logger.error("Tailoring failed", error=e)
        print_error(f"Tailoring failed: {e}")
        return 1

def cmd_apply(args):
    """Full workflow: evaluate + tailor + track."""
    logger.info("Starting full application workflow")
    print("🚀 Starting full application workflow...\n")
    
    llm_service = LLMService()  # Uses provider from settings
    try:
        from resume_agent.storage.google_docs import get_services
        _, docs_service = get_services()
    except Exception:
        _require_google_services()
    
    # Get JD
    if args.url:
        print(f"📄 Extracting job description from: {args.url}")
        logger.info("Extracting job description", url=args.url)
        jd_text = extract_clean_jd(args.url, llm_service)
        company = args.company or input("Company name: ").strip()
        job_title = args.title or input("Job title: ").strip()
    else:
        print("❌ Error: Must provide --url")
        return 1
    
    # Load resume
    resume_text = read_google_doc(docs_service, RESUME_DOC_ID)
    
    # Step 1: Evaluate fit
    print("\n" + "="*60)
    print("STEP 1: Evaluating Fit")
    print("="*60)
    logger.info("Evaluating fit")
    fit_evaluation = evaluate_resume_fit(llm_service, resume_text, jd_text)
    
    # Display structured evaluation
    if hasattr(fit_evaluation, 'to_display_string'):
        print(fit_evaluation.to_display_string())
        fit_score = fit_evaluation.score
    else:
        # Fallback for text output
        print(fit_evaluation)
        fit_score = None
    
    # Step 2: Tailor resume
    print("\n" + "="*60)
    print("STEP 2: Tailoring Resume")
    print("="*60)
    subfolder_id = get_subfolder_id_for_job(GOOGLE_FOLDER_ID, job_title, company)
    tailored_doc_id = copy_doc_to_folder(
        RESUME_DOC_ID, subfolder_id, f"{job_title}_Tailored"
    )
    
    tailored_resume = tailor_resume_for_job(resume_text, jd_text, llm_service)
    generate_diff_markdown(resume_text, tailored_resume, job_title, company)
    write_to_google_doc(tailored_doc_id, tailored_resume)
    
    print(f"✅ Tailored resume: https://docs.google.com/document/d/{tailored_doc_id}")
    
    # Step 3: Track application
    print("\n" + "="*60)
    print("STEP 3: Tracking Application")
    print("="*60)
    app_id = add_application(
        job_title=job_title,
        company=company,
        job_url=args.url,
        fit_score=fit_score,
        resume_doc_id=tailored_doc_id,
        status="prepared"
    )
    logger.info("Application tracked", app_id=app_id, company=company, job_title=job_title)
    print(f"✅ Application tracked (ID: {app_id})")
    
    return 0

def cmd_list(args):
    """List tracked applications."""
    apps = list_applications(status=args.status, limit=args.limit)
    
    if not apps:
        console.print("[yellow]No applications found.[/yellow]")
        return 0
    
    # Create Rich table
    table = Table(title=f"Applications ({len(apps)} total)", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim")
    table.add_column("Company", style="cyan")
    table.add_column("Job Title", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Fit Score", justify="right")
    table.add_column("Applied", style="dim")
    
    status_style = {
        "prepared": "[yellow]📝 Prepared[/yellow]",
        "applied": "[green]✅ Applied[/green]",
        "interview": "[blue]🎯 Interview[/blue]",
        "rejected": "[red]❌ Rejected[/red]",
        "offer": "[bold green]🎉 Offer[/bold green]"
    }
    
    for app in apps:
        status_display = status_style.get(app["status"], f"📋 {app['status']}")
        fit_score = f"{app['fit_score']}/10" if app['fit_score'] else "N/A"
        applied_date = app['application_date'][:10] if app['application_date'] else "N/A"
        
        table.add_row(
            str(app['id']),
            app['company'],
            app['job_title'],
            status_display,
            fit_score,
            applied_date
        )
    
    console.print("\n")
    console.print(table)
    
    if args.status:
        console.print(f"\n[dim]Filtered by status: {args.status}[/dim]")
    
    return 0

def cmd_stats(args):
    """Show application statistics."""
    stats = get_statistics()
    
    console.print("\n")
    console.print(Panel.fit("📊 Application Statistics", style="bold blue"))
    
    # Summary table
    summary_table = Table(show_header=False, box=None)
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="green")
    
    summary_table.add_row("Total Applications", str(stats['total_applications']))
    avg_score = f"{stats['average_fit_score']:.1f}" if stats['average_fit_score'] else "N/A"
    summary_table.add_row("Average Fit Score", avg_score)
    
    console.print(summary_table)
    
    # Status breakdown
    if stats['by_status']:
        console.print("\n[bold]By Status:[/bold]")
        status_table = Table(show_header=True, header_style="bold yellow")
        status_table.add_column("Status", style="cyan")
        status_table.add_column("Count", justify="right", style="green")
        
        for status, count in sorted(stats['by_status'].items()):
            status_table.add_row(status, str(count))
        
        console.print(status_table)
    
    console.print()

def cmd_search(args):
    """Search applications."""
    apps = search_applications(args.query)
    
    if not apps:
        print(f"No applications found matching '{args.query}'")
        return 0
    
    print(f"\n🔍 Found {len(apps)} applications matching '{args.query}':\n")
    for app in apps:
        print(f"[{app['id']}] {app['company']} - {app['job_title']}")
        print(f"   Status: {app['status']} | Applied: {app['application_date']}\n")

def validate_config():
    """Validate configuration before starting"""
    errors = []
    
    if not settings.google_folder_id:
        errors.append("GOOGLE_FOLDER_ID not set in .env")
    if not settings.resume_doc_id:
        errors.append("RESUME_DOC_ID not set in .env")
    
    if errors:
        raise ConfigError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))


def main():
    # Validate configuration
    try:
        validate_config()
    except ConfigError as e:
        print(f"❌ Configuration Error:\n{e}")
        print("\nRun 'python setup_env.py' to configure your environment.")
        return 1
    
    parser = argparse.ArgumentParser(
        description="Resume Agent - AI-powered resume tailoring and job application assistant"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate resume fit for a job")
    eval_parser.add_argument("--url", help="Job listing URL")
    eval_parser.add_argument("--jd-file", help="Path to job description file")
    eval_parser.set_defaults(func=cmd_evaluate)
    
    # Tailor command
    tailor_parser = subparsers.add_parser("tailor", help="Tailor resume for a job")
    tailor_parser.add_argument("--url", help="Job listing URL")
    tailor_parser.add_argument("--jd-file", help="Path to job description file")
    tailor_parser.add_argument("--company", help="Company name")
    tailor_parser.add_argument("--title", help="Job title")
    tailor_parser.add_argument("--track", action="store_true", help="Track this application")
    tailor_parser.add_argument("--evaluate-first", action="store_true", help="Evaluate fit before tailoring")
    tailor_parser.set_defaults(func=cmd_tailor)
    
    # Apply command (full workflow)
    apply_parser = subparsers.add_parser("apply", help="Full workflow: evaluate + tailor + track")
    apply_parser.add_argument("--url", required=True, help="Job listing URL")
    apply_parser.add_argument("--company", help="Company name")
    apply_parser.add_argument("--title", help="Job title")
    apply_parser.set_defaults(func=cmd_apply)
    
    # List command
    list_parser = subparsers.add_parser("list", help="List tracked applications")
    list_parser.add_argument("--status", help="Filter by status")
    list_parser.add_argument("--limit", type=int, default=50, help="Limit results")
    list_parser.set_defaults(func=cmd_list)
    
    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show application statistics")
    stats_parser.set_defaults(func=cmd_stats)
    
    # Search command
    search_parser = subparsers.add_parser("search", help="Search applications")
    search_parser.add_argument("query", help="Search query")
    search_parser.set_defaults(func=cmd_search)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
