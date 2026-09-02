import React, { useEffect, useMemo, useState } from 'react'
import RoleDetailModal from './RoleDetailModal'
import './DiscoverRoles.css'

const ARCHETYPE_OPTIONS = [
  { value: 'software_engineering', label: 'Software / Product Engineering' },
  { value: 'platform_infrastructure', label: 'Platform / Infrastructure / SRE' },
  { value: 'data_ml_ai', label: 'Data / ML / AI Engineering' },
  { value: 'applied_ai_llmops', label: 'Applied AI / LLMOps / Agentic Systems' },
  { value: 'product_technical_product', label: 'Product / Technical Product' },
  { value: 'solutions_customer_engineering', label: 'Solutions / Customer / Sales Engineering' },
]

const SENIORITY_OPTIONS = ['any', 'junior', 'mid', 'senior', 'staff', 'principal', 'manager', 'director']
const REMOTE_MODE_OPTIONS = ['remote', 'hybrid', 'onsite']
const INBOX_FILTERS = ['active', 'shortlisted', 'dismissed', 'all']
const MAX_VISIBLE_PILLS = 6
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

const EMPTY_FORM = {
  search_intent: '',
  role_families: [],
  seniority: 'any',
  remote_modes: [],
  include_locations: '',
  exclude_locations: '',
  must_have_keywords: '',
  avoid_keywords: '',
  prefer_visa_sponsorship: false,
}

