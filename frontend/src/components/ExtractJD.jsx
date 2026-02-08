import React, { useState } from 'react'
import './ExtractJD.css'

function ExtractJD() {
  const [jobUrl, setJobUrl] = useState('')
  const [jdText, setJdText] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState(null)

  const handleExtract = async () => {
    if (!jobUrl) {
      setError('Please enter a job URL')
      return
    }

    setIsLoading(true)
    setError(null)
    setJdText('')

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
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="extract-jd">
      <h2>Extract Job Description</h2>
      
      <div className="form-group">
        <label>Job Listing URL *</label>
        <input
          type="url"
          value={jobUrl}
          onChange={(e) => setJobUrl(e.target.value)}
          placeholder="https://..."
          disabled={isLoading}
        />
      </div>

      {error && (
        <div className="error-message">
          ❌ {error}
        </div>
      )}

      <button
        className="primary-button"
        onClick={handleExtract}
        disabled={isLoading || !jobUrl}
      >
        {isLoading ? 'Extracting...' : 'Extract Job Description'}
      </button>

      {jdText && (
        <div className="result-container">
          <h3>✅ Job Description Extracted</h3>
          <textarea
            readOnly
            value={jdText}
            rows={20}
            className="jd-text"
          />
        </div>
      )}
    </div>
  )
}

export default ExtractJD
