import React, { useState, useEffect } from 'react'
import './FeedbackLearner.css'

function FeedbackLearner({ resumeContent, jobDescription, onFeedbackSubmitted }) {
  const [showFeedback, setShowFeedback] = useState(false)
  const [feedbackText, setFeedbackText] = useState('')
  const [feedbackType, setFeedbackType] = useState('formatting')
  const [suggestedImprovement, setSuggestedImprovement] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [learningOpportunities, setLearningOpportunities] = useState([])
  const [showLearningModal, setShowLearningModal] = useState(false)
  const [selectedOpportunities, setSelectedOpportunities] = useState([])

  useEffect(() => {
    // Load learning opportunities on mount
    loadLearningOpportunities()
  }, [])

  const loadLearningOpportunities = async () => {
    try {
      const response = await fetch('/api/feedback/opportunities')
      if (response.ok) {
        const data = await response.json()
        setLearningOpportunities(data.opportunities || [])
      }
    } catch (err) {
      console.error('Failed to load learning opportunities:', err)
    }
  }

  const handleSubmitFeedback = async () => {
    if (!feedbackText.trim()) {
      alert('Please provide feedback text')
      return
    }

    setIsSubmitting(true)
    try {
      const response = await fetch('/api/feedback', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          feedback_text: feedbackText,
          feedback_type: feedbackType,
          context: {
            resume_content: resumeContent,
            job_description: jobDescription
          },
          suggested_improvement: suggestedImprovement || undefined
        })
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to submit feedback')
      }

      const data = await response.json()
      
      // Ask if user wants to use this for learning
      const useForLearning = window.confirm(
        'Feedback submitted! Would you like to use this feedback to improve the AI prompts?\n\n' +
        'This will help the system learn and produce better resumes in the future.'
      )

      if (useForLearning) {
        await approveFeedbackForLearning(data.feedback_id)
      }

      // Reset form
      setFeedbackText('')
      setSuggestedImprovement('')
      setShowFeedback(false)
      
      if (onFeedbackSubmitted) {
        onFeedbackSubmitted()
      }

      // Reload opportunities
      loadLearningOpportunities()
      
      alert('Feedback submitted successfully!')
    } catch (err) {
      alert(`Error: ${err.message}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const approveFeedbackForLearning = async (feedbackId) => {
    try {
      const response = await fetch('/api/feedback/approve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          feedback_id: feedbackId,
          approve: true
        })
      })

      if (response.ok) {
        console.log('Feedback approved for learning')
      }
    } catch (err) {
      console.error('Failed to approve feedback:', err)
    }
  }

  const handleUpdatePrompts = async () => {
    if (selectedOpportunities.length === 0) {
      alert('Please select at least one learning opportunity')
      return
    }

    const confirmMessage = 
      `Are you sure you want to update the prompt templates based on ${selectedOpportunities.length} feedback entries?\n\n` +
      `This will modify the system prompts and may affect future resume tailoring.`

    if (!window.confirm(confirmMessage)) {
      return
    }

    try {
      const response = await fetch('/api/prompts/update', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          feedback_ids: selectedOpportunities,
          prompt_section: 'system'
        })
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || 'Failed to update prompts')
      }

      const data = await response.json()
      alert(`Prompts updated successfully! New version: ${data.new_version}`)
      
      // Reload opportunities
      loadLearningOpportunities()
      setShowLearningModal(false)
      setSelectedOpportunities([])
    } catch (err) {
      alert(`Error: ${err.message}`)
    }
  }

  return (
    <div className="feedback-learner">
      <button
        className="feedback-button"
        onClick={() => setShowFeedback(!showFeedback)}
      >
        💡 Provide Feedback
      </button>

      {showFeedback && (
        <div className="feedback-modal">
          <div className="feedback-content">
            <h3>Help Improve the AI</h3>
            <p>Your feedback helps the system learn and produce better resumes.</p>

            <div className="feedback-form">
              <label>
                Feedback Type:
                <select
                  value={feedbackType}
                  onChange={(e) => setFeedbackType(e.target.value)}
                >
                  <option value="formatting">Formatting</option>
                  <option value="content">Content</option>
                  <option value="style">Style/Tone</option>
                  <option value="structure">Structure</option>
                  <option value="other">Other</option>
                </select>
              </label>

              <label>
                Your Feedback:
                <textarea
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  placeholder="e.g., 'Too much bold formatting in bullet points'"
                  rows={4}
                />
              </label>

              <label>
                Suggested Improvement (Optional):
                <textarea
                  value={suggestedImprovement}
                  onChange={(e) => setSuggestedImprovement(e.target.value)}
                  placeholder="e.g., 'DO NOT use bold formatting in bullet points'"
                  rows={3}
                />
              </label>

              <div className="feedback-actions">
                <button
                  onClick={handleSubmitFeedback}
                  disabled={isSubmitting || !feedbackText.trim()}
                >
                  {isSubmitting ? 'Submitting...' : 'Submit Feedback'}
                </button>
                <button
                  onClick={() => {
                    setShowFeedback(false)
                    setFeedbackText('')
                    setSuggestedImprovement('')
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {learningOpportunities.length > 0 && (
        <button
          className="learning-opportunities-button"
          onClick={() => setShowLearningModal(true)}
        >
          🧠 {learningOpportunities.length} Learning Opportunity{learningOpportunities.length !== 1 ? 'ies' : 'y'}
        </button>
      )}

      {showLearningModal && (
        <div className="learning-modal">
          <div className="learning-content">
            <h3>Learning Opportunities</h3>
            <p>These feedback entries can be incorporated into the prompt templates:</p>

            <div className="opportunities-list">
              {learningOpportunities.map((opp, idx) => (
                <div key={idx} className="opportunity-item">
                  <label>
                    <input
                      type="checkbox"
                      checked={selectedOpportunities.includes(opp.feedback_id)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedOpportunities([...selectedOpportunities, opp.feedback_id])
                        } else {
                          setSelectedOpportunities(
                            selectedOpportunities.filter(id => id !== opp.feedback_id)
                          )
                        }
                      }}
                    />
                    <div className="opportunity-content">
                      <strong>{opp.feedback_type}</strong>
                      <p>{opp.feedback_text}</p>
                      {opp.suggested_improvement && (
                        <p className="suggestion">
                          💡 {opp.suggested_improvement}
                        </p>
                      )}
                    </div>
                  </label>
                </div>
              ))}
            </div>

            <div className="learning-actions">
              <button
                onClick={handleUpdatePrompts}
                disabled={selectedOpportunities.length === 0}
              >
                Update Prompts ({selectedOpportunities.length} selected)
              </button>
              <button onClick={() => {
                setShowLearningModal(false)
                setSelectedOpportunities([])
              }}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default FeedbackLearner