function parseCsv(text) {
  return String(text || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function archetypeLabel(value) {
  return ARCHETYPE_OPTIONS.find((option) => option.value === value)?.label || value || 'Unknown'
}

function titleCase(value) {
  const text = String(value || '').trim()
  return text ? text[0].toUpperCase() + text.slice(1) : ''
}

function pluralize(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`
}

function resultSourceLabel(source) {
  if (source === 'cache') return 'Cached results'
  if (source === 'stale_cache_fallback') return 'Cached fallback'
  return 'Fresh search'
}

// Only surface facts the posting actually carries; blank fields used to render
// as a run of "Unknown" / "unavailable" placeholders that read as broken data.
function metaItems(role) {
  const archetype = role.archetype ? archetypeLabel(role.archetype) : ''
  return [role.location, titleCase(role.remote_mode), role.posted_label, archetype, role.compensation, role.source_domain]
    .map((value) => String(value || '').trim())
    .filter((value) => value && value.toLowerCase() !== 'unknown' && !value.toLowerCase().endsWith('unavailable'))
}

function fitTone(score) {
  if (score === null || score === undefined) return 'unknown'
  if (score >= 7) return 'high'
  if (score >= 5) return 'medium'
  return 'low'
}

function formFromCriteria(criteria = {}) {
  return {
    search_intent: criteria.search_intent || '',
    role_families: criteria.role_families || [],
    seniority: criteria.seniority || 'any',
    remote_modes: criteria.remote_modes || [],
    include_locations: Array.isArray(criteria.include_locations) ? criteria.include_locations.join(', ') : '',
    exclude_locations: Array.isArray(criteria.exclude_locations) ? criteria.exclude_locations.join(', ') : '',
    must_have_keywords: Array.isArray(criteria.must_have_keywords) ? criteria.must_have_keywords.join(', ') : '',
    avoid_keywords: Array.isArray(criteria.avoid_keywords) ? criteria.avoid_keywords.join(', ') : '',
    prefer_visa_sponsorship: Boolean(criteria.prefer_visa_sponsorship),
  }
}

function DiscoverShell({ children }) {
  return (
    <div className="discover-page">
      <div className="discover-hero">
        <div className="discover-hero-copy">
          <h2>Discover Roles</h2>
          <p>Search broadly, review quickly, and only run strategy evaluation on roles you choose.</p>
        </div>
      </div>
      {children}
    </div>
  )
}

function EmptyState({ icon, title, children }) {
  return (
    <div className="discover-empty-card">
      <span className="discover-empty-icon" aria-hidden="true">
        {icon}
      </span>
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  )
}

function ResultsSkeleton() {
  return (
    <div className="discover-results-grid" aria-hidden="true">
      {[0, 1, 2, 3].map((key) => (
        <div key={key} className="discover-skeleton-card">
          <div className="discover-skeleton-line w-40" />
          <div className="discover-skeleton-line w-90" />
          <div className="discover-skeleton-line w-70" />
          <div className="discover-skeleton-line w-90" />
        </div>
      ))}
    </div>
  )
}

export default function DiscoverRoles({ isAuthenticated, onOpenInTailor }) {
  const [status, setStatus] = useState({ enabled: false, configured: false, provider: 'none', reason: null })
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [searching, setSearching] = useState(false)
  const [loadingInbox, setLoadingInbox] = useState(false)
  const [savingSearch, setSavingSearch] = useState(false)
  const [savedSearchName, setSavedSearchName] = useState('')
  const [savedSearches, setSavedSearches] = useState([])
  const [defaultPreferences, setDefaultPreferences] = useState({})
  const [analytics, setAnalytics] = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const [error, setError] = useState('')
  const [resultMeta, setResultMeta] = useState(null)
  const [roles, setRoles] = useState([])
  const [inboxFilter, setInboxFilter] = useState('active')
  const [inboxSearch, setInboxSearch] = useState('')
  const [pendingRoleId, setPendingRoleId] = useState(null)
  const [inboxSort, setInboxSort] = useState('default')
  const [evaluatedOnly, setEvaluatedOnly] = useState(false)
  const [detailRoleId, setDetailRoleId] = useState(null)
  const [dismissingRoleId, setDismissingRoleId] = useState(null)
  const [dismissReasons, setDismissReasons] = useState([])
  const [dismissComment, setDismissComment] = useState('')
  const [form, setForm] = useState(EMPTY_FORM)

  const filterIsActive = useMemo(
    () => inboxFilter !== 'active' || inboxSearch.trim().length > 0 || evaluatedOnly || inboxSort !== 'default',
    [inboxFilter, inboxSearch, evaluatedOnly, inboxSort]
  )

  useEffect(() => {
    let cancelled = false
    const loadStatus = async () => {
      setLoadingStatus(true)
      try {
        const response = await fetch('/api/discover/status', { credentials: 'include' })
        const data = await response.json()
        if (!cancelled) setStatus(data)
      } catch (err) {
        if (!cancelled)
          setStatus({ enabled: false, configured: false, provider: 'none', reason: 'Failed to load discovery status.' })
      } finally {
        if (!cancelled) setLoadingStatus(false)
      }
    }
    loadStatus()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!isAuthenticated || !status.configured) return
    loadInbox(inboxFilter, inboxSearch)
  }, [isAuthenticated, status.configured]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isAuthenticated || !status.configured) return
    let cancelled = false
    const loadDiscoveryMeta = async () => {
      try {
        const [savedRes, prefsRes, analyticsRes, suggestionsRes] = await Promise.all([
          fetch('/api/discover/saved-searches', { credentials: 'include' }),
          fetch('/api/discover/preferences', { credentials: 'include' }),
          fetch('/api/discover/analytics', { credentials: 'include' }),
          fetch('/api/discover/suggestions', { credentials: 'include' }),
        ])
        const [savedPayload, prefsPayload, analyticsPayload, suggestionsPayload] = await Promise.all([
          savedRes.json().catch(() => ({})),
          prefsRes.json().catch(() => ({})),
          analyticsRes.json().catch(() => ({})),
          suggestionsRes.json().catch(() => ({})),
        ])
        if (cancelled) return
        setSavedSearches(savedPayload.saved_searches || [])
        setDefaultPreferences(prefsPayload.defaults || {})
        setAnalytics(analyticsPayload || null)
        setSuggestions(suggestionsPayload.suggestions || [])
        if (prefsPayload.defaults && Object.keys(prefsPayload.defaults).length > 0) {
          setForm((prev) => ({ ...prev, ...formFromCriteria(prefsPayload.defaults) }))
        }
      } catch (err) {
        if (!cancelled) setError('Failed to load discovery preferences and analytics')
      }
    }
    loadDiscoveryMeta()
    return () => {
      cancelled = true
    }
  }, [isAuthenticated, status.configured])

  const loadInbox = async (
    state = inboxFilter,
    search = inboxSearch,
    sort = inboxSort,
    onlyEvaluated = evaluatedOnly
  ) => {
    setLoadingInbox(true)
    try {
      const params = new URLSearchParams({
        inbox_state: state,
        limit: '100', // API caps at 100; the catalog now yields well over 50
      })
      if (search.trim()) params.set('search', search.trim())
      if (sort !== 'default') params.set('sort', sort)
      if (onlyEvaluated) params.set('evaluated_only', 'true')
      const response = await fetch(`/api/discover/roles?${params.toString()}`, { credentials: 'include' })
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}))
        throw new Error(payload.detail || 'Failed to load discover inbox')
      }
      const payload = await response.json()
      setRoles(payload.roles || [])
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoadingInbox(false)
    }
  }

  const currentCriteria = () => ({
    search_intent: form.search_intent,
    role_families: form.role_families,
    seniority: form.seniority,
    remote_modes: form.remote_modes,
    include_locations: parseCsv(form.include_locations),
    exclude_locations: parseCsv(form.exclude_locations),
    must_have_keywords: parseCsv(form.must_have_keywords),
    avoid_keywords: parseCsv(form.avoid_keywords),
    prefer_visa_sponsorship: Boolean(form.prefer_visa_sponsorship),
    page_size: 20,
  })

  const updateArrayField = (field, value) => {
    setForm((prev) => {
      const values = prev[field] || []
      const nextValues = values.includes(value) ? values.filter((item) => item !== value) : [...values, value]
      return { ...prev, [field]: nextValues }
    })
  }

  const clearForm = () => {
    setForm(EMPTY_FORM)
    setError('')
  }

  // `refresh` bypasses the query cache (6h) *and* the ATS feed cache, so the
  // same criteria can be re-run instead of replaying the ids from last time.
  const handleSearch = async ({ refresh = false } = {}) => {
    if (!form.search_intent.trim() && form.role_families.length === 0) {
      setError('Add search intent or at least one role family.')
      return
    }
    setSearching(true)
    setError('')
    try {
      const response = await fetch('/api/discover/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          search_intent: form.search_intent,
          role_families: form.role_families,
          seniority: form.seniority,
          remote_modes: form.remote_modes,
          include_locations: parseCsv(form.include_locations),
          exclude_locations: parseCsv(form.exclude_locations),
          must_have_keywords: parseCsv(form.must_have_keywords),
          avoid_keywords: parseCsv(form.avoid_keywords),
          prefer_visa_sponsorship: Boolean(form.prefer_visa_sponsorship),
          page_size: 20,
          refresh,
        }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || 'Discover search failed')
      }
      setRoles(payload.roles || [])
      setResultMeta({ count: (payload.roles || []).length, source: payload.result_source, warnings: payload.warnings || [] })
      setInboxFilter('active')
      setInboxSearch('')
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }

  const refreshInbox = async (state = inboxFilter, search = inboxSearch) => {
    await loadInbox(state, search, inboxSort, evaluatedOnly)
  }

  const refreshDiscoveryMeta = async () => {
    const [savedRes, prefsRes, analyticsRes, suggestionsRes] = await Promise.all([
      fetch('/api/discover/saved-searches', { credentials: 'include' }),
      fetch('/api/discover/preferences', { credentials: 'include' }),
      fetch('/api/discover/analytics', { credentials: 'include' }),
      fetch('/api/discover/suggestions', { credentials: 'include' }),
    ])
    const [savedPayload, prefsPayload, analyticsPayload, suggestionsPayload] = await Promise.all([
      savedRes.json().catch(() => ({})),
      prefsRes.json().catch(() => ({})),
      analyticsRes.json().catch(() => ({})),
      suggestionsRes.json().catch(() => ({})),
    ])
    setSavedSearches(savedPayload.saved_searches || [])
    setDefaultPreferences(prefsPayload.defaults || {})
    setAnalytics(analyticsPayload || null)
    setSuggestions(suggestionsPayload.suggestions || [])
  }

  const handleSaveSearch = async ({ asDefault = false } = {}) => {
    if (!savedSearchName.trim()) {
      setError('Add a name for the saved search.')
      return
    }
    setSavingSearch(true)
    setError('')
    try {
      const response = await fetch('/api/discover/saved-searches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ name: savedSearchName.trim(), criteria: currentCriteria(), is_default: asDefault }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to save search')
      }
      setSavedSearchName('')
      await refreshDiscoveryMeta()
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingSearch(false)
    }
  }

  const applySavedSearch = async (searchId) => {
    try {
      const response = await fetch(`/api/discover/saved-searches/${searchId}`, {
        credentials: 'include',
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to load saved search')
      }
      setForm((prev) => ({ ...prev, ...formFromCriteria(payload.criteria || {}) }))
    } catch (err) {
      setError(err.message)
    }
  }

  const deleteSavedSearch = async (searchId) => {
    try {
      const response = await fetch(`/api/discover/saved-searches/${searchId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to delete saved search')
      }
      await refreshDiscoveryMeta()
    } catch (err) {
      setError(err.message)
    }
  }

  const saveCurrentAsDefaults = async () => {
    try {
      const response = await fetch('/api/discover/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ defaults: currentCriteria() }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to save defaults')
      }
      setDefaultPreferences(payload.defaults || {})
      await refreshDiscoveryMeta()
    } catch (err) {
      setError(err.message)
    }
  }

  const actOnSuggestion = async (suggestion, action) => {
    try {
      const response = await fetch(`/api/discover/suggestions/${encodeURIComponent(suggestion.suggestion_key)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ action, payload: suggestion.payload || {} }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || 'Failed to update suggestion')
      }
      if (action === 'accepted' && payload.preferences?.defaults) {
        setDefaultPreferences(payload.preferences.defaults)
        setForm((prev) => ({ ...prev, ...formFromCriteria(payload.preferences.defaults) }))
      }
      await refreshDiscoveryMeta()
    } catch (err) {
      setError(err.message)
    }
  }

  // Card actions run from onClick handlers, so failures have to land in the
  // error banner rather than an unhandled rejection in the console.
  const runRoleAction = async (roleId, action) => {
    if (pendingRoleId) return
    setPendingRoleId(roleId)
    setError('')
    try {
      await action()
    } catch (err) {
      setError(err.message)
    } finally {
      setPendingRoleId(null)
    }
  }

  const postRoleAction = async (roleId, path, body, failureMessage) => {
    const response = await fetch(`/api/discover/roles/${roleId}/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body || {}),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail || failureMessage)
    }
    return payload
  }

  const handleShortlist = (roleId) =>
    runRoleAction(roleId, async () => {
      await postRoleAction(roleId, 'shortlist', {}, 'Failed to shortlist role')
      await refreshInbox()
      await refreshDiscoveryMeta()
    })

  const handleDismiss = (roleId) =>
    runRoleAction(roleId, async () => {
      await postRoleAction(
        roleId,
        'dismiss',
        { reasons: dismissReasons, comment: dismissComment || null },
        'Failed to save feedback',
      )
      setDismissingRoleId(null)
      setDismissReasons([])
      setDismissComment('')
      await refreshInbox()
      await refreshDiscoveryMeta()
    })

  const handleRestore = (roleId) =>
    runRoleAction(roleId, async () => {
      await postRoleAction(roleId, 'restore', {}, 'Failed to restore role')
      await refreshInbox()
      await refreshDiscoveryMeta()
    })

  const handleOpenInTailor = (roleId) =>
    runRoleAction(roleId, async () => {
      const payload = await postRoleAction(roleId, 'open-in-tailor', {}, 'Failed to open role in Tailor')
      setDetailRoleId(null)
      if (typeof onOpenInTailor === 'function') {
        onOpenInTailor(payload.discover_seed)
      }
    })

  const resetInboxFilters = async () => {
    setInboxFilter('active')
    setInboxSearch('')
    setInboxSort('default')
    setEvaluatedOnly(false)
    await loadInbox('active', '', 'default', false)
  }

  const safely = (action) => async (...args) => {
    try {
      await action(...args)
    } catch (err) {
      setError(err.message)
    }
  }

  if (!isAuthenticated) {
    return (
      <DiscoverShell>
        <EmptyState icon="🔐" title="Sign in required">
          Discover requires an authenticated local user. Sign in with Google to search and save a role inbox.
        </EmptyState>
      </DiscoverShell>
    )
  }

  if (loadingStatus) {
    return (
      <DiscoverShell>
        <div className="discover-panel">
          <div className="discover-skeleton-line w-40" />
          <div className="discover-skeleton-line w-70" />
        </div>
      </DiscoverShell>
    )
  }

  if (!status.configured) {
    return (
      <DiscoverShell>
        <EmptyState icon="🧭" title="Discover is unavailable">
          {status.reason || 'Discover search is not configured on this instance.'}
        </EmptyState>
      </DiscoverShell>
    )
  }

  return (
    <div className="discover-page">
      <div className="discover-hero">
        <div className="discover-hero-copy">
          <h2>Discover Roles</h2>
          <p>Search broadly, review quickly, and only run strategy evaluation on roles you choose.</p>
        </div>
        <div className="discover-hero-badges">
          <span className="discover-status-badge">
            <span className="discover-status-dot" aria-hidden="true" />
            {status.provider && status.provider !== 'none' ? `${status.provider} connected` : 'Discovery ready'}
          </span>
        </div>
      </div>

      {error && (
        <div className="discover-alert" role="alert">
          <span>{error}</span>
          <button type="button" className="discover-alert-dismiss" onClick={() => setError('')} aria-label="Dismiss error">
            ×
          </button>
        </div>
      )}

      <section className="discover-panel">
        <div className="discover-panel-head">
          <div>
            <h3 className="discover-panel-title">Search criteria</h3>
            <p className="discover-panel-subtitle">Describe the roles you want. Everything except search intent is optional.</p>
          </div>
        </div>

        <div className="discover-form-grid">
          <label className="discover-field discover-field-wide">
            <span className="discover-label">Search intent</span>
            <input
              className="discover-input"
              type="text"
              value={form.search_intent}
              onChange={(event) => setForm((prev) => ({ ...prev, search_intent: event.target.value }))}
              placeholder="applied AI backend roles at product startups"
            />
          </label>

          <div className="discover-field discover-field-wide">
            <span className="discover-label">Role families</span>
            <div className="discover-chip-row">
              {ARCHETYPE_OPTIONS.map((option) => {
                const selected = form.role_families.includes(option.value)
                return (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={selected}
                    className={selected ? 'discover-chip active' : 'discover-chip'}
                    onClick={() => updateArrayField('role_families', option.value)}
                  >
                    {option.label}
                  </button>
                )
              })}
            </div>
          </div>

          <label className="discover-field">
            <span className="discover-label">Seniority</span>
            <select
              className="discover-select"
              value={form.seniority}
              onChange={(event) => setForm((prev) => ({ ...prev, seniority: event.target.value }))}
            >
              {SENIORITY_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </select>
          </label>

          <div className="discover-field">
            <span className="discover-label">Remote modes</span>
            <div className="discover-chip-row">
              {REMOTE_MODE_OPTIONS.map((option) => {
                const selected = form.remote_modes.includes(option)
                return (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={selected}
                    className={selected ? 'discover-chip active' : 'discover-chip'}
                    onClick={() => updateArrayField('remote_modes', option)}
                  >
                    {titleCase(option)}
                  </button>
                )
              })}
            </div>
          </div>

          <label className="discover-field">
            <span className="discover-label">Include locations</span>
            <input
              className="discover-input"
              type="text"
              value={form.include_locations}
              onChange={(event) => setForm((prev) => ({ ...prev, include_locations: event.target.value }))}
              placeholder="new york, boston"
            />
            <small className="discover-hint">Comma separated</small>
          </label>

          <label className="discover-field">
            <span className="discover-label">Exclude locations</span>
            <input
              className="discover-input"
              type="text"
              value={form.exclude_locations}
              onChange={(event) => setForm((prev) => ({ ...prev, exclude_locations: event.target.value }))}
              placeholder="san francisco"
            />
            <small className="discover-hint">Comma separated</small>
          </label>

          <label className="discover-field">
            <span className="discover-label">Must-have keywords</span>
            <input
              className="discover-input"
              type="text"
              value={form.must_have_keywords}
              onChange={(event) => setForm((prev) => ({ ...prev, must_have_keywords: event.target.value }))}
              placeholder="python, llm, backend"
            />
            <small className="discover-hint">Comma separated</small>
          </label>

          <label className="discover-field">
            <span className="discover-label">Avoid keywords</span>
            <input
              className="discover-input"
              type="text"
              value={form.avoid_keywords}
              onChange={(event) => setForm((prev) => ({ ...prev, avoid_keywords: event.target.value }))}
              placeholder="frontend, onsite"
            />
            <small className="discover-hint">Comma separated</small>
          </label>

          <div className="discover-field discover-field-wide">
            <span className="discover-label">Visa sponsorship</span>
            <label className="discover-switch">
              <input
                type="checkbox"
                checked={Boolean(form.prefer_visa_sponsorship)}
                onChange={(event) => setForm((prev) => ({ ...prev, prefer_visa_sponsorship: event.target.checked }))}
              />
              Prefer roles likely to support sponsorship
            </label>
          </div>
        </div>

        <div className="discover-panel-footer">
          <span className="discover-footer-hint">Results land in the inbox below and stay there until you dismiss them.</span>
          <button type="button" className="discover-secondary-button" onClick={clearForm} disabled={searching}>
            Clear
          </button>
          <button
            type="button"
            className="discover-secondary-button"
            onClick={() => handleSearch({ refresh: true })}
            disabled={searching}
            title="Re-run this search against fresh job feeds instead of the cached result"
          >
            Refresh
          </button>
          <button type="button" className="discover-primary-button" onClick={() => handleSearch()} disabled={searching}>
            {searching ? 'Searching…' : 'Search roles'}
          </button>
        </div>
      </section>

      {resultMeta && (
        <div className="discover-meta-bar">
          <strong>{pluralize(resultMeta.count, 'result', 'results')}</strong>
          <span className="discover-meta-sep" aria-hidden="true">
            •
          </span>
          <span>{resultSourceLabel(resultMeta.source)}</span>
          {resultMeta.warnings?.length > 0 && (
            <>
              <span className="discover-meta-sep" aria-hidden="true">
                •
              </span>
              <span className="discover-meta-warning">{resultMeta.warnings.join(' ')}</span>
            </>
          )}
        </div>
      )}

      <div className="discover-content-grid">
        <div className="discover-main-column">
          <div className="discover-toolbar">
            <div className="discover-filter-tabs" role="group" aria-label="Inbox filter">
              {INBOX_FILTERS.map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={inboxFilter === value}
                  className={inboxFilter === value ? 'discover-filter-tab active' : 'discover-filter-tab'}
                  onClick={async () => {
                    setInboxFilter(value)
                    await loadInbox(value, inboxSearch)
                  }}
                >
                  {titleCase(value)}
                </button>
              ))}
            </div>
            <div className="discover-toolbar-controls">
              <label className="discover-sort-control">
                <span>Sort</span>
                <select
                  value={inboxSort}
                  onChange={async (event) => {
                    const value = event.target.value
                    setInboxSort(value)
                    await loadInbox(inboxFilter, inboxSearch, value, evaluatedOnly)
                  }}
                >
                  <option value="default">Best match</option>
                  <option value="fit">Fit score</option>
                </select>
              </label>
              <label className="discover-checkbox-label">
                <input
                  type="checkbox"
                  checked={evaluatedOnly}
                  onChange={async (event) => {
                    const checked = event.target.checked
                    setEvaluatedOnly(checked)
                    await loadInbox(inboxFilter, inboxSearch, inboxSort, checked)
                  }}
                />
                Evaluated only
              </label>
            </div>
            <div className="discover-search-field">
              <span className="discover-search-icon" aria-hidden="true">
                🔍
              </span>
              <input
                className="discover-search-input"
                type="search"
                value={inboxSearch}
                aria-label="Search the role inbox"
                onChange={(event) => setInboxSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') loadInbox(inboxFilter, inboxSearch)
                }}
                placeholder="Search inbox…"
              />
              {inboxSearch && (
                <button
                  type="button"
                  className="discover-search-clear"
                  aria-label="Clear inbox search"
                  onClick={() => {
                    setInboxSearch('')
                    loadInbox(inboxFilter, '')
                  }}
                >
                  ×
                </button>
              )}
            </div>
          </div>

          {filterIsActive && !loadingInbox && (
            <div className="discover-filter-banner">
              <span>
                Showing <strong>{titleCase(inboxFilter)}</strong>
                {inboxSearch.trim() ? ` matching “${inboxSearch.trim()}”` : ''} — {pluralize(roles.length, 'role', 'roles')}.
              </span>
              <button type="button" className="discover-secondary-button discover-button-sm" onClick={resetInboxFilters}>
                Reset view
              </button>
            </div>
          )}

          {loadingInbox ? (
            <ResultsSkeleton />
          ) : roles.length === 0 ? (
            <EmptyState icon="📭" title="No roles in this view yet">
              Run a search above to populate the inbox, or switch filters to see roles you have already triaged.
            </EmptyState>
          ) : (
            <div className="discover-results-grid">
              {roles.map((role) => {
                const matched = role.matched_filters || []
                const blockers = role.possible_blockers || []
                const pills = [
                  ...matched.map((item) => ({ key: `match-${item}`, label: item, kind: 'match' })),
                  ...blockers.map((item) => ({ key: `block-${item}`, label: item, kind: 'blocker' })),
                ]
                const visiblePills = pills.slice(0, MAX_VISIBLE_PILLS)
                const hiddenPillCount = pills.length - visiblePills.length
                const isShortlisted = role.inbox_state === 'shortlisted'
                const isDismissed = role.inbox_state === 'dismissed'
                const isPending = pendingRoleId === role.id

                return (
                  <article key={role.id} className={isDismissed ? 'discover-role-card is-dismissed' : 'discover-role-card'}>
                    <div className="discover-role-header">
                      <div className="discover-role-heading">
                        <div className="discover-role-company">{role.company || 'Unknown company'}</div>
                        <button
                          type="button"
                          className="discover-role-title discover-role-title-button"
                          onClick={() => setDetailRoleId(role.id)}
                        >
                          {role.job_title}
                        </button>
                      </div>
                      <div className="discover-role-header-right">
                        {role.fit_score !== null && role.fit_score !== undefined && (
                          <span className={`discover-fit-badge tone-${fitTone(role.fit_score)}`}>
                            Fit {role.fit_score}/10
                          </span>
                        )}
                        {isShortlisted && <span className="discover-state-badge shortlisted">Shortlisted</span>}
                        {isDismissed && <span className="discover-state-badge dismissed">Dismissed</span>}
                      </div>
                    </div>

                    <div className="discover-role-meta">
                      {/* Index-keyed: a role can legitimately repeat a value here
                          (location "Remote" alongside remote_mode "Remote"). */}
                      {metaItems(role).map((item, index) => (
                        <span key={`${role.id}-meta-${index}-${item}`} className="discover-meta-item">
                          {item}
                        </span>
                      ))}
                      {Number(role.extraction_confidence || 0) < 0.6 && (
                        <span className="discover-confidence-badge">Low confidence</span>
                      )}
                    </div>

                    <p className="discover-role-summary">{role.short_tldr || 'No summary available yet.'}</p>

                    {pills.length > 0 && (
                      <div className="discover-pill-groups">
                        {visiblePills.map((pill) => (
                          <span key={`${role.id}-${pill.key}`} className={`discover-pill ${pill.kind}`}>
                            {pill.label}
                          </span>
                        ))}
                        {hiddenPillCount > 0 && <span className="discover-pill more">+{hiddenPillCount} more</span>}
                      </div>
                    )}

                    <div className="discover-card-actions">
                      <div className="discover-action-group">
                        <button
                          type="button"
                          className="discover-primary-button discover-button-sm"
                          disabled={isPending}
                          onClick={() => setDetailRoleId(role.id)}
                        >
                          View job
                        </button>
                        <button
                          type="button"
                          className="discover-secondary-button discover-button-sm"
                          disabled={isPending}
                          onClick={() => handleOpenInTailor(role.id)}
                        >
                          Open in Tailor
                        </button>
                        <a
                          href={role.apply_url || role.canonical_url}
                          target="_blank"
                          rel="noreferrer"
                          className="discover-link-button discover-button-sm"
                        >
                          Posting ↗
                        </a>
                      </div>
                      <div className="discover-action-group">
                        {isShortlisted ? (
                          <button
                            type="button"
                            className="discover-secondary-button discover-button-sm is-active"
                            disabled={isPending}
                            onClick={() => handleRestore(role.id)}
                          >
                            ★ Shortlisted
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="discover-secondary-button discover-button-sm"
                            disabled={isPending}
                            onClick={() => handleShortlist(role.id)}
                          >
                            ☆ Shortlist
                          </button>
                        )}
                        {isDismissed ? (
                          <button
                            type="button"
                            className="discover-secondary-button discover-button-sm"
                            disabled={isPending}
                            onClick={() => handleRestore(role.id)}
                          >
                            Restore
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="discover-secondary-button discover-button-sm"
                            disabled={isPending}
                            aria-expanded={dismissingRoleId === role.id}
                            onClick={() => {
                              setDismissingRoleId(dismissingRoleId === role.id ? null : role.id)
                              setDismissReasons([])
                              setDismissComment('')
                            }}
                          >
                            Not relevant
                          </button>
                        )}
                      </div>
                    </div>

                    {dismissingRoleId === role.id && (
                      <div className="discover-dismiss-form">
                        <span className="discover-label">Why is this not a fit?</span>
                        <div className="discover-chip-row">
                          {DISMISS_REASONS.map((reason) => {
                            const selected = dismissReasons.includes(reason)
                            return (
                              <button
                                key={reason}
                                type="button"
                                aria-pressed={selected}
                                className={selected ? 'discover-chip active' : 'discover-chip'}
                                onClick={() => {
                                  setDismissReasons((prev) =>
                                    prev.includes(reason) ? prev.filter((item) => item !== reason) : [...prev, reason],
                                  )
                                }}
                              >
                                {reason}
                              </button>
                            )
                          })}
                        </div>
                        <textarea
                          className="discover-textarea"
                          value={dismissComment}
                          onChange={(event) => setDismissComment(event.target.value)}
                          placeholder="Optional comment"
                        />
                        <div className="discover-dismiss-actions">
                          <button
                            type="button"
                            className="discover-primary-button discover-button-sm"
                            disabled={isPending}
                            onClick={() => handleDismiss(role.id)}
                          >
                            Save feedback
                          </button>
                          <button
                            type="button"
                            className="discover-secondary-button discover-button-sm"
                            onClick={() => setDismissingRoleId(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </div>

        <aside className="discover-rail">
          {suggestions.length > 0 && (
            <section className="discover-panel">
              <div className="discover-panel-head">
                <div>
                  <h3 className="discover-panel-title">Preference suggestions</h3>
                  <p className="discover-panel-subtitle">Patterns picked up from what you shortlist and dismiss.</p>
                </div>
              </div>
              <div className="discover-suggestions-list">
                {suggestions.map((suggestion) => (
                  <div key={suggestion.suggestion_key} className="discover-suggestion-card">
                    <div>
                      <strong>{suggestion.title}</strong>
                      <small>Observed {pluralize(suggestion.evidence_count, 'time', 'times')} in the last 90 days.</small>
                    </div>
                    <div className="discover-card-row">
                      <button
                        type="button"
                        className="discover-primary-button discover-button-sm"
                        onClick={() => actOnSuggestion(suggestion, 'accepted')}
                      >
                        Apply
                      </button>
                      <button
                        type="button"
                        className="discover-secondary-button discover-button-sm"
                        onClick={() => actOnSuggestion(suggestion, 'dismissed')}
                      >
                        Dismiss
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="discover-panel">
            <div className="discover-panel-head">
              <div>
                <h3 className="discover-panel-title">Saved searches</h3>
                <p className="discover-panel-subtitle">Store the criteria above and reuse them later.</p>
              </div>
            </div>

            <div className="discover-saved-search-create">
              <input
                className="discover-input"
                type="text"
                value={savedSearchName}
                aria-label="Saved search name"
                onChange={(event) => setSavedSearchName(event.target.value)}
                placeholder="Applied AI remote"
              />
              <div className="discover-card-row">
                <button
                  type="button"
                  className="discover-secondary-button discover-button-sm"
                  onClick={() => handleSaveSearch()}
                  disabled={savingSearch}
                >
                  {savingSearch ? 'Saving…' : 'Save search'}
                </button>
                <button type="button" className="discover-secondary-button discover-button-sm" onClick={saveCurrentAsDefaults}>
                  Save as defaults
                </button>
              </div>
            </div>

            <p className="discover-defaults-copy">
              Defaults: {Object.keys(defaultPreferences || {}).length > 0 ? 'saved' : 'not set yet'}
            </p>

            {savedSearches.length === 0 ? (
              <p className="discover-muted-copy">No saved searches yet.</p>
            ) : (
              <div className="discover-saved-search-list">
                {savedSearches.map((saved) => (
                  <div key={saved.id} className="discover-saved-search-card">
                    <div className="discover-saved-search-name">
                      <strong>{saved.name}</strong>
                      {saved.is_default ? <span className="discover-default-badge">Default</span> : null}
                    </div>
                    <div className="discover-card-row">
                      <button
                        type="button"
                        className="discover-secondary-button discover-button-sm"
                        onClick={() => applySavedSearch(saved.id)}
                      >
                        Apply
                      </button>
                      <button
                        type="button"
                        className="discover-secondary-button discover-button-sm"
                        onClick={() => deleteSavedSearch(saved.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="discover-panel">
            <div className="discover-panel-head">
              <div>
                <h3 className="discover-panel-title">Discovery analytics</h3>
                <p className="discover-panel-subtitle">How discovered roles move through your funnel.</p>
              </div>
            </div>

            {analytics ? (
              <div className="discover-analytics-grid">
                <div className="discover-analytics-stat">
                  <span>{analytics.funnel?.discovered_roles || 0}</span>
                  <small>Discovered</small>
                </div>
                <div className="discover-analytics-stat">
                  <span>{analytics.funnel?.shortlisted_roles || 0}</span>
                  <small>Shortlisted</small>
                </div>
                <div className="discover-analytics-stat">
                  <span>{analytics.funnel?.opened_in_tailor_roles || 0}</span>
                  <small>In Tailor</small>
                </div>
                <div className="discover-analytics-stat">
                  <span>{analytics.funnel?.strategy_linked_roles || 0}</span>
                  <small>Strategies</small>
                </div>
                <div className="discover-analytics-stat">
                  <span>{analytics.funnel?.application_linked_roles || 0}</span>
                  <small>Applications</small>
                </div>
                <div className="discover-analytics-stat">
                  <span>{analytics.restore_rate_percent || 0}%</span>
                  <small>Restore rate</small>
                </div>
              </div>
            ) : (
              <p className="discover-muted-copy">No analytics yet.</p>
            )}

            {analytics?.reason_counts?.length > 0 && (
              <div className="discover-analytics-list">
                <span className="discover-label">Top dismiss reasons</span>
                {analytics.reason_counts.slice(0, 5).map((item) => (
                  <div key={item.reason} className="discover-analytics-row">
                    <span>{item.reason}</span>
                    <span>{item.count}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>

      {detailRoleId !== null && (
        <RoleDetailModal
          roleId={detailRoleId}
          archetypeLabel={archetypeLabel}
          onClose={() => setDetailRoleId(null)}
          onShortlist={handleShortlist}
          onDismiss={handleDismiss}
          onRestore={handleRestore}
          onOpenInTailor={handleOpenInTailor}
          onRoleChanged={async () => {
            await refreshInbox()
            await refreshDiscoveryMeta()
          }}
        />
      )}
    </div>
  )
}
