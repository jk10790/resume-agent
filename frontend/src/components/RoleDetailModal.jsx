import React, { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

const DISMISS_REASONS = [
  'wrong role family',
  'wrong seniority',
  'wrong location or remote',
  'too frontend-heavy',
  'too managerial',
  'too customer-facing',
  'wrong domain',
  'tech mismatch',
  'company type not right',
  'duplicate or stale',
]

function fitTone(score) {
  if (score === null || score === undefined) return 'unknown'
  if (score >= 7) return 'high'
  if (score >= 5) return 'medium'
  return 'low'
}

function formatTimestamp(value) {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed.toLocaleString()
}

export default function RoleDetailModal({
  roleId,
  archetypeLabel,
  onClose,
  onShortlist,
  onDismiss,
  onRestore,
  onOpenInTailor,
  onRoleChanged,
}) {
  const [role, setRole] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [evaluating, setEvaluating] = useState(false)
  const [busyAction, setBusyAction] = useState('')
  const [showDismiss, setShowDismiss] = useState(false)
  const [dismissReasons, setDismissReasons] = useState([])
  const [dismissComment, setDismissComment] = useState('')

  const loadRole = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`/api/discover/roles/${roleId}`, { credentials: 'include' })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload.detail || 'Failed to load this role')
      setRole(payload)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [roleId])

  useEffect(() => {
    loadRole()
  }, [loadRole])

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  // Freeze the page behind the dialog. Without this the inbox keeps scrolling
  // under the backdrop and the modal drifts off screen while it is open.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [])

  const handleEvaluate = async () => {
    setEvaluating(true)
    setError('')
    try {
      const response = await fetch(`/api/discover/roles/${roleId}/evaluate-fit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({}),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload.detail || 'Fit evaluation failed')
      setRole((prev) => ({ ...prev, ...payload.role }))
      if (typeof onRoleChanged === 'function') onRoleChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setEvaluating(false)
    }
  }

  const runAction = async (name, action) => {
    setBusyAction(name)
    setError('')
    try {
      await action()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyAction('')
    }
  }

  const isDismissed = role?.inbox_state === 'dismissed'
  const evaluation = role?.fit_evaluation || null
  const fitScore = role?.fit_score ?? null
  const evaluatedAt = formatTimestamp(role?.fit_evaluated_at)
  const lowConfidence = Number(role?.extraction_confidence || 0) < 0.6

  // Rendered through a portal on purpose: `.main-content` carries a
  // backdrop-filter, which makes it the containing block for position:fixed
  // descendants. Left in the tree, the dialog anchors to that box instead of
  // the viewport and lands thousands of pixels down the page.
  return createPortal(
    <div className="discover-modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="discover-modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Job description"
      >
        {loading ? (
          <div className="discover-modal-loading">Loading role...</div>
        ) : !role ? (
          <div className="discover-modal-loading">
            <p>{error || 'This role could not be loaded.'}</p>
            <button type="button" className="discover-secondary-button" onClick={onClose}>
              Close
            </button>
          </div>
        ) : (
          <>
            <div className="discover-modal-header">
              <div>
                <div className="discover-modal-company">{role.company || 'Unknown company'}</div>
                <h3 className="discover-modal-title">{role.job_title || 'Untitled role'}</h3>
              </div>
              <button type="button" className="discover-modal-close" onClick={onClose} aria-label="Close">
                ×
              </button>
            </div>

            <div className="discover-modal-meta">
              <span>{role.location || 'Location unavailable'}</span>
              <span>{role.remote_mode || 'unknown'}</span>
              <span>{role.posted_label || 'Date unavailable'}</span>
              <span>{archetypeLabel ? archetypeLabel(role.archetype) : role.archetype}</span>
              {role.compensation && <span>{role.compensation}</span>}
              <span>{role.source_domain}</span>
              {lowConfidence && <span className="discover-confidence-badge">Low confidence</span>}
              {isDismissed && <span className="discover-modal-state">Dismissed</span>}
            </div>

            {(role.matched_filters?.length > 0 || role.possible_blockers?.length > 0) && (
              <div className="discover-pill-groups">
                {(role.matched_filters || []).map((item) => (
                  <span key={`match-${item}`} className="discover-pill match">
                    {item}
                  </span>
                ))}
                {(role.possible_blockers || []).map((item) => (
                  <span key={`block-${item}`} className="discover-pill blocker">
                    {item}
                  </span>
                ))}
              </div>
            )}

            {error && <div className="discover-inline-error">{error}</div>}

            <div className={`discover-fit-panel tone-${fitTone(fitScore)}`}>
              {fitScore === null ? (
                <div className="discover-fit-empty">
                  <div>
                    <strong>Fit not evaluated yet</strong>
                    <p>Score this posting against your resume before spending a tailoring run on it.</p>
                  </div>
                  <button
                    type="button"
                    className="discover-primary-button"
                    onClick={handleEvaluate}
                    disabled={evaluating}
                  >
                    {evaluating ? 'Evaluating...' : 'Evaluate fit'}
                  </button>
                </div>
              ) : (
                <>
                  <div className="discover-fit-header">
                    <div className="discover-fit-score">
                      <span className="discover-fit-number">{fitScore}</span>
                      <span className="discover-fit-denominator">/10</span>
                    </div>
                    <div className="discover-fit-verdict">
                      <strong>{role.fit_should_apply ? 'Worth applying' : 'Probably not worth applying'}</strong>
                      {evaluation?.confidence && <span>Confidence: {evaluation.confidence}</span>}
                      {evaluatedAt && <span>Evaluated {evaluatedAt}</span>}
                    </div>
                    <button
                      type="button"
                      className="discover-secondary-button"
                      onClick={handleEvaluate}
                      disabled={evaluating}
                    >
                      {evaluating ? 'Evaluating...' : 'Re-evaluate'}
                    </button>
                  </div>
                  <div className="discover-fit-columns">
                    {evaluation?.matching_areas?.length > 0 && (
                      <div>
                        <h4>Matches</h4>
                        <ul>
                          {evaluation.matching_areas.map((item, idx) => (
                            <li key={`match-area-${idx}`}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {evaluation?.missing_areas?.length > 0 && (
                      <div>
                        <h4>Gaps</h4>
                        <ul>
                          {evaluation.missing_areas.map((item, idx) => (
                            <li key={`missing-area-${idx}`}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {evaluation?.recommendations?.length > 0 && (
                      <div>
                        <h4>Recommendations</h4>
                        <ul>
                          {evaluation.recommendations.map((item, idx) => (
                            <li key={`rec-${idx}`}>{item}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>

            <div className="discover-jd-block">
              <div className="discover-jd-header">
                <h4>Job description</h4>
                <a href={role.apply_url || role.canonical_url} target="_blank" rel="noreferrer">
                  Open original posting
                </a>
              </div>
              {lowConfidence && (
                <p className="discover-jd-warning">
                  Extracted text may be partial — open the original posting to confirm before tailoring.
                </p>
              )}
              <pre className="discover-jd-text">{role.raw_text || 'No job description text was stored for this role.'}</pre>
            </div>

            {role.feedback_events?.length > 0 && (
              <div className="discover-feedback-log">
                <h4>Your feedback</h4>
                <ul>
                  {role.feedback_events.map((event) => (
                    <li key={event.id}>
                      <span className="discover-feedback-decision">{event.decision.replace(/_/g, ' ')}</span>
                      {event.reasons?.length > 0 && <span>{event.reasons.join(', ')}</span>}
                      {event.comment && <span>“{event.comment}”</span>}
                      <span className="discover-feedback-date">{formatTimestamp(event.created_at)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {showDismiss && (
              <div className="discover-dismiss-form">
                <div className="discover-chip-row">
                  {DISMISS_REASONS.map((reason) => (
                    <button
                      key={reason}
                      type="button"
                      className={dismissReasons.includes(reason) ? 'discover-chip active' : 'discover-chip'}
                      onClick={() => {
                        setDismissReasons((prev) =>
                          prev.includes(reason) ? prev.filter((item) => item !== reason) : [...prev, reason]
                        )
                      }}
                    >
                      {reason}
                    </button>
                  ))}
                </div>
                <textarea
                  value={dismissComment}
                  onChange={(event) => setDismissComment(event.target.value)}
                  placeholder="Optional comment"
                />
                <div className="discover-actions">
                  <button
                    type="button"
                    className="discover-primary-button"
                    disabled={busyAction === 'dismiss'}
                    onClick={() =>
                      runAction('dismiss', async () => {
                        await onDismiss(role.id, dismissReasons, dismissComment)
                        onClose()
                      })
                    }
                  >
                    Save feedback
                  </button>
                  <button type="button" className="discover-secondary-button" onClick={() => setShowDismiss(false)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <div className="discover-modal-actions">
              {isDismissed ? (
                <>
                  <button
                    type="button"
                    className="discover-secondary-button"
                    disabled={busyAction === 'restore'}
                    onClick={() => runAction('restore', async () => { await onRestore(role.id); await loadRole() })}
                  >
                    Restore to inbox
                  </button>
                  <span className="discover-modal-hint">Restore this role before opening it in Tailor.</span>
                </>
              ) : (
                <>
                  {role.inbox_state !== 'shortlisted' && (
                    <button
                      type="button"
                      className="discover-secondary-button"
                      disabled={busyAction === 'shortlist'}
                      onClick={() => runAction('shortlist', async () => { await onShortlist(role.id); await loadRole() })}
                    >
                      Shortlist
                    </button>
                  )}
                  {!showDismiss && (
                    <button type="button" className="discover-secondary-button" onClick={() => setShowDismiss(true)}>
                      Not relevant
                    </button>
                  )}
                  <button
                    type="button"
                    className="discover-primary-button"
                    disabled={busyAction === 'tailor'}
                    onClick={() => runAction('tailor', async () => { await onOpenInTailor(role.id) })}
                  >
                    Tailor resume →
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
