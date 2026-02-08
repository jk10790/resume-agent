import React, { useState, useEffect } from 'react'
import './Applications.css'

function Applications() {
  const [applications, setApplications] = useState([])
  const [stats, setStats] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('All')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const statusOptions = ['All', 'Applied', 'Interview', 'Rejected', 'Offer', 'Withdrawn']

  useEffect(() => {
    loadApplications()
    loadStatistics()
  }, [statusFilter, searchQuery])

  const loadApplications = async () => {
    try {
      setLoading(true)
      let url = '/api/applications?'
      if (searchQuery) {
        url += `search=${encodeURIComponent(searchQuery)}`
      } else if (statusFilter !== 'All') {
        url += `status=${encodeURIComponent(statusFilter.toLowerCase())}`
      }

      const response = await fetch(url)
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const data = await response.json()
      setApplications(data.applications || [])
      setError(null)
    } catch (err) {
      setError(err.message)
      setApplications([])
    } finally {
      setLoading(false)
    }
  }

  const loadStatistics = async () => {
    try {
      const response = await fetch('/api/applications/stats')
      if (response.ok) {
        const data = await response.json()
        setStats(data)
      }
    } catch (err) {
      // Stats are optional, don't show error
      console.error('Failed to load statistics:', err)
    }
  }

  const updateStatus = async (appId, newStatus) => {
    try {
      const response = await fetch(`/api/applications/${appId}/status`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ status: newStatus })
      })

      if (!response.ok) {
        throw new Error('Failed to update status')
      }

      // Reload applications
      loadApplications()
      loadStatistics()
    } catch (err) {
      alert(`Failed to update status: ${err.message}`)
    }
  }

  return (
    <div className="applications">
      <h2>📊 Application Tracker</h2>
      <p>View and manage your job applications</p>

      {stats && (
        <div className="stats-container">
          <div className="stat-item">
            <div className="stat-value">{stats.total || 0}</div>
            <div className="stat-label">Total</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.active || 0}</div>
            <div className="stat-label">Active</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.avg_fit_score ? stats.avg_fit_score.toFixed(1) : 'N/A'}</div>
            <div className="stat-label">Avg Fit Score</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.interview || 0}</div>
            <div className="stat-label">Interviews</div>
          </div>
        </div>
      )}

      <div className="filters">
        <div className="form-group">
          <label>🔍 Search</label>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Company name or job title..."
          />
        </div>
        <div className="form-group">
          <label>Filter by Status</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {statusOptions.map(status => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="error-message">
          ❌ {error}
        </div>
      )}

      {loading ? (
        <div className="loading">Loading applications...</div>
      ) : applications.length === 0 ? (
        <div className="no-applications">
          No applications found.
        </div>
      ) : (
        <>
          <div className="applications-count">
            Found {applications.length} application(s)
          </div>
          <div className="applications-list">
            {applications.map(app => (
              <div key={app.id} className="application-card">
                <div className="application-header">
                  <h3>{app.job_title}</h3>
                  <span className="company-name">at {app.company}</span>
                </div>
                
                <div className="application-details">
                  <div className="detail-item">
                    <strong>Status:</strong> {app.status || 'Applied'}
                  </div>
                  <div className="detail-item">
                    <strong>Fit Score:</strong> {app.fit_score ? `${app.fit_score}/10` : 'N/A'}
                  </div>
                  <div className="detail-item">
                    <strong>Date:</strong> {app.application_date ? new Date(app.application_date).toLocaleDateString() : 'N/A'}
                  </div>
                </div>

                {app.job_url && (
                  <div className="application-links">
                    <a href={app.job_url} target="_blank" rel="noopener noreferrer">
                      🔗 Job Posting
                    </a>
                  </div>
                )}

                {app.resume_doc_id && (
                  <div className="application-links">
                    <a
                      href={`https://docs.google.com/document/d/${app.resume_doc_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      📄 Tailored Resume
                    </a>
                  </div>
                )}

                <div className="application-actions">
                  <label>
                    <strong>Update Status:</strong>
                    <select
                      value={app.status || 'applied'}
                      onChange={(e) => updateStatus(app.id, e.target.value)}
                    >
                      {statusOptions.filter(s => s !== 'All').map(status => (
                        <option key={status} value={status.toLowerCase()}>
                          {status}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

export default Applications
