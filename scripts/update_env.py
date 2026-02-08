#!/usr/bin/env python3
"""
Script to update .env file with new configuration options.
Safely adds missing configs without overwriting existing values.
"""

import os
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# New configs to add (organized by category)
NEW_CONFIGS = {
    "# Resume Validation Configuration": [
        "RESUME_MIN_WORDS=200",
        "RESUME_MAX_WORDS=1000",
        "RESUME_RECOMMENDED_MIN_WORDS=300",
        "RESUME_RECOMMENDED_MAX_WORDS=800",
    ],
    "# ATS (Applicant Tracking System) Scoring Configuration": [
        "ATS_MIN_SCORE=70",
        "ATS_TABLE_PENALTY=20",
        "ATS_MISSING_SECTIONS_PENALTY=15",
        "ATS_SHORT_PENALTY=10",
        "ATS_LONG_PENALTY=5",
        "ATS_MISSING_HEADER_PENALTY=10",
        "ATS_MISSING_CONTACT_PENALTY=10",
    ],
    "# Resume Parser Configuration": [
        "RESUME_HEADER_MAX_LINES=8",
        "# RESUME_SECTION_MAPPINGS={\"Professional Experience\": \"Work Experience\"}",
    ],
    "# Resume Tailoring Configuration": [
        "TAILORING_INTENSITY_DEFAULT=medium",
        "TAILORING_ALLOWED_INTENSITIES=light,medium,heavy",
    ],
    "# Approval Workflow Configuration": [
        "APPROVAL_TIMEOUT_SECONDS=3600",
        "APPROVAL_STORAGE_BACKEND=memory",
    ],
    "# API Configuration": [
        "API_CORS_ORIGINS=http://localhost:3000,http://localhost:5173",
        "API_MAX_REQUEST_SIZE=10485760",
    ],
}

# Also include existing configs that might be missing
EXISTING_CONFIGS = {
    "# LLM Service Configuration": [
        "LLM_CACHE_SIZE=100",
        "LLM_MAX_RETRIES=3",
        "LLM_RETRY_DELAY=1.0",
    ],
    "# Job Description Extraction Configuration": [
        "JD_EXTRACTION_TIMEOUT=10",
        "JD_EXTRACTION_MAX_RETRIES=3",
        "JD_TEXT_LIMIT=8000",
    ],
    "# Google API Configuration": [
        "GOOGLE_API_TIMEOUT=60",
    ],
}


def get_existing_keys(env_file: Path) -> set:
    """Extract existing config keys from .env file"""
    existing = set()
    if not env_file.exists():
        return existing
    
    try:
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                # Extract key (before =)
                if '=' in line:
                    key = line.split('=')[0].strip()
                    existing.add(key)
    except Exception as e:
        print(f"⚠️  Warning: Could not read {env_file}: {e}")
    
    return existing


def add_configs_to_env(env_file: Path, configs: dict, existing_keys: set):
    """Add missing configs to .env file"""
    added = []
    skipped = []
    
    try:
        # Read existing content
        content = ""
        if env_file.exists():
            with open(env_file, 'r') as f:
                content = f.read()
        
        # Check if we need to add anything
        needs_update = False
        new_content = []
        
        # Add existing content
        if content:
            new_content.append(content.rstrip())
            if not content.endswith('\n'):
                new_content.append('')
        
        # Add new configs
        for category, config_list in configs.items():
            category_added = False
            for config in config_list:
                # Skip comments
                if config.startswith('#'):
                    if category not in [line for line in new_content if line.startswith(category)]:
                        if not category_added:
                            new_content.append('')
                            new_content.append(category)
                            category_added = True
                        new_content.append(config)
                    continue
                
                # Extract key
                key = config.split('=')[0].strip()
                
                if key not in existing_keys:
                    if not category_added:
                        new_content.append('')
                        new_content.append(category)
                        category_added = True
                    new_content.append(config)
                    added.append(key)
                    needs_update = True
                else:
                    skipped.append(key)
        
        # Write updated content
        if needs_update:
            with open(env_file, 'w') as f:
                f.write('\n'.join(new_content))
                if not new_content[-1].endswith('\n'):
                    f.write('\n')
            return added, skipped
        else:
            return [], skipped
    
    except PermissionError:
        print(f"❌ Permission denied: Cannot write to {env_file}")
        print(f"   Please run this script with appropriate permissions, or manually update .env")
        return None, None
    except Exception as e:
        print(f"❌ Error updating {env_file}: {e}")
        return None, None


def main():
    print("🔧 Updating .env file with new configuration options...")
    print()
    
    # Check if .env exists
    if not ENV_FILE.exists():
        print(f"⚠️  {ENV_FILE} does not exist.")
        if ENV_EXAMPLE.exists():
            print(f"   Found {ENV_EXAMPLE}")
            print(f"   Copy it first: cp {ENV_EXAMPLE} {ENV_FILE}")
            print(f"   Then run this script again.")
        else:
            print(f"   Creating new .env file...")
            # Create empty .env file
            try:
                ENV_FILE.touch()
            except PermissionError:
                print(f"❌ Permission denied: Cannot create {ENV_FILE}")
                return
        print()
    
    # Get existing keys
    existing_keys = get_existing_keys(ENV_FILE)
    print(f"📋 Found {len(existing_keys)} existing configuration keys")
    print()
    
    # Merge all configs
    all_configs = {**EXISTING_CONFIGS, **NEW_CONFIGS}
    
    # Add missing configs
    added, skipped = add_configs_to_env(ENV_FILE, all_configs, existing_keys)
    
    if added is None:
        # Error occurred
        return
    
    # Report results
    if added:
        print(f"✅ Added {len(added)} new configuration options:")
        for key in added:
            print(f"   + {key}")
        print()
    
    if skipped:
        print(f"⏭️  Skipped {len(skipped)} existing configuration options:")
        for key in skipped[:10]:  # Show first 10
            print(f"   - {key}")
        if len(skipped) > 10:
            print(f"   ... and {len(skipped) - 10} more")
        print()
    
    if not added and not skipped:
        print("✅ All configuration options are already present in .env")
    elif added:
        print(f"✅ .env file updated successfully!")
        print(f"   Review and adjust values in {ENV_FILE} as needed")


if __name__ == "__main__":
    main()
