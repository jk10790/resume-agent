import React, { useState, useEffect, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ResumeComparison, { diffLines } from './ResumeComparison'
import FeedbackLearner from './FeedbackLearner'
import './TailorResume.css'

function TailorResume() {
  const [company, setCompany] = useState('')
  const [jobTitle, setJobTitle] = useState('')
  const [jdText, setJdText] = useState('')
  const [jobUrl, setJobUrl] = useState('')
  const [inputMethod, setInputMethod] = useState('url') // 'url' or 'text'
  const [evaluateFirst, setEvaluateFirst] = useState(true)
  const [trackApplication, setTrackApplication] = useState(true)
  
  // Resume and folder selection
  const [resumeDocId, setResumeDocId] = useState(null) // null = no selection
  const [saveFolderId, setSaveFolderId] = useState(null) // null = no selection
  const [selectedResumeName, setSelectedResumeName] = useState(null)
  const [selectedFolderName, setSelectedFolderName] = useState(null)
  const [availableResumes, setAvailableResumes] = useState([])
  const [availableFolders, setAvailableFolders] = useState([])
  const [loadingResumes, setLoadingResumes] = useState(false)
  const [loadingFolders, setLoadingFolders] = useState(false)
  // Check localStorage first to determine initial state
  const getInitialResumeSelectorState = () => {
    const lastResumeId = localStorage.getItem('resume_agent_last_resume_id');
    return !lastResumeId; // Show selector only if no saved selection
  };
  
  const getInitialFolderSelectorState = () => {
    const lastFolderId = localStorage.getItem('resume_agent_last_folder_id');
    return !lastFolderId; // Show selector only if no saved selection
  };
  
  const [showResumeSelector, setShowResumeSelector] = useState(getInitialResumeSelectorState())
  const [showFolderSelector, setShowFolderSelector] = useState(getInitialFolderSelectorState())
  
  const [progress, setProgress] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [llmAcknowledgment, setLlmAcknowledgment] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isExtracting, setIsExtracting] = useState(false)
  const [fetchedResumeContent, setFetchedResumeContent] = useState('')
  const [showComparison, setShowComparison] = useState(false)
  
  // Approval workflow states
  const [approvalId, setApprovalId] = useState(null)
  const [approvalRequired, setApprovalRequired] = useState(false)
  const [refinementFeedback, setRefinementFeedback] = useState('')
  const [showInlineDiff, setShowInlineDiff] = useState(true) // Show diff by default
  
  // Search/filter states
  const [resumeSearchQuery, setResumeSearchQuery] = useState('')
  const [folderSearchQuery, setFolderSearchQuery] = useState('')
  
  // Authentication state
  const [isAuthenticated, setIsAuthenticated] = useState(false) // Single declaration - no duplicates
  
  // Skill extraction and confirmation state
  const [skillsExtracted, setSkillsExtracted] = useState(false)
  const [extractedSkills, setExtractedSkills] = useState([])
  const [confirmedSkills, setConfirmedSkills] = useState([])
  const [showSkillConfirmation, setShowSkillConfirmation] = useState(false)
  const [extractingSkills, setExtractingSkills] = useState(false)

  // Resume quality analysis state
  const [qualityReport, setQualityReport] = useState(null)
  const [analyzingQuality, setAnalyzingQuality] = useState(false)
  const [showQualityReport, setShowQualityReport] = useState(false)
  const [qualityAnswers, setQualityAnswers] = useState({})  // User answers to clarifying questions
  const [editedImprovedResume, setEditedImprovedResume] = useState('')  // Editable improved resume
  const [savingImprovedResume, setSavingImprovedResume] = useState(false)
  const [previewMode, setPreviewMode] = useState('preview')  // 'edit' or 'preview'

  const analyzeResumeQuality = async () => {
    if (!resumeDocId) {
      setError('Please select a resume first')
      return
    }
    
    setAnalyzingQuality(true)
    setError(null)
    
    try {
      // Check for cached improved resume first
      const cached = await loadCachedResume()
      if (cached && cached.text) {
        // Ask user if they want to continue with cached version
        const useCached = window.confirm(
          `Found a cached improved resume (Score: ${cached.score}, Version: ${cached.version}).\n\n` +
          `Last updated: ${new Date(cached.updated_at).toLocaleString()}\n\n` +
          `Click OK to continue editing it, or Cancel to start fresh from your original resume.`
        )
        
        if (useCached) {
          // Analyze the cached version
          const response = await fetch('/api/analyze-resume-quality', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
              resume_text: cached.text,
              improve: false
            })
          })
          
          if (response.ok) {
            const data = await response.json()
            setQualityReport({
              ...data,
              cached_version: cached.version,
              cached_updated_at: cached.updated_at
            })
            setEditedImprovedResume(cached.text)
            setShowQualityReport(true)
            setAnalyzingQuality(false)
            return
          }
        }
      }
      
      // Analyze original resume from Google Drive
      const response = await fetch('/api/analyze-resume-quality', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          resume_doc_id: resumeDocId,
          improve: false
        })
      })
      
      if (!response.ok) {
        throw new Error(`Failed to analyze quality: ${response.statusText}`)
      }
      
      const data = await response.json()
      setQualityReport(data)
      setEditedImprovedResume('')  // Reset to start fresh
      setShowQualityReport(true)
    } catch (err) {
      console.error('Quality analysis error:', err)
      setError(`Failed to analyze resume quality: ${err.message}`)
    } finally {
      setAnalyzingQuality(false)
    }
  }

  const improveResume = async () => {
    // Use edited improved resume if available, otherwise need doc_id
    if (!editedImprovedResume && !resumeDocId) {
      setError('Please select a resume first')
      return
    }
    
    setAnalyzingQuality(true)
    setError(null)
    
    try {
      // If we already have an improved resume being edited, use that text
      // Otherwise fetch from Google Drive
      const requestBody = editedImprovedResume 
        ? {
            resume_text: editedImprovedResume,  // Use the current edited text
            improve: true,
            user_answers: qualityAnswers
          }
        : {
            resume_doc_id: resumeDocId,
            improve: true,
            user_answers: qualityAnswers
          }
      
      const response = await fetch('/api/analyze-resume-quality', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(requestBody)
      })
      
      if (!response.ok) {
        throw new Error(`Failed to improve resume: ${response.statusText}`)
      }
      
      const data = await response.json()
      setQualityReport(data)
      setShowQualityReport(true)
      // Set the improved resume for editing (backend auto-caches it)
      if (data.improved_resume) {
        setEditedImprovedResume(data.improved_resume)
      }
    } catch (err) {
      console.error('Resume improvement error:', err)
      setError(`Failed to improve resume: ${err.message}`)
    } finally {
      setAnalyzingQuality(false)
    }
  }

  // Load cached improved resume from backend
  const loadCachedResume = async () => {
    if (!resumeDocId) return null
    try {
      const response = await fetch(`/api/cached-improved-resume?doc_id=${resumeDocId}`, {
        credentials: 'include'
      })
      if (response.ok) {
        const data = await response.json()
        if (data.found && data.resume) {
          return data.resume
        }
      }
    } catch (err) {
      console.log('No cached resume:', err)
    }
    return null
  }

  const updateQualityAnswer = (questionId, value) => {
    setQualityAnswers(prev => ({
      ...prev,
      [questionId]: value
    }))
  }

  // Re-check quality of the improved resume
  const recheckQuality = async () => {
    if (!editedImprovedResume) return
    
    setAnalyzingQuality(true)
    
    try {
      const response = await fetch('/api/analyze-resume-quality', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          resume_text: editedImprovedResume,  // Use the edited text
          resume_doc_id: resumeDocId,  // Pass doc_id so backend can update cache
          improve: false
        })
      })
      
      if (!response.ok) {
        throw new Error(`Failed to analyze: ${response.statusText}`)
      }
      
      const data = await response.json()
      setQualityReport(data)
      setQualityAnswers({})  // Reset answers for new round
    } catch (err) {
      console.error('Re-check quality error:', err)
      setError(`Failed to re-check quality: ${err.message}`)
    } finally {
      setAnalyzingQuality(false)
    }
  }

  // Save improved resume to Google Drive
  const saveImprovedResumeToDrive = async () => {
    if (!editedImprovedResume) {
      alert('❌ No improved resume to save. Please improve the resume first.')
      return
    }
    
    // Use selected folder or root
    const targetFolderId = saveFolderId || 'root'
    const folderName = saveFolderId 
      ? (availableFolders.find(f => f.id === saveFolderId)?.name || 'Selected folder')
      : 'Google Drive Root'
    
    setSavingImprovedResume(true)
    
    try {
      const response = await fetch('/api/save-improved-resume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          resume_text: editedImprovedResume,
          folder_id: targetFolderId,
          filename: `Improved_Resume_${new Date().toISOString().split('T')[0]}`
        })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to save')
      }
      
      const data = await response.json()
      alert(`✅ Resume saved successfully!\n\nFile: ${data.filename}\nFolder: ${folderName}\n\nClick OK to open in Google Docs`)
      
      // Open the doc in a new tab
      if (data.doc_url) {
        window.open(data.doc_url, '_blank')
      }
      
      setShowQualityReport(false)
    } catch (err) {
      console.error('Save improved resume error:', err)
      alert(`❌ Failed to save: ${err.message}`)
    } finally {
      setSavingImprovedResume(false)
    }
  }

  const handleExtractJD = async () => {
    if (!jobUrl) {
      setError('Please enter a job URL')
      return
    }

    setIsExtracting(true)
    setError(null)

    try {
      const response = await fetch('/api/extract-jd', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ job_url: jobUrl })
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`)
      }

      const data = await response.json()
      setJdText(data.jd_text)
      setInputMethod('text') // Switch to text view after extraction
    } catch (err) {
      setError(err.message)
    } finally {
      setIsExtracting(false)
    }
  }

  // Load available resumes and folders
  useEffect(() => {
    const loadResumes = async () => {
      setLoadingResumes(true);
      try {
        const response = await fetch('/api/google-docs?max_results=20', {
          credentials: 'include'
        });
        if (response.ok) {
          const data = await response.json();
          setAvailableResumes(data.docs || []);
        } else if (response.status === 401) {
          // Not authenticated - clear lists
          setAvailableResumes([]);
        }
      } catch (err) {
        console.error('Failed to load resumes:', err);
      } finally {
        setLoadingResumes(false);
      }
    };

    const loadFolders = async () => {
      setLoadingFolders(true);
      try {
        const response = await fetch('/api/google-folders?max_results=20', {
          credentials: 'include'
        });
        if (response.ok) {
          const data = await response.json();
          setAvailableFolders(data.folders || []);
        } else if (response.status === 401) {
          // Not authenticated - clear lists
          setAvailableFolders([]);
        }
      } catch (err) {
        console.error('Failed to load folders:', err);
      } finally {
        setLoadingFolders(false);
      }
    };

    // Check auth status and load if authenticated
    const checkAndLoad = async () => {
      try {
        const response = await fetch('/api/auth/google/status', {
          credentials: 'include'
        });
        const data = await response.json();
        setIsAuthenticated(data.authenticated);
        
        if (data.authenticated) {
          loadResumes();
          loadFolders();
        } else {
          setAvailableResumes([]);
          setAvailableFolders([]);
        }
      } catch (err) {
        console.error('Failed to check auth status:', err);
        setIsAuthenticated(false);
        setAvailableResumes([]);
        setAvailableFolders([]);
      }
    };

    checkAndLoad();
  }, []);

  // Load last used selections after resumes/folders are loaded
  useEffect(() => {
    if (availableResumes.length > 0 || availableFolders.length > 0) {
      // Load last used selections from localStorage
      const lastResumeId = localStorage.getItem('resume_agent_last_resume_id');
      const lastResumeName = localStorage.getItem('resume_agent_last_resume_name');
      const lastFolderId = localStorage.getItem('resume_agent_last_folder_id');
      const lastFolderName = localStorage.getItem('resume_agent_last_folder_name');
      
      // Only set if the resume/folder still exists in available list
      if (lastResumeId && lastResumeName) {
        const resumeExists = availableResumes.find(d => d.id === lastResumeId);
        if (resumeExists) {
          setResumeDocId(lastResumeId);
          setSelectedResumeName(lastResumeName);
          setShowResumeSelector(false); // Hide selector if we have a valid last selection
          
          // Check if skills were already extracted for this resume
          const skillsExtractedForResume = localStorage.getItem(`resume_agent_skills_extracted_${lastResumeId}`);
          if (skillsExtractedForResume === 'true') {
            setSkillsExtracted(true);
          }
        } else {
          // Clear invalid stored selection
          localStorage.removeItem('resume_agent_last_resume_id');
          localStorage.removeItem('resume_agent_last_resume_name');
        }
      }
      
      if (lastFolderId && lastFolderName) {
        const folderExists = availableFolders.find(f => f.id === lastFolderId);
        if (folderExists) {
          setSaveFolderId(lastFolderId);
          setSelectedFolderName(lastFolderName);
          setShowFolderSelector(false); // Hide selector if we have a valid last selection
        } else {
          // Clear invalid stored selection
          localStorage.removeItem('resume_agent_last_folder_id');
          localStorage.removeItem('resume_agent_last_folder_name');
        }
      }
    }
  }, [availableResumes, availableFolders]);
  
  // Load confirmed skills on mount
  useEffect(() => {
    const loadConfirmedSkills = async () => {
      try {
        const response = await fetch('/api/user/skills', {
          credentials: 'include'
        });
        if (response.ok) {
          const data = await response.json();
          if (data.skills && data.skills.length > 0) {
            setConfirmedSkills(data.skills);
            setSkillsExtracted(true);
          }
        }
      } catch (err) {
        console.error('Failed to load confirmed skills:', err);
      }
    };
    
    if (isAuthenticated) {
      loadConfirmedSkills();
    }
  }, [isAuthenticated]);

  const extractSkills = async () => {
    if (!resumeDocId) {
      setError('Please select a resume first')
      return
    }
    
    setExtractingSkills(true)
    setError(null)
    
    try {
      // Extract skills
      const response = await fetch('/api/resume/extract-skills', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          doc_id: resumeDocId
        })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to extract skills')
      }
      
      const data = await response.json()
      setExtractedSkills(data.skills.all_skills || [])
      setConfirmedSkills(data.skills.all_skills || []) // Pre-populate with extracted skills
      setSkillsExtracted(true)
      setShowSkillConfirmation(true)
      
      // Also run a quick quality check in background
      runQuickQualityCheck()
    } catch (err) {
      setError(err.message)
    } finally {
      setExtractingSkills(false)
    }
  }
  
  // Quick quality check to warn about issues before tailoring
  const [originalQualityWarning, setOriginalQualityWarning] = useState(null)
  
  const runQuickQualityCheck = async () => {
    if (!resumeDocId) return
    
    try {
      const response = await fetch('/api/analyze-resume-quality', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          resume_doc_id: resumeDocId,
          improve: false
        })
      })
      
      if (response.ok) {
        const data = await response.json()
        // Only show warning if score is below 70
        if (data.overall_score < 70) {
          setOriginalQualityWarning({
            score: data.overall_score,
            atsScore: data.ats_score,
            metricsCount: data.metrics_count,
            priority: data.improvement_priority?.slice(0, 3) || [],
            hasQuestions: data.questions && data.questions.length > 0
          })
        } else {
          setOriginalQualityWarning(null)
        }
      }
    } catch (err) {
      console.log('Quality check skipped:', err.message)
    }
  }
  
  const confirmSkills = async () => {
    if (confirmedSkills.length === 0) {
      setError('Please select at least one skill')
      return
    }
    
    try {
      // Use bulk update endpoint (much more efficient than individual calls)
      const response = await fetch('/api/user/skills', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({ skills: confirmedSkills })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to save skills')
      }
      
      const data = await response.json()
      setConfirmedSkills(data.skills || confirmedSkills)
      
      // Mark skills as extracted for this resume
      if (resumeDocId) {
        localStorage.setItem(`resume_agent_skills_extracted_${resumeDocId}`, 'true')
      }
      
      setShowSkillConfirmation(false)
      setSkillsExtracted(true)
      setError(null)
    } catch (err) {
      setError(`Failed to save skills: ${err.message}`)
    }
  }
  
  const resetSkills = async () => {
    if (!window.confirm('Are you sure you want to reset all skills? This will clear your confirmed skills list.')) {
      return
    }
    
    try {
      const response = await fetch('/api/user/skills', {
        method: 'DELETE',
        credentials: 'include'
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to reset skills')
      }
      
      setConfirmedSkills([])
      setExtractedSkills([])
      setSkillsExtracted(false)
      
      // Clear localStorage markers for all resumes
      Object.keys(localStorage).forEach(key => {
        if (key.startsWith('resume_agent_skills_extracted_')) {
          localStorage.removeItem(key)
        }
      })
      
      setError(null)
    } catch (err) {
      setError(`Failed to reset skills: ${err.message}`)
    }
  }
  
  const updateSkill = async (oldSkill, newSkill) => {
    if (!newSkill.trim()) {
      return false
    }
    
    try {
      const response = await fetch(`/api/user/skills/${encodeURIComponent(oldSkill)}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({ new_skill: newSkill.trim() })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to update skill')
      }
      
      const data = await response.json()
      setConfirmedSkills(data.skills || [])
      return true
    } catch (err) {
      setError(`Failed to update skill: ${err.message}`)
      return false
    }
  }

  const handleTailor = async () => {
    // Validation: need company, job title, and either URL or JD text
    if (!company || !jobTitle) {
      setError('Please fill in Company Name and Job Title')
      return
    }

    if (!jobUrl && !jdText.trim()) {
      setError('Please provide either a Job Listing URL or paste the Job Description')
      return
    }

    // If URL provided but no JD text, extract it first
    let finalJdText = jdText
    if (jobUrl && !jdText.trim()) {
      setIsLoading(true)
      setError(null)
      setProgress({
        currentStep: 0,
        totalSteps: 1,
        message: 'Extracting job description from URL...',
        progress: 0
      })

      try {
        const extractResponse = await fetch('/api/extract-jd', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ job_url: jobUrl })
        })

        if (!extractResponse.ok) {
          const errorData = await extractResponse.json()
          throw new Error(errorData.detail || 'Failed to extract job description')
        }

        const extractData = await extractResponse.json()
        finalJdText = extractData.jd_text
      } catch (err) {
        setError(`Failed to extract job description: ${err.message}`)
        setIsLoading(false)
        return
      }
    }

    // Validation: require resume and folder selection
    if (!resumeDocId) {
      setError('Please select a resume to tailor')
      return
    }

    // Note: saveFolderId is optional - backend will use configured default folder if not provided
    
    // Check if skills have been extracted and confirmed
    if (!skillsExtracted && resumeDocId) {
      // Auto-extract skills first, then show confirmation modal
      await extractSkills()
      // Don't proceed until user confirms skills in the modal
      return
    }
    
    if (!skillsExtracted) {
      setError('Please extract and confirm your skills before tailoring')
      return
    }

    setIsLoading(true)
    setError(null)
    setResult(null) // Clear previous result
    setProgress({
      currentStep: 0,
      totalSteps: 0,
      message: 'Starting...',
      progress: 0
    })

    try {
      const response = await fetch('/api/tailor-resume', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
                  company,
                  job_title: jobTitle,
                  jd_text: finalJdText,
                  job_url: jobUrl || null,
                  evaluate_first: evaluateFirst,
                  track_application: trackApplication,
                  resume_doc_id: resumeDocId || null,
                  save_folder_id: saveFolderId || null
                })
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = JSON.parse(line.slice(6))
            
            if (data.type === 'error') {
              setError(data.error)
              setIsLoading(false)
              return
            }
            
            if (data.type === 'step_start' || data.type === 'step_complete') {
              setProgress({
                currentStep: data.step_number,
                totalSteps: data.total_steps,
                message: data.message,
                progress: data.progress,
                step: data.step
              })
            }
            
            if (data.type === 'poor_fit_stopped') {
              console.log('Received poor_fit_stopped event:', data)
              
              // Show the poor fit result
              const evaluation = data.evaluation || {}
              const parsedResume = data.parsed_resume || {}
              const analyzedJd = data.analyzed_jd || {}
              
              // Build detailed message
              let message = `## ⚠️ Workflow Stopped: Poor Job Fit\n\n`
              message += `**Fit Score:** ${evaluation.score || 'N/A'}/10\n\n`
              
              if (evaluation.matching_areas && evaluation.matching_areas.length > 0) {
                message += `### ✅ Matching Skills\n${evaluation.matching_areas.map(s => `- ${s}`).join('\n')}\n\n`
              }
              
              if (evaluation.missing_areas && evaluation.missing_areas.length > 0) {
                message += `### ❌ Missing Required Skills\n${evaluation.missing_areas.map(s => `- ${s}`).join('\n')}\n\n`
              }
              
              if (evaluation.recommendations && evaluation.recommendations.length > 0) {
                message += `### 💡 Recommendations\n${evaluation.recommendations.map(r => `- ${r}`).join('\n')}\n\n`
              }
              
              if (analyzedJd.required_skills && analyzedJd.required_skills.length > 0) {
                message += `### 📋 JD Required Skills\n${analyzedJd.required_skills.slice(0, 10).map(s => `- ${s}`).join('\n')}\n\n`
              }
              
              if (parsedResume.all_skills && parsedResume.all_skills.length > 0) {
                message += `### 📝 Your Skills\n${parsedResume.all_skills.slice(0, 15).map(s => `- ${s}`).join('\n')}\n`
              }
              
              // Set result to display the evaluation
              setResult({
                poor_fit_stopped: true,
                evaluation: evaluation,
                parsed_resume: parsedResume,
                analyzed_jd: analyzedJd,
                fit_message: message
              })
              
              setProgress({
                currentStep: data.step_number || 3,
                totalSteps: data.total_steps || 6,
                message: '⚠️ Workflow stopped - Poor job fit',
                progress: data.progress || 0.5,
                step: 'poor_fit_stopped'
              })
              
              setIsLoading(false)
              return
            }
            
            if (data.type === 'approval_required') {
              console.log('Received approval_required event:', data)
              setApprovalId(data.approval_id)
              setApprovalRequired(true)
              
              // Store LLM acknowledgment if provided
              if (data.llm_acknowledgment) {
                setLlmAcknowledgment(data.llm_acknowledgment)
              }
              
              // Store fit warning if provided
              if (data.fit_warning) {
                setError(`⚠️ ${data.fit_warning.message}${data.fit_warning.missing_areas.length > 0 ? '\n\nMissing: ' + data.fit_warning.missing_areas.join(', ') : ''}`)
              }
              
              // Update progress to show 100% when approval is required
              if (data.step_number && data.total_steps) {
                setProgress({
                  currentStep: data.step_number,
                  totalSteps: data.total_steps,
                  message: data.message,
                  progress: data.progress || 1.0,
                  step: 'approval_required'
                })
              }
              
              // Set result from approval data
              const newResult = {
                ...data.result,
                tailored_resume: data.result.tailored_resume || '',
                original_resume_text: data.result.original_resume_text || '',
                validation: data.result.validation,
                jd_requirements: data.result.jd_requirements,
                current_tailoring_iteration: data.result.current_tailoring_iteration || 1,
                timestamp: Date.now()
              }
              setResult(newResult)
              setIsLoading(false) // Stop loading spinner, show approval UI
            }
            
            if (data.type === 'complete') {
              console.log('Received complete result:', {
                hasResume: !!data.result.tailored_resume,
                resumeLength: data.result.tailored_resume?.length || 0,
                applicationId: data.result.application_id,
                docUrl: data.result.doc_url
              })
              
              // Store LLM acknowledgment if provided
              if (data.llm_acknowledgment) {
                setLlmAcknowledgment(data.llm_acknowledgment)
              }
              
              // If we have a doc_url, fetch the actual saved resume from Google Docs
              // This ensures we're showing what was actually saved, not just what was returned
              let finalResumeText = data.result.tailored_resume || ''
              
              if (data.result.doc_url) {
                // Extract doc_id from URL: https://docs.google.com/document/d/{doc_id}
                const docIdMatch = data.result.doc_url.match(/\/d\/([a-zA-Z0-9_-]+)/)
                if (docIdMatch && docIdMatch[1]) {
                  const docId = docIdMatch[1]
                  console.log('Fetching resume from Google Doc:', docId)
                  
                  try {
                    const resumeResponse = await fetch(`/api/resume/${docId}`, {
                      credentials: 'include'
                    })
                    if (resumeResponse.ok) {
                      const resumeData = await resumeResponse.json()
                      finalResumeText = resumeData.resume_text || finalResumeText
                      console.log('Fetched resume from Google Doc, length:', finalResumeText.length)
                    } else {
                      console.warn('Failed to fetch resume from Google Doc, using returned text')
                    }
                  } catch (err) {
                    console.warn('Error fetching resume from Google Doc:', err)
                    // Fall back to returned text
                  }
                }
              }
              
              // Ensure we're setting the latest result with a unique key
              const newResult = {
                ...data.result,
                tailored_resume: finalResumeText, // Use fetched resume or fallback to returned
                timestamp: Date.now() // Add timestamp to force re-render
              }
              console.log('Setting result with timestamp:', newResult.timestamp, 'resume length:', newResult.tailored_resume.length)
              setResult(newResult)
              setIsLoading(false)
            }
          }
        }
      }
    } catch (err) {
      setError(err.message)
      setIsLoading(false)
    }
  }

  const handleApprove = async () => {
    if (!approvalId) return
    
    setIsLoading(true)
    setError(null)
    
    try {
      const response = await fetch('/api/approve-resume', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          approval_id: approvalId,
          approved: true
        })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to approve resume')
      }
      
      const data = await response.json()
      setApprovalRequired(false)
      setApprovalId(null)
      
      // Update result with final data
      if (data.result) {
        setResult({
          ...result,
          doc_url: data.result.doc_url,
          application_id: data.result.application_id,
          timestamp: Date.now()
        })
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }

  const handleRefine = async () => {
    if (!approvalId || !refinementFeedback.trim()) {
      setError('Please provide feedback for refinement')
      return
    }
    
    setIsLoading(true)
    setError(null)
    
    try {
      const response = await fetch('/api/refine-resume', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          approval_id: approvalId,
          feedback: refinementFeedback
        })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to refine resume')
      }
      
      const data = await response.json()
      
      // Store LLM acknowledgment if provided
      if (data.llm_acknowledgment) {
        setLlmAcknowledgment(data.llm_acknowledgment)
      }
      
      // Update result with refined resume (matching structure from streaming response)
      if (data.result) {
        setResult({
          ...result,
          tailored_resume: data.result.tailored_resume || result.tailored_resume,
          original_resume_text: data.result.original_resume_text || result.original_resume_text,
          validation: data.result.validation || result.validation,
          jd_requirements: data.result.jd_requirements || result.jd_requirements,
          ats_score: data.result.ats_score !== undefined ? data.result.ats_score : result.ats_score,
          approval_required: data.result.approval_required !== undefined ? data.result.approval_required : true,
          approval_status: data.result.approval_status || result.approval_status || 'pending',
          current_tailoring_iteration: data.result.current_tailoring_iteration || result.current_tailoring_iteration,
          timestamp: Date.now()
        })
        
        // Update approval state from response
        setApprovalRequired(data.result.approval_required !== undefined ? data.result.approval_required : true)
        setApprovalId(data.approval_id || data.result.approval_id || approvalId)
      } else {
        // Fallback if result not in response
        setResult({
          ...result,
          timestamp: Date.now()
        })
        setApprovalRequired(true)
      }
      
      setRefinementFeedback('')
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }

  // Inline diff component for approval UI
  const InlineDiffView = ({ original, tailored }) => {
    const diffResult = useMemo(() => diffLines(original, tailored), [original, tailored])
    
    return (
      <div className="inline-diff-view">
        <div className="diff-summary" style={{ marginBottom: '1rem', padding: '0.75rem', background: '#f8f9fa', borderRadius: '4px' }}>
          <strong>📊 Change Summary:</strong>
          <div style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>
            <span style={{ color: '#4caf50' }}>+ {diffResult.filter(l => l.type === 'added').length} lines added</span>
            {' • '}
            <span style={{ color: '#f44336' }}>- {diffResult.filter(l => l.type === 'removed').length} lines removed</span>
            {' • '}
            <span style={{ color: '#ff9800' }}>~ {diffResult.filter(l => l.type === 'modified').length} lines modified</span>
          </div>
        </div>
        <div className="resume-preview-wrapper" style={{ maxHeight: '600px', overflow: 'auto' }}>
          {diffResult.map((line, idx) => {
            if (line.type === 'equal') {
              return (
                <div key={idx} className="diff-line-inline diff-equal-inline" style={{ opacity: 0.6, padding: '0.1rem 0.5rem' }}>
                  {line.original}
                </div>
              )
            } else if (line.type === 'added') {
              return (
                <div key={idx} className="diff-line-inline diff-added-inline" style={{ background: '#e8f5e9', borderLeft: '3px solid #4caf50', padding: '0.25rem 0.5rem', margin: '0.25rem 0' }}>
                  <span style={{ color: '#4caf50', fontWeight: 'bold', marginRight: '0.5rem' }}>+</span>
                  {line.tailored}
                </div>
              )
            } else if (line.type === 'removed') {
              return (
                <div key={idx} className="diff-line-inline diff-removed-inline" style={{ background: '#ffebee', borderLeft: '3px solid #f44336', padding: '0.25rem 0.5rem', margin: '0.25rem 0' }}>
                  <span style={{ color: '#f44336', fontWeight: 'bold', marginRight: '0.5rem' }}>-</span>
                  <span style={{ textDecoration: 'line-through' }}>{line.original}</span>
                </div>
              )
            } else if (line.type === 'modified') {
              return (
                <div key={idx} style={{ margin: '0.5rem 0' }}>
                  <div className="diff-line-inline diff-removed-inline" style={{ background: '#ffebee', borderLeft: '3px solid #f44336', padding: '0.25rem 0.5rem', marginBottom: '0.25rem' }}>
                    <span style={{ color: '#f44336', fontWeight: 'bold', marginRight: '0.5rem' }}>-</span>
                    {line.original}
                  </div>
                  <div className="diff-line-inline diff-added-inline" style={{ background: '#e8f5e9', borderLeft: '3px solid #4caf50', padding: '0.25rem 0.5rem' }}>
                    <span style={{ color: '#4caf50', fontWeight: 'bold', marginRight: '0.5rem' }}>+</span>
                    {line.tailored}
                  </div>
                </div>
              )
            }
            return null
          })}
        </div>
      </div>
    )
  }

  const handleReject = async () => {
    if (!approvalId) return
    
    setIsLoading(true)
    setError(null)
    
    try {
      const response = await fetch('/api/approve-resume', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          approval_id: approvalId,
          approved: false
        })
      })
      
      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to reject resume')
      }
      
      setApprovalRequired(false)
      setApprovalId(null)
      setResult(null)
      setError('Resume tailoring was rejected. You can start over with different parameters.')
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }


  return (
    <div className="tailor-resume">
      <h2>Tailor Your Resume</h2>
      
      <div className="form-group">
        <label>Company Name *</label>
        <input
          type="text"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          placeholder="Google"
          disabled={isLoading}
        />
      </div>

      <div className="form-group">
        <label>Job Title *</label>
        <input
          type="text"
          value={jobTitle}
          onChange={(e) => setJobTitle(e.target.value)}
          placeholder="Senior Software Engineer"
          disabled={isLoading}
        />
      </div>

      {/* Quality Report Modal */}
      {showQualityReport && qualityReport && (
        <div className="quality-report-modal">
          <div className="modal-content quality-modal-content">
            <div className="modal-header">
              <h3>📊 Resume Quality Report</h3>
              <button 
                className="close-button"
                onClick={() => setShowQualityReport(false)}
              >
                ✕
              </button>
            </div>
            
            <div className="quality-score-section">
              <div className={`quality-score score-${qualityReport.overall_score >= 80 ? 'high' : qualityReport.overall_score >= 60 ? 'medium' : 'low'}`}>
                <span className="score-number">{qualityReport.overall_score}</span>
                <span className="score-label">/100</span>
              </div>
              <p className="impact-text">{qualityReport.estimated_impact}</p>
            </div>
            
            {qualityReport.strengths && qualityReport.strengths.length > 0 && (
              <div className="quality-section strengths-section">
                <h4>✅ Strengths</h4>
                <ul>
                  {qualityReport.strengths.map((strength, idx) => (
                    <li key={idx}>{strength}</li>
                  ))}
                </ul>
              </div>
            )}
            
            {qualityReport.improvement_priority && qualityReport.improvement_priority.length > 0 && (
              <div className="quality-section priority-section">
                <h4>🎯 Improvement Priorities</h4>
                <ol>
                  {qualityReport.improvement_priority.map((priority, idx) => (
                    <li key={idx}>{priority}</li>
                  ))}
                </ol>
              </div>
            )}
            
            {qualityReport.issues && qualityReport.issues.length > 0 && (
              <div className="quality-section issues-section">
                <h4>📝 Issues Found ({qualityReport.issues.length})</h4>
                <div className="issues-list">
                  {qualityReport.issues.map((issue, idx) => (
                    <div key={idx} className={`quality-issue issue-${issue.severity}`}>
                      <div className="issue-header">
                        <span className={`severity-badge severity-${issue.severity}`}>
                          {issue.severity.toUpperCase()}
                        </span>
                        <span className="issue-category">{issue.category}</span>
                        <span className="issue-section">{issue.section}</span>
                      </div>
                      <p className="issue-description">{issue.issue}</p>
                      <p className="issue-suggestion">💡 {issue.suggestion}</p>
                      {issue.example && (
                        <p className="issue-example">📝 Example: {issue.example}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            
            {/* Clarifying Questions - shown before improving */}
            {qualityReport.questions && qualityReport.questions.length > 0 && !qualityReport.improved_resume && (
              <div className="quality-section questions-section">
                <h4>❓ Help Us Improve Your Resume</h4>
                <p className="questions-intro">
                  Answer these questions to ensure we add accurate information (optional but recommended):
                </p>
                <div className="questions-list">
                  {qualityReport.questions.map((q) => (
                    <div key={q.id} className="quality-question">
                      <label className="question-label">
                        {q.question}
                        {q.required && <span className="required">*</span>}
                      </label>
                      <p className="question-context" style={{ whiteSpace: 'pre-line' }}>{q.context}</p>
                      {q.options ? (
                        <select
                          value={qualityAnswers[q.id] || ''}
                          onChange={(e) => updateQualityAnswer(q.id, e.target.value)}
                          className="question-select"
                        >
                          <option value="">Select an option...</option>
                          {q.options.map((opt, idx) => (
                            <option key={idx} value={opt}>{opt}</option>
                          ))}
                        </select>
                      ) : (
                        <textarea
                          value={qualityAnswers[q.id] || ''}
                          onChange={(e) => updateQualityAnswer(q.id, e.target.value)}
                          placeholder={q.id === 'metrics_by_role' 
                            ? "Senior Dev @ Acme: Led team of 8, reduced deploy time by 40%\nQA Lead @ TechCorp: Automated 200+ test cases"
                            : "Type your answer..."}
                          className="question-textarea"
                          rows={q.id === 'metrics_by_role' ? 4 : 2}
                        />
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {qualityReport.improved_resume && (
              <div className="quality-section improved-section">
                <div className="improved-header">
                  <h4>✨ Improved Resume</h4>
                  <p className="score-improvement">
                    Score improved from {qualityReport.before_score} to {qualityReport.after_score}
                    {qualityReport.metrics_added > 0 && ` (+${qualityReport.metrics_added} metrics added)`}
                  </p>
                </div>
                
                <div className="changes-made">
                  <details>
                    <summary><strong>Changes Made ({qualityReport.changes_made?.length || 0})</strong></summary>
                    <ul>
                      {qualityReport.changes_made?.map((change, idx) => (
                        <li key={idx}>{change}</li>
                      ))}
                    </ul>
                  </details>
                </div>
                
                {/* Resume Preview/Edit with Tabs */}
                <div className="resume-preview-section">
                  <div className="preview-header">
                    <strong>📄 Improved Resume</strong>
                    <div className="preview-tabs">
                      <button 
                        className={`tab-button ${previewMode === 'preview' ? 'active' : ''}`}
                        onClick={() => setPreviewMode('preview')}
                      >
                        👁️ Preview
                      </button>
                      <button 
                        className={`tab-button ${previewMode === 'edit' ? 'active' : ''}`}
                        onClick={() => setPreviewMode('edit')}
                      >
                        ✏️ Edit
                      </button>
                    </div>
                  </div>
                  
                  {previewMode === 'preview' ? (
                    <div className="resume-preview-markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {editedImprovedResume}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <textarea
                      className="resume-preview-textarea"
                      value={editedImprovedResume}
                      onChange={(e) => setEditedImprovedResume(e.target.value)}
                      rows={20}
                      placeholder="Edit your resume here..."
                    />
                  )}
                </div>
                
                {/* Folder selector and action buttons */}
                <div className="save-options">
                  <div className="folder-selector-inline">
                    <label>📁 Save to:</label>
                    <select
                      value={saveFolderId || ''}
                      onChange={(e) => {
                        setSaveFolderId(e.target.value)
                        const folder = availableFolders.find(f => f.id === e.target.value)
                        setSelectedFolderName(folder?.name || '')
                      }}
                      className="folder-select"
                    >
                      <option value="">Google Drive Root</option>
                      {availableFolders.map(folder => (
                        <option key={folder.id} value={folder.id}>
                          {folder.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  
                  <div className="improved-actions">
                    <button
                      className="secondary-button"
                      onClick={recheckQuality}
                      disabled={analyzingQuality || !editedImprovedResume}
                    >
                      {analyzingQuality ? '🔄 Checking...' : '🔄 Re-check Quality'}
                    </button>
                    <button
                      className="primary-button"
                      onClick={saveImprovedResumeToDrive}
                      disabled={savingImprovedResume || !editedImprovedResume}
                    >
                      {savingImprovedResume ? '💾 Saving...' : '💾 Save to Google Drive'}
                    </button>
                  </div>
                </div>
              </div>
            )}
            
            <div className="modal-actions">
              {!qualityReport.improved_resume && qualityReport.issues && qualityReport.issues.length > 0 && (
                <button
                  className="primary-button"
                  onClick={improveResume}
                  disabled={analyzingQuality}
                >
                  {analyzingQuality ? 'Improving...' : '✨ Auto-Improve Resume'}
                </button>
              )}
              <button
                className="secondary-button"
                onClick={() => {
                  setShowQualityReport(false)
                  setQualityAnswers({})  // Reset answers when closing
                  setEditedImprovedResume('')  // Reset edited resume
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Skill Confirmation Modal */}
      {showSkillConfirmation && (
        <div className="skill-confirmation-modal">
          <div className="modal-content">
            <h3>📋 Review Your Skills</h3>
            <p>We've extracted skills from your resume. Please review, add any missing skills, or remove skills you no longer have.</p>
            
            <div className="skills-editor">
              <div className="skills-list">
                {/* Show all unique skills from extracted and confirmed */}
                {[...new Set([...extractedSkills, ...confirmedSkills])].map((skill, idx) => (
                  <label key={idx} className="skill-checkbox">
                    <input
                      type="checkbox"
                      checked={confirmedSkills.includes(skill)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setConfirmedSkills([...confirmedSkills, skill])
                        } else {
                          setConfirmedSkills(confirmedSkills.filter(s => s !== skill))
                        }
                      }}
                    />
                    <span>{skill}</span>
                  </label>
                ))}
              </div>
              
              <div className="add-skill-section">
                <input
                  type="text"
                  placeholder="Add a new skill and press Enter..."
                  onKeyPress={(e) => {
                    if (e.key === 'Enter' && e.target.value.trim()) {
                      const newSkill = e.target.value.trim()
                      if (!confirmedSkills.includes(newSkill)) {
                        setConfirmedSkills([...confirmedSkills, newSkill])
                        // Also add to extracted skills so it shows up in the list
                        if (!extractedSkills.includes(newSkill)) {
                          setExtractedSkills([...extractedSkills, newSkill])
                        }
                      }
                      e.target.value = ''
                    }
                  }}
                  className="add-skill-input"
                />
                <small style={{ color: '#666', marginTop: '0.25rem', display: 'block' }}>
                  Press Enter to add a skill
                </small>
              </div>
            </div>
            
            <div className="modal-actions">
              <button
                className="primary-button"
                onClick={confirmSkills}
                disabled={confirmedSkills.length === 0}
              >
                ✅ Confirm Skills ({confirmedSkills.length})
              </button>
              <button
                className="secondary-button"
                onClick={() => {
                  // Select all extracted skills
                  setConfirmedSkills([...extractedSkills])
                }}
              >
                Select All
              </button>
              <button
                className="secondary-button"
                onClick={() => {
                  // Clear all selections
                  setConfirmedSkills([])
                }}
              >
                Clear All
              </button>
              <button
                className="secondary-button"
                onClick={() => {
                  setShowSkillConfirmation(false)
                  setConfirmedSkills(extractedSkills) // Reset to extracted
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Resume Selection */}
      <div className="form-group">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <label>Resume Source</label>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {resumeDocId && (
              <button
                type="button"
                className="secondary-button"
                onClick={analyzeResumeQuality}
                disabled={isLoading || analyzingQuality}
                style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}
              >
                {analyzingQuality ? 'Analyzing...' : '📊 Quality Check'}
              </button>
            )}
            {resumeDocId && !skillsExtracted && (
              <button
                type="button"
                className="secondary-button"
                onClick={extractSkills}
                disabled={isLoading || loadingResumes || extractingSkills}
                style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}
              >
                {extractingSkills ? 'Extracting...' : '📋 Extract Skills'}
              </button>
            )}
            <button
              type="button"
              className="secondary-button"
              onClick={() => setShowResumeSelector(!showResumeSelector)}
              disabled={isLoading}
              style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}
            >
              {showResumeSelector ? 'Hide' : (resumeDocId ? 'Change Resume' : 'Select Resume')}
            </button>
          </div>
        </div>
        {showResumeSelector ? (
          <div className="selector-container">
            <div className="selector-search">
              <input
                type="text"
                placeholder="Search resumes..."
                value={resumeSearchQuery}
                onChange={(e) => setResumeSearchQuery(e.target.value)}
                className="search-input"
              />
            </div>
            {loadingResumes ? (
              <div className="loading-text">Loading resumes...</div>
            ) : availableResumes.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">📄</div>
                <div className="empty-state-message">No resumes found in your Google Drive</div>
                <div className="upload-section">
                  <input
                    type="file"
                    id="resume-upload"
                    accept=".pdf,.doc,.docx,.txt,.md"
                    style={{ display: 'none' }}
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      
                      try {
                        setIsLoading(true);
                        setError(null);
                        
                        const formData = new FormData();
                        formData.append('file', file);
                        if (saveFolderId) {
                          formData.append('folder_id', saveFolderId);
                        }
                        
                        const response = await fetch('/api/google-docs/upload', {
                          method: 'POST',
                          credentials: 'include',
                          body: formData
                        });
                        
                        if (!response.ok) {
                          const errorData = await response.json();
                          throw new Error(errorData.detail || 'Failed to upload resume');
                        }
                        
                        const data = await response.json();
                        
                        // Refresh the list
                        const listResponse = await fetch('/api/google-docs?max_results=100', {
                          credentials: 'include'
                        });
                        if (listResponse.ok) {
                          const listData = await listResponse.json();
                          setAvailableResumes(listData.docs || []);
                          // Auto-select the newly uploaded resume
                          setResumeDocId(data.doc_id);
                          setSelectedResumeName(data.doc_name);
                          setShowResumeSelector(false);
                          localStorage.setItem('resume_agent_last_resume_id', data.doc_id);
                          localStorage.setItem('resume_agent_last_resume_name', data.doc_name);
                        }
                        
                        alert(`Resume "${data.doc_name}" uploaded successfully! It has been converted to a Google Doc.`);
                      } catch (err) {
                        setError(err.message);
                      } finally {
                        setIsLoading(false);
                        // Reset file input
                        e.target.value = '';
                      }
                    }}
                    disabled={isLoading}
                  />
                  <label htmlFor="resume-upload" className="upload-button">
                    📤 Upload Resume
                  </label>
                </div>
                <div className="empty-state-hint">
                  Supported formats: PDF, DOC, DOCX, TXT, MD
                  <br />
                  Or upload your resume to Google Drive manually and refresh this page
                </div>
              </div>
            ) : (
              availableResumes
                .filter(doc => 
                  !resumeSearchQuery || 
                  doc.name.toLowerCase().includes(resumeSearchQuery.toLowerCase())
                )
                .map((doc) => (
                <div key={doc.id} className="selector-option">
                  <label>
                    <input
                      type="radio"
                      name="resume_source"
                      checked={resumeDocId === doc.id}
                      onChange={() => {
                        setResumeDocId(doc.id);
                        setSelectedResumeName(doc.name);
                        setShowResumeSelector(false); // Close selector after selection
                        // Save to localStorage
                        localStorage.setItem('resume_agent_last_resume_id', doc.id);
                        localStorage.setItem('resume_agent_last_resume_name', doc.name);
                      }}
                      disabled={isLoading}
                    />
                    <span className="selector-label">
                      {doc.name}
                      {doc.modifiedTime && (
                        <span className="selector-meta">
                          {' '}(Modified: {new Date(doc.modifiedTime).toLocaleDateString()})
                        </span>
                      )}
                    </span>
                  </label>
                  <a
                    href={doc.webViewLink}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="selector-link"
                    onClick={(e) => e.stopPropagation()}
                  >
                    Open
                  </a>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="info-text">
            {resumeDocId && selectedResumeName ? (
              <div>
                <span>📄 Selected: <strong>{selectedResumeName}</strong></span>
                {!skillsExtracted && (
                  <div style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: '#666' }}>
                    ⚠️ Skills not extracted yet. Click "Extract Skills" button above.
                  </div>
                )}
                {skillsExtracted && (
                  <div style={{ marginTop: '0.5rem' }}>
                    <div style={{ fontSize: '0.85rem', color: '#4caf50', marginBottom: '0.5rem' }}>
                      ✅ Skills confirmed ({confirmedSkills.length} skills)
                    </div>
                    <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                      <button
                        type="button"
                        className="small-button"
                        onClick={() => setShowSkillConfirmation(true)}
                        style={{ padding: '0.25rem 0.5rem', fontSize: '0.8rem' }}
                      >
                        ✏️ Edit Skills
                      </button>
                      <button
                        type="button"
                        className="small-button danger"
                        onClick={resetSkills}
                        style={{ padding: '0.25rem 0.5rem', fontSize: '0.8rem' }}
                      >
                        🗑️ Reset Skills
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <span className="warning-text">⚠️ Please select a resume to tailor</span>
            )}
          </div>
        )}
      </div>

      {/* Save Location Selection */}
      <div className="form-group">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
          <label>Save Location</label>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setShowFolderSelector(!showFolderSelector)}
            disabled={isLoading}
            style={{ padding: '0.5rem 1rem', fontSize: '0.9rem' }}
          >
            {showFolderSelector ? 'Hide' : (saveFolderId ? 'Change Folder' : 'Select Folder')}
          </button>
        </div>
        {showFolderSelector ? (
          <div className="selector-container">
            <div className="selector-search">
              <input
                type="text"
                placeholder="Search folders..."
                value={folderSearchQuery}
                onChange={(e) => setFolderSearchQuery(e.target.value)}
                className="search-input"
              />
            </div>
            {loadingFolders ? (
              <div className="loading-text">Loading folders...</div>
            ) : availableFolders.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">📁</div>
                <div className="empty-state-message">No folders found in your Google Drive</div>
                <button
                  type="button"
                  className="create-button"
                  onClick={async () => {
                    const folderName = prompt('Enter a name for your new folder:', 'Resume Tailor');
                    if (folderName && folderName.trim()) {
                      try {
                        setIsLoading(true);
                        const response = await fetch('/api/google-folders/create', {
                          method: 'POST',
                          headers: {
                            'Content-Type': 'application/json',
                          },
                          credentials: 'include',
                          body: JSON.stringify({
                            folder_name: folderName.trim()
                          })
                        });
                        
                        if (!response.ok) {
                          const errorData = await response.json();
                          throw new Error(errorData.detail || 'Failed to create folder');
                        }
                        
                        const data = await response.json();
                        // Refresh the list
                        const listResponse = await fetch('/api/google-folders?max_results=100', {
                          credentials: 'include'
                        });
                        if (listResponse.ok) {
                          const listData = await listResponse.json();
                          setAvailableFolders(listData.folders || []);
                          // Auto-select the newly created folder
                          setSaveFolderId(data.folder_id);
                          setSelectedFolderName(data.folder_name);
                          setShowFolderSelector(false);
                          localStorage.setItem('resume_agent_last_folder_id', data.folder_id);
                          localStorage.setItem('resume_agent_last_folder_name', data.folder_name);
                        }
                        alert(`Folder "${data.folder_name}" created successfully!`);
                      } catch (err) {
                        setError(err.message);
                      } finally {
                        setIsLoading(false);
                      }
                    }
                  }}
                  disabled={isLoading}
                >
                  ➕ Create New Folder
                </button>
                <div className="empty-state-hint">Or create a folder in Google Drive first</div>
              </div>
            ) : (
              availableFolders
                .filter(folder =>
                  !folderSearchQuery ||
                  folder.name.toLowerCase().includes(folderSearchQuery.toLowerCase()) ||
                  (folder.path && folder.path.toLowerCase().includes(folderSearchQuery.toLowerCase()))
                )
                .map((folder) => (
                <div key={folder.id} className="selector-option">
                  <label>
                    <input
                      type="radio"
                      name="save_folder"
                      checked={saveFolderId === folder.id}
                      onChange={() => {
                        setSaveFolderId(folder.id);
                        setSelectedFolderName(folder.name);
                        setShowFolderSelector(false); // Close selector after selection
                        // Save to localStorage
                        localStorage.setItem('resume_agent_last_folder_id', folder.id);
                        localStorage.setItem('resume_agent_last_folder_name', folder.name);
                      }}
                      disabled={isLoading}
                    />
                    <span className="selector-label">
                      {folder.name}
                      {folder.path && folder.path !== 'Unknown' && (
                        <span className="selector-meta"> ({folder.path})</span>
                      )}
                    </span>
                  </label>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="info-text">
            {saveFolderId ? (
              <>
                Saving to: {availableFolders.find(f => f.id === saveFolderId)?.name || 'Selected folder'}
              </>
            ) : (
              <>
                📁 Will save to your Google Drive root (or click "Select Folder" to choose)
              </>
            )}
          </div>
        )}
      </div>

      <div className="form-group">
        <label>Job Description Source *</label>
        <div className="radio-group">
          <label>
            <input
              type="radio"
              value="url"
              checked={inputMethod === 'url'}
              onChange={(e) => {
                setInputMethod('url')
                setJdText('') // Clear text when switching to URL
              }}
              disabled={isLoading}
            />
            📄 Job Listing URL
          </label>
          <label>
            <input
              type="radio"
              value="text"
              checked={inputMethod === 'text'}
              onChange={(e) => {
                setInputMethod('text')
                setJobUrl('') // Clear URL when switching to text
              }}
              disabled={isLoading}
            />
            📝 Paste Text
          </label>
        </div>
      </div>

      {inputMethod === 'url' ? (
        <div className="form-group">
          <label>Job Listing URL *</label>
          <div className="input-with-button">
            <input
              type="url"
              value={jobUrl}
              onChange={(e) => setJobUrl(e.target.value)}
              placeholder="https://..."
              disabled={isLoading || isExtracting}
            />
            <button
              type="button"
              className="secondary-button"
              onClick={handleExtractJD}
              disabled={isLoading || isExtracting || !jobUrl}
            >
              {isExtracting ? 'Extracting...' : 'Extract JD'}
            </button>
          </div>
          {jdText && (
            <div className="extracted-jd-preview">
              <strong>✅ Extracted Job Description:</strong>
              <textarea
                readOnly
                value={jdText.substring(0, 500)}
                rows={5}
                className="jd-preview"
              />
              {jdText.length > 500 && (
                <p className="preview-note">
                  Showing first 500 characters. Full text will be used for tailoring.
                </p>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="form-group">
          <label>Job Description *</label>
          <textarea
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            placeholder="Paste job description here..."
            rows={10}
            disabled={isLoading}
          />
        </div>
      )}

      <div className="form-group checkbox-group">
        <label>
          <input
            type="checkbox"
            checked={evaluateFirst}
            onChange={(e) => setEvaluateFirst(e.target.checked)}
            disabled={isLoading}
          />
          Evaluate fit before tailoring
        </label>
        <label>
          <input
            type="checkbox"
            checked={trackApplication}
            onChange={(e) => setTrackApplication(e.target.checked)}
            disabled={isLoading}
          />
          Track this application
        </label>
      </div>

      {/* Pre-tailoring quality warning */}
      {originalQualityWarning && !result && (
        <div className="quality-pre-warning">
          <div className="warning-header">
            <strong>⚠️ Original Resume Quality: {originalQualityWarning.score}/100</strong>
            {originalQualityWarning.atsScore < 70 && (
              <span className="ats-badge">ATS: {originalQualityWarning.atsScore}%</span>
            )}
          </div>
          <p>Your resume could be improved before tailoring. Low quality resumes often result in:</p>
          <ul>
            <li>More fabrication issues (LLM adds content that wasn't there)</li>
            <li>Lower ATS compatibility</li>
            <li>Weaker job fit scores</li>
          </ul>
          {originalQualityWarning.priority.length > 0 && (
            <div className="priority-issues">
              <strong>Top priorities:</strong>
              <ul>
                {originalQualityWarning.priority.map((p, idx) => (
                  <li key={idx}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="warning-actions">
            <button 
              className="secondary-button"
              onClick={() => {
                analyzeResumeQuality()
                setOriginalQualityWarning(null) // Dismiss warning
              }}
              disabled={analyzingQuality}
            >
              {analyzingQuality ? 'Analyzing...' : '📊 Improve Resume First'}
            </button>
            <button 
              className="text-button"
              onClick={() => setOriginalQualityWarning(null)}
            >
              Proceed Anyway →
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="error-message">
          ❌ {error}
        </div>
      )}

      {progress && (
        <div className="progress-container">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${progress.progress * 100}%` }}
            />
          </div>
          <div className="progress-text">
            {progress.message} ({progress.currentStep}/{progress.totalSteps})
          </div>
        </div>
      )}

      <button
        className="primary-button"
        onClick={handleTailor}
        disabled={isLoading || !company || !jobTitle || (!jobUrl && !jdText.trim())}
      >
        {isLoading ? 'Processing...' : 'Tailor Resume'}
      </button>

      {/* Poor Fit Stopped Result */}
      {result && result.poor_fit_stopped && (
        <div className="result-container poor-fit-container">
          <h3>⚠️ Workflow Stopped: Poor Job Fit</h3>
          
          <div className="poor-fit-summary">
            <div className="fit-score-display">
              <span className="score-label">Fit Score:</span>
              <span className={`score-value score-${result.evaluation?.score >= 7 ? 'high' : result.evaluation?.score >= 5 ? 'medium' : 'low'}`}>
                {result.evaluation?.score || 'N/A'}/10
              </span>
            </div>
            <p className="fit-explanation">
              This job may not be a good match for your current skillset. 
              The workflow was stopped to save time.
            </p>
          </div>
          
          {/* Matching Skills */}
          {result.evaluation?.matching_areas && result.evaluation.matching_areas.length > 0 && (
            <div className="skills-section matching-skills">
              <h4>✅ Matching Skills ({result.evaluation.matching_areas.length})</h4>
              <ul>
                {result.evaluation.matching_areas.map((skill, idx) => (
                  <li key={idx}>{skill}</li>
                ))}
              </ul>
            </div>
          )}
          
          {/* Missing Skills */}
          {result.evaluation?.missing_areas && result.evaluation.missing_areas.length > 0 && (
            <div className="skills-section missing-skills">
              <h4>❌ Missing Required Skills ({result.evaluation.missing_areas.length})</h4>
              <ul>
                {result.evaluation.missing_areas.map((skill, idx) => (
                  <li key={idx}>{skill}</li>
                ))}
              </ul>
            </div>
          )}
          
          {/* JD Required Skills */}
          {result.analyzed_jd?.required_skills && result.analyzed_jd.required_skills.length > 0 && (
            <div className="skills-section jd-skills">
              <h4>📋 JD Required Skills</h4>
              <ul>
                {result.analyzed_jd.required_skills.slice(0, 10).map((skill, idx) => (
                  <li key={idx}>{skill}</li>
                ))}
                {result.analyzed_jd.required_skills.length > 10 && (
                  <li className="more-items">...and {result.analyzed_jd.required_skills.length - 10} more</li>
                )}
              </ul>
            </div>
          )}
          
          {/* Your Skills */}
          {result.parsed_resume?.all_skills && result.parsed_resume.all_skills.length > 0 && (
            <div className="skills-section your-skills">
              <h4>📝 Your Confirmed Skills ({result.parsed_resume.all_skills.length})</h4>
              <ul>
                {result.parsed_resume.all_skills.slice(0, 15).map((skill, idx) => (
                  <li key={idx}>{skill}</li>
                ))}
                {result.parsed_resume.all_skills.length > 15 && (
                  <li className="more-items">...and {result.parsed_resume.all_skills.length - 15} more</li>
                )}
              </ul>
            </div>
          )}
          
          {/* Recommendations */}
          {result.evaluation?.recommendations && result.evaluation.recommendations.length > 0 && (
            <div className="recommendations-section">
              <h4>💡 Recommendations</h4>
              <ul>
                {result.evaluation.recommendations.map((rec, idx) => (
                  <li key={idx}>{rec}</li>
                ))}
              </ul>
            </div>
          )}
          
          <div className="poor-fit-actions">
            <button 
              className="secondary-button"
              onClick={() => {
                setResult(null)
                setProgress({ currentStep: 0, totalSteps: 0, message: '', progress: 0 })
              }}
            >
              🔄 Try Another Job
            </button>
          </div>
        </div>
      )}

      {result && !result.poor_fit_stopped && (
        <div className="result-container">
          <h3>✅ Resume Tailored Successfully!</h3>
          
          {/* Fit Evaluation Warning */}
          {result.evaluation && (result.evaluation.score < 5 || !result.evaluation.should_apply) && (
            <div className="fit-warning">
              <div className="warning-header">
                <strong>⚠️ Low Fit Score: {result.evaluation.score}/10</strong>
              </div>
              <div className="warning-message">
                This role may not be a good match for your background. Consider reviewing the requirements before applying.
              </div>
              {result.evaluation.missing_areas && result.evaluation.missing_areas.length > 0 && (
                <div className="missing-areas">
                  <strong>Missing Requirements:</strong>
                  <ul>
                    {result.evaluation.missing_areas.map((area, idx) => (
                      <li key={idx}>{area}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
          
          {/* Fabrication Check Results (renamed from Quality Validation for clarity) */}
          {result.quality_report && (
            <div className="quality-cache">
              <div className="quality-cache-header">
                <strong>📊 Resume Quality (cached)</strong>
                <span className={`quality-score score-${result.quality_report.overall_score >= 80 ? 'high' : result.quality_report.overall_score >= 60 ? 'medium' : 'low'}`}>
                  Score: {result.quality_report.overall_score}/100
                </span>
              </div>
              <div className="quality-cache-details">
                <span>ATS: {result.quality_report.ats_score ?? 'n/a'}</span>
                <span>Metrics: {result.quality_report.metrics_count ?? 0}</span>
                <span>Updated: {result.quality_report.updated_at ? new Date(result.quality_report.updated_at).toLocaleString() : 'n/a'}</span>
              </div>
              {result.quality_warning && (
                <div className="quality-cache-warning">
                  {result.quality_warning.message}
                  <button
                    className="secondary-button"
                    onClick={analyzeResumeQuality}
                    disabled={analyzingQuality}
                  >
                    📊 Run Quality Check
                  </button>
                </div>
              )}
            </div>
          )}
          {result.quality_warning && result.quality_warning.missing && !result.quality_report && (
            <div className="quality-cache">
              <div className="quality-cache-header">
                <strong>📊 Resume Quality</strong>
              </div>
              <div className="quality-cache-warning">
                {result.quality_warning.message}
                <button
                  className="secondary-button"
                  onClick={analyzeResumeQuality}
                  disabled={analyzingQuality}
                >
                  📊 Run Quality Check
                </button>
              </div>
            </div>
          )}
          {result.validation && (
            <div className="validation-container">
              <div className="validation-header">
                <strong>🔍 Fabrication Check</strong>
                <span className={`quality-score score-${result.validation.quality_score >= 80 ? 'high' : result.validation.quality_score >= 60 ? 'medium' : 'low'}`}>
                  Score: {result.validation.quality_score}/100
                </span>
              </div>
              
              {/* Low score warning with actionable steps */}
              {result.validation.quality_score < 60 && (
                <div className="low-score-warning">
                  <strong>⚠️ Low Score Detected</strong>
                  <p>
                    The tailored resume contains items that weren't in your original resume. 
                    This usually means:
                  </p>
                  <ul>
                    <li>Skills/technologies were added that you haven't confirmed</li>
                    <li>Experience details were embellished</li>
                  </ul>
                  <p><strong>Recommended Actions:</strong></p>
                  <ol>
                    <li>✏️ <strong>Update your skills</strong> - Click "Edit Skills" above to add any missing skills you actually have</li>
                    <li>📊 <strong>Improve original resume</strong> - Use the "Quality Check" button to enhance your original resume first</li>
                    <li>🔄 <strong>Try again</strong> - After updating skills or improving your resume, tailor again</li>
                  </ol>
                  <div className="low-score-actions">
                    <button 
                      className="secondary-button"
                      onClick={() => setShowSkillConfirmation(true)}
                    >
                      ✏️ Edit Skills
                    </button>
                    <button 
                      className="secondary-button"
                      onClick={analyzeResumeQuality}
                      disabled={analyzingQuality}
                    >
                      📊 Quality Check
                    </button>
                  </div>
                </div>
              )}
              
              {result.validation.issues && result.validation.issues.length > 0 && (
                <div className="validation-issues">
                  <strong>Issues Found:</strong>
                  {result.validation.issues.map((issue, idx) => (
                    <div key={idx} className={`issue issue-${issue.severity}`}>
                      <span className="issue-severity">{issue.severity.toUpperCase()}</span>
                      <span className="issue-message">{issue.message}</span>
                      {issue.suggestion && (
                        <div className="issue-suggestion">💡 {issue.suggestion}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {result.validation.metric_provenance && (
                <div className="metric-provenance">
                  <strong>📌 Metrics Provenance</strong>
                  <div className="metric-summary">
                    <span>Allowed: {result.validation.metric_provenance.allowed?.length || 0}</span>
                    <span>In Resume: {result.validation.metric_provenance.tailored?.length || 0}</span>
                    <span>Flagged: {result.validation.metric_provenance.flagged?.length || 0}</span>
                  </div>
                  {result.validation.metric_provenance.flagged_details &&
                    result.validation.metric_provenance.flagged_details.length > 0 && (
                      <div className="metric-flagged">
                        <strong>Unverified metrics detected:</strong>
                        {result.validation.metric_provenance.flagged_details.map((metric, idx) => (
                          <div key={idx} className="metric-flagged-item">
                            <span className="metric-raw">"{metric.raw}"</span>
                            {metric.line && (
                              <div className="metric-line">Context: {metric.line}</div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  {result.validation.metric_provenance.flagged_details &&
                    result.validation.metric_provenance.flagged_details.length === 0 && (
                      <div className="metric-ok">No unverified metrics found.</div>
                    )}
                </div>
              )}
              
              {result.validation.recommendations && result.validation.recommendations.length > 0 && (
                <div className="validation-recommendations">
                  <strong>Recommendations:</strong>
                  <ul>
                    {result.validation.recommendations.map((rec, idx) => (
                      <li key={idx}>{rec}</li>
                    ))}
                  </ul>
                </div>
              )}
              
              {result.jd_requirements && (
                <div className="jd-requirements">
                  <strong>📋 Key Requirements from Job Description:</strong>
                  {result.jd_requirements.required_skills && result.jd_requirements.required_skills.length > 0 && (
                    <div className="requirement-group">
                      <strong>Required Skills:</strong>
                      <div className="skill-tags">
                        {result.jd_requirements.required_skills.map((skill, idx) => (
                          <span key={idx} className="skill-tag required">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {result.jd_requirements.preferred_skills && result.jd_requirements.preferred_skills.length > 0 && (
                    <div className="requirement-group">
                      <strong>Preferred Skills:</strong>
                      <div className="skill-tags">
                        {result.jd_requirements.preferred_skills.map((skill, idx) => (
                          <span key={idx} className="skill-tag preferred">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          
          {result.doc_url && (
            <div className="result-item">
              <strong>📄 Google Docs:</strong>{' '}
              <a href={result.doc_url} target="_blank" rel="noopener noreferrer">
                Open in Google Docs
              </a>
            </div>
          )}
          
          {result.diff_path && (
            <div className="result-item">
              <strong>📝 Change Log:</strong> {result.diff_path}
            </div>
          )}
          
          {result.application_id && (
            <div className="result-item">
              <strong>📊 Application ID:</strong> {result.application_id}
            </div>
          )}

          {/* Fit Score Display */}
          {(result.evaluation || result.fit_score) && (
            <div className="result-item">
              <strong>🎯 Fit Score:</strong> {result.evaluation?.score ?? result.fit_score}/10
              {result.evaluation && (
                <span style={{ marginLeft: '1rem', color: result.evaluation.should_apply ? '#4caf50' : '#f57c00' }}>
                  {result.evaluation.should_apply ? '✅ Recommended to apply' : '⚠️ Not recommended'}
                </span>
              )}
            </div>
          )}
          
          {/* Fit Evaluation Details */}
          {result.evaluation && result.evaluation.matching_areas && result.evaluation.matching_areas.length > 0 && (
            <div className="result-item">
              <strong>✅ Matching Areas:</strong>
              <ul style={{ marginTop: '0.5rem', marginLeft: '1.5rem' }}>
                {result.evaluation.matching_areas.map((area, idx) => (
                  <li key={idx}>{area}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Approval UI - shown when approval is required */}
          {approvalRequired && result.tailored_resume && (
            <div className="approval-container">
              <h3>👁️ Review & Approve Resume</h3>
              <p>Please review the tailored resume below. You can approve it, request refinements, or reject it.</p>
              
              <div className="result-item resume-preview-container" key={`resume-preview-${result.timestamp || Date.now()}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                  <strong>📄 Tailored Resume Preview:</strong>
                  {result.original_resume_text && (
                    <button
                      className="secondary-button"
                      onClick={() => setShowInlineDiff(!showInlineDiff)}
                      style={{ fontSize: '0.9rem', padding: '0.5rem 1rem' }}
                    >
                      {showInlineDiff ? '👁️ Show Full Resume' : '🔍 Show Changes Only'}
                    </button>
                  )}
                </div>
                {result.timestamp && (
                  <div className="resume-timestamp">
                    Generated: {new Date(result.timestamp).toLocaleString()}
                    {result.current_tailoring_iteration > 1 && (
                      <span> (Iteration {result.current_tailoring_iteration})</span>
                    )}
                  </div>
                )}
                
                {showInlineDiff && result.original_resume_text ? (
                  <InlineDiffView 
                    original={result.original_resume_text} 
                    tailored={result.tailored_resume} 
                  />
                ) : (
                  <div className="resume-preview-wrapper">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      className="resume-markdown"
                      key={`markdown-${result.tailored_resume.substring(0, 50)}`}
                    >
                      {result.tailored_resume}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
              
              <div className="approval-actions">
                {result.original_resume_text && (
                  <button
                    className="secondary-button"
                    onClick={() => setShowComparison(true)}
                    style={{ marginBottom: '1rem' }}
                  >
                    🔍 View Changes (Compare with Original)
                  </button>
                )}
                
                <button
                  className="primary-button"
                  onClick={handleApprove}
                  disabled={isLoading}
                >
                  ✅ Approve & Save
                </button>
                
                <div className="refine-section">
                  <textarea
                    placeholder="Enter feedback for refinement (e.g., 'Make the summary more technical', 'Add more AWS experience')"
                    value={refinementFeedback}
                    onChange={(e) => setRefinementFeedback(e.target.value)}
                    disabled={isLoading}
                    rows={3}
                    style={{ width: '100%', marginBottom: '10px', padding: '10px' }}
                  />
                  <button
                    className="secondary-button"
                    onClick={handleRefine}
                    disabled={isLoading || !refinementFeedback.trim()}
                  >
                    🔄 Refine Resume
                  </button>
                </div>
                
                <button
                  className="danger-button"
                  onClick={handleReject}
                  disabled={isLoading}
                  style={{ marginTop: '10px' }}
                >
                  ❌ Reject
                </button>
              </div>
            </div>
          )}

          {/* Final result (after approval) - only show if not in approval mode */}
          {!approvalRequired && result.tailored_resume && (
            <div className="result-item resume-preview-container" key={`resume-${result.application_id || result.timestamp || Date.now()}`}>
              <strong>📄 Tailored Resume Preview:</strong>
              {result.timestamp && (
                <div className="resume-timestamp">
                  Generated: {new Date(result.timestamp).toLocaleString()}
                </div>
              )}
              <div className="resume-preview-wrapper">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  className="resume-markdown"
                  key={`markdown-${result.tailored_resume.substring(0, 50)}`}
                >
                  {result.tailored_resume}
                </ReactMarkdown>
              </div>
              <div className="resume-actions">
                <button
                  className="secondary-button"
                  onClick={() => {
                    navigator.clipboard.writeText(result.tailored_resume)
                    alert('Resume copied to clipboard!')
                  }}
                >
                  📋 Copy Full Resume
                </button>
                {result.original_resume_text && (
                  <button
                    className="secondary-button"
                    onClick={() => setShowComparison(true)}
                  >
                    🔍 Compare with Original
                  </button>
                )}
              </div>
              
              {/* Feedback Learner Component */}
              <div style={{ marginTop: '20px', paddingTop: '20px', borderTop: '1px solid #eee' }}>
                <FeedbackLearner
                  resumeContent={result.tailored_resume}
                  jobDescription={jdText}
                  onFeedbackSubmitted={() => {
                    // Reload or refresh if needed
                    console.log('Feedback submitted')
                  }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {showComparison && result.original_resume_text && result.tailored_resume && (
        <ResumeComparison
          original={result.original_resume_text}
          tailored={result.tailored_resume}
          onClose={() => setShowComparison(false)}
        />
      )}
    </div>
  )
}

export default TailorResume
