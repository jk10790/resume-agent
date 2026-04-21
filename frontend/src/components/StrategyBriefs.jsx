import React, { useCallback, useEffect, useMemo, useState } from 'react'
import './StrategyBriefs.css'

function formatEvidencePreviewItem(item) {
  if (!item) return ''
  if (typeof item === 'string') return item
  const requirement = item.requirement ? String(item.requirement).trim() : ''
  const evidence = item.evidence ? String(item.evidence).trim() : ''
  const sourceSection = item.source_section ? String(item.source_section).trim() : ''
  if (requirement && evidence) return `${requirement}: ${evidence}${sourceSection ? ` (${sourceSection})` : ''}`
  return requirement || evidence || sourceSection || ''
}

function StrategyBriefs({ onOpenInTailor, onViewApplications, initialSearchQuery = '', onConsumedInitialSearch = null }) {
  const [strategyBriefs, setStrategyBriefs] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busyBriefId, setBusyBriefId] = useState(null)

  const loadStrategyBriefs = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/job-strategy?limit=50', {
        credentials: 'include'
      })
      if (!response.ok) {
        throw new Error(`Failed to load strategy briefs (${response.status})`)
      }
      const data = await response.json()
      setStrategyBriefs(data.strategy_briefs || [])
      setError(null)
    } catch (err) {
      setStrategyBriefs([])
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStrategyBriefs()
  }, [loadStrategyBriefs])

  useEffect(() => {
    if (!initialSearchQuery) return
    setSearchQuery(initialSearchQuery)
    if (typeof onConsumedInitialSearch === 'function') {
      onConsumedInitialSearch()
    }
  }, [initialSearchQuery, onConsumedInitialSearch])

  const filteredBriefs = useMemo(() => {
    return strategyBriefs
      .filter((brief) => {
        if (statusFilter === 'all') return true
        return (brief.approval_status || 'pending') === statusFilter
      })
      .filter((brief) => {
        const query = searchQuery.trim().toLowerCase()
        if (!query) return true
        return [brief.company, brief.job_title, brief.archetype, brief.role_summary]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(query))
      })
  }, [strategyBriefs, searchQuery, statusFilter])

  const updateBriefDecision = async (briefId, action) => {
    setBusyBriefId(briefId)
    setError(null)
    try {
      const endpoint = action === 'override' ? 'override' : 'approve'
      const response = await fetch(`/api/job-strategy/${briefId}/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({})
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}))
        throw new Error(payload.detail || `Failed to ${action} strategy brief`)
      }
      const data = await response.json()
      const updatedBrief = data.strategy_brief
      setStrategyBriefs((prev) =>
        prev.map((brief) => (brief.id === briefId ? { ...brief, ...updatedBrief } : brief))
      )
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyBriefId(null)
    }
  }

  const duplicateBrief = async (briefId) => {
    setBusyBriefId(briefId)
    setError(null)
    try {
      const response = await fetch(`/api/job-strategy/${briefId}/duplicate`, {
        method: 'POST',
        credentials: 'include'
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}))
        throw new Error(payload.detail || 'Failed to duplicate strategy brief')
      }
      await loadStrategyBriefs()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyBriefId(null)
    }
  }

  const hasActiveFilters = statusFilter !== 'all' || searchQuery.trim().length > 0

  const clearFilters = () => {
    setSearchQuery('')
    setStatusFilter('all')
  }

  return (
    <div className="strategy-briefs-page">
      <div className="strategy-briefs-hero">
        <div>
          <h2>Strategy Brief Library</h2>
          <p>
            Browse saved job strategies, approve or override weak-fit gates, and open a brief in the tailoring workspace when you want to work from it.
          </p>
        </div>
        <button className="secondary-button" onClick={loadStrategyBriefs} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <div className="strategy-briefs-filters">
        <div className="form-group">
          <label>Search briefs</label>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search company, role, archetype..."
            disabled={loading}
          />
        </div>
        <div className="form-group">
          <label>Status filter</label>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} disabled={loading}>
            <option value="all">All</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="override_approved">Override Approved</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>
      </div>

      {hasActiveFilters && (
        <div className="context-banner">
          <div>
            <strong>Library is filtered.</strong>
            <span>
              {searchQuery.trim()
                ? ` Showing strategy briefs matching "${searchQuery.trim()}".`
                : ' Showing a narrowed set of saved strategy briefs.'}
            </span>
          </div>
          <button className="secondary-button" onClick={clearFilters} disabled={loading}>
            Clear Filters
          </button>
        </div>
      )}

      {error && <div className="error-message">❌ {error}</div>}

      {loading ? (
        <div className="loading">Loading strategy briefs...</div>
      ) : filteredBriefs.length === 0 ? (
        <div className="strategy-briefs-empty">
          <strong>{hasActiveFilters ? 'No strategy briefs match the current filters.' : 'No saved strategy briefs yet.'}</strong>
          <span>
            {hasActiveFilters
              ? 'Clear the filters to browse the rest of your saved strategy library.'
              : 'Run a strategy review from the Tailor tab and the saved brief will appear here.'}
          </span>
        </div>
      ) : (
        <div className="strategy-briefs-grid">
          {filteredBriefs.map((brief) => (
            <div key={brief.id} className="strategy-library-card">
              <div className="strategy-library-card-topline">
                <div>
                  <div className="strategy-library-title">
                    {brief.company || 'Unknown company'} <span>·</span> {brief.job_title || 'Unknown role'}
                  </div>
                  <div className="strategy-library-meta">
                    <span>{brief.archetype || 'general'}</span>
                    <span>Target {(brief.target_alignment || 'unranked').replace(/_/g, ' ')}</span>
                    <span>Fit {brief.fit_score}/10</span>
                    <span>Gate {(brief.gating_decision || 'proceed').replace(/_/g, ' ')}</span>
                  </div>
                </div>
                <span className={`strategy-status-pill status-${brief.approval_status || 'pending'}`}>
                  {(brief.approval_status || 'pending').replace(/_/g, ' ')}
                </span>
              </div>

              <p className="strategy-library-summary">
                {brief.role_summary || `${brief.archetype || 'general'} role strategy`}
              </p>

              <div className="strategy-library-footer">
                <span>Updated {brief.updated_at ? new Date(brief.updated_at).toLocaleDateString() : 'n/a'}</span>
                {(brief.provenance?.evidence_sections || []).length > 0 && (
                  <span>Grounded in {(brief.provenance.evidence_sections || []).join(', ')}</span>
                )}
                {(brief.provenance?.blocker_reason_codes || []).length > 0 && (
                  <span>Blockers {(brief.provenance.blocker_reason_codes || []).join(', ').replace(/_/g, ' ')}</span>
                )}
              </div>

              {!!brief.provenance?.sample_evidence?.length && (
                <p className="strategy-library-summary strategy-library-evidence-preview">
                  <strong>Evidence preview:</strong> {brief.provenance.sample_evidence.map(formatEvidencePreviewItem).filter(Boolean).join(' • ')}
                </p>
              )}

              <div className="strategy-library-actions">
                <button
                  className="primary-button"
                  onClick={() => onOpenInTailor?.(brief.id)}
                  disabled={busyBriefId === brief.id}
                >
                  {busyBriefId === brief.id ? 'Working...' : 'Open in Tailor'}
                </button>
                <button
                  className="secondary-button"
                  onClick={() => onViewApplications?.(`${brief.company || ''} ${brief.job_title || ''}`.trim())}
                  disabled={busyBriefId === brief.id}
                >
                  View Applications
                </button>
                <button
                  className="secondary-button"
                  onClick={() => duplicateBrief(brief.id)}
                  disabled={busyBriefId === brief.id}
                >
                  Duplicate
                </button>
                {brief.approval_status !== 'approved' && brief.approval_status !== 'override_approved' && (
                  <button
                    className="secondary-button"
                    onClick={() => updateBriefDecision(brief.id, 'approve')}
                    disabled={busyBriefId === brief.id}
                  >
                    Approve
                  </button>
                )}
                {brief.gating_decision === 'stop_and_ask' && brief.approval_status !== 'override_approved' && (
                  <button
                    className="secondary-button"
                    onClick={() => updateBriefDecision(brief.id, 'override')}
                    disabled={busyBriefId === brief.id}
                  >
                    Override Weak Fit
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default StrategyBriefs
