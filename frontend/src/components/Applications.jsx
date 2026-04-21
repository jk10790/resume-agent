import React, { useState, useEffect } from 'react'
import './Applications.css'

function formatEvidencePreviewItem(item) {
  if (!item) return ''
  if (typeof item === 'string') return item
  const requirement = item.requirement ? String(item.requirement).trim() : ''
  const evidence = item.evidence ? String(item.evidence).trim() : ''
  const sourceSection = item.source_section ? String(item.source_section).trim() : ''
  if (requirement && evidence) return `${requirement}: ${evidence}${sourceSection ? ` (${sourceSection})` : ''}`
  return requirement || evidence || sourceSection || ''
}

function formatStrategyEventLabel(eventType) {
  if (!eventType) return 'No recent strategy events'
  return eventType
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function Applications({
  initialSearchQuery = '',
  onConsumedInitialSearch = null,
  onOpenStrategyInTailor = null,
  onBrowseStrategies = null,
}) {
  const [applications, setApplications] = useState([])
  const [stats, setStats] = useState(null)
  const [patterns, setPatterns] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('All')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const statusOptions = ['All', 'Applied', 'Interview', 'Rejected', 'Offer', 'Withdrawn']

  useEffect(() => {
    loadApplications()
    loadStatistics()
    loadPatterns()
  }, [statusFilter, searchQuery])

  useEffect(() => {
    if (!initialSearchQuery) return
    setSearchQuery(initialSearchQuery)
    if (typeof onConsumedInitialSearch === 'function') {
      onConsumedInitialSearch()
    }
  }, [initialSearchQuery, onConsumedInitialSearch])

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

  const loadPatterns = async () => {
    try {
      const response = await fetch('/api/applications/patterns')
      if (response.ok) {
        const data = await response.json()
        setPatterns(data)
      }
    } catch (err) {
      console.error('Failed to load pattern analysis:', err)
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

  const focusRecommendations = [
    patterns?.archetype_breakdown?.[0]?.archetype
      ? {
          label: 'Browse best-fit strategies',
          action: () => onBrowseStrategies?.(patterns.archetype_breakdown[0].archetype),
        }
      : null,
    (patterns?.by_outcome?.positive || 0) > 0
      ? {
          label: 'Show interview pipeline',
          action: () => {
            setSearchQuery('')
            setStatusFilter('Interview')
          },
        }
      : null,
  ].filter(Boolean)

  const hasActiveFilters = statusFilter !== 'All' || searchQuery.trim().length > 0

  const clearFilters = () => {
    setSearchQuery('')
    setStatusFilter('All')
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

      {patterns && patterns.total_applications > 0 && (
        <div className="pattern-analysis-panel">
          <div className="pattern-analysis-header">
            <div>
              <h3>Strategy Patterns</h3>
              <p>What your tracked outcomes are saying about fit, blockers, and target role focus.</p>
            </div>
            {patterns.fit_floor_recommendation != null && (
              <span className="pattern-chip">Current fit floor: {patterns.fit_floor_recommendation}/10</span>
            )}
          </div>
          <div className="pattern-analysis-grid">
            <div className="pattern-card">
              <strong>Outcomes</strong>
              <span>Positive {patterns.by_outcome?.positive || 0}</span>
              <span>Negative {patterns.by_outcome?.negative || 0}</span>
              <span>Pending {patterns.by_outcome?.pending || 0}</span>
            </div>
            <div className="pattern-card">
              <strong>Best Archetype</strong>
              <span>{patterns.archetype_breakdown?.[0]?.archetype || 'Not enough data'}</span>
              {patterns.archetype_breakdown?.[0] && (
                <small>{patterns.archetype_breakdown[0].conversion_rate}% conversion</small>
              )}
            </div>
            <div className="pattern-card">
              <strong>Strongest Target Fit</strong>
              <span>{patterns.target_alignment_breakdown?.[0]?.target_alignment || 'unranked'}</span>
              {patterns.target_alignment_breakdown?.[0] && (
                <small>{patterns.target_alignment_breakdown[0].conversion_rate}% conversion</small>
              )}
            </div>
            <div className="pattern-card">
              <strong>Top Blocker</strong>
              <span>{patterns.blocker_reason_codes?.[0]?.reason_code?.replace(/_/g, ' ') || 'No blocker trends yet'}</span>
              {patterns.blocker_reason_codes?.[0] && (
                <small>{patterns.blocker_reason_codes[0].frequency} applications</small>
              )}
            </div>
          </div>
          {(patterns.recommendations || []).length > 0 && (
            <div className="pattern-recommendations">
              {patterns.recommendations.map((recommendation) => (
                <div key={recommendation} className="pattern-recommendation-item">
                  {recommendation}
                </div>
              ))}
            </div>
          )}
          {focusRecommendations.length > 0 && (
            <div className="pattern-actions">
              {focusRecommendations.map((item) => (
                <button
                  key={item.label}
                  className="secondary-button"
                  onClick={() => {
                    item.action?.()
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          )}
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

      {hasActiveFilters && (
        <div className="context-banner">
          <div>
            <strong>Tracker is filtered.</strong>
            <span>
              {searchQuery.trim()
                ? ` Showing applications matching "${searchQuery.trim()}".`
                : ` Showing only ${statusFilter.toLowerCase()} applications.`}
            </span>
          </div>
          <button className="secondary-button" onClick={clearFilters}>
            Clear Filters
          </button>
        </div>
      )}

      {error && (
        <div className="error-message">
          ❌ {error}
        </div>
      )}

      {loading ? (
        <div className="loading">Loading applications...</div>
      ) : applications.length === 0 ? (
        <div className="no-applications">
          {hasActiveFilters ? 'No applications match the current filters.' : 'No applications found.'}
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

                {app.strategy_brief && (
                  <div className="strategy-brief-panel">
                    <div className="strategy-brief-header">
                      <h4>Linked Strategy Brief</h4>
                      <span className={`strategy-status status-${app.strategy_brief.approval_status || 'pending'}`}>
                        {(app.strategy_brief.approval_status || 'pending').replace(/_/g, ' ')}
                      </span>
                    </div>
                    <div className="strategy-brief-grid">
                      <div className="detail-item">
                        <strong>Archetype:</strong> {app.strategy_brief.archetype || 'General'}
                      </div>
                      <div className="detail-item">
                        <strong>Target Fit:</strong> {(app.strategy_brief.target_alignment || 'unranked').replace(/_/g, ' ')}
                      </div>
                      <div className="detail-item">
                        <strong>Fit:</strong> {app.strategy_brief.fit_score ? `${app.strategy_brief.fit_score}/10` : 'N/A'}
                      </div>
                      <div className="detail-item">
                        <strong>Gate:</strong> {(app.strategy_brief.gating_decision || 'proceed').replace(/_/g, ' ')}
                      </div>
                      <div className="detail-item">
                        <strong>Last Strategy Event:</strong> {formatStrategyEventLabel(app.strategy_brief.last_event_type)}
                      </div>
                    </div>
                    {app.strategy_brief.role_summary && (
                      <p className="strategy-summary">{app.strategy_brief.role_summary}</p>
                    )}
                    {!!app.strategy_brief.provenance?.evidence_sections?.length && (
                      <p className="strategy-summary">
                        <strong>Grounding:</strong> {app.strategy_brief.provenance.evidence_sections.join(', ')}
                      </p>
                    )}
                    {!!app.strategy_brief.provenance?.blocker_reason_codes?.length && (
                      <p className="strategy-summary">
                        <strong>Blockers:</strong> {app.strategy_brief.provenance.blocker_reason_codes.join(', ').replace(/_/g, ' ')}
                      </p>
                    )}
                    {!!app.strategy_brief.provenance?.sample_evidence?.length && (
                      <p className="strategy-summary">
                        <strong>Evidence preview:</strong> {app.strategy_brief.provenance.sample_evidence.map(formatEvidencePreviewItem).filter(Boolean).join(' • ')}
                      </p>
                    )}
                  </div>
                )}

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
                    {app.strategy_brief?.id && (
                      <span className="strategy-link-note">
                        Strategy brief #{app.strategy_brief.id}
                      </span>
                    )}
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
                  {app.strategy_brief?.id && (
                    <div className="application-inline-actions">
                      <button
                        className="secondary-button"
                        onClick={() => onOpenStrategyInTailor?.(app.strategy_brief.id)}
                      >
                        Open Strategy in Tailor
                      </button>
                      <button
                        className="secondary-button"
                        onClick={() => onBrowseStrategies?.(`${app.company || ''} ${app.job_title || ''}`.trim())}
                      >
                        Browse Matching Strategies
                      </button>
                    </div>
                  )}
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
