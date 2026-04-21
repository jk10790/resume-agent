import React, { useEffect, useMemo, useState } from 'react'
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

function parseCsv(text) {
  return String(text || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function archetypeLabel(value) {
  return ARCHETYPE_OPTIONS.find((option) => option.value === value)?.label || value || 'Unknown'
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
  }
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
  const [dismissingRoleId, setDismissingRoleId] = useState(null)
  const [dismissReasons, setDismissReasons] = useState([])
  const [dismissComment, setDismissComment] = useState('')
  const [form, setForm] = useState({
    search_intent: '',
    role_families: [],
    seniority: 'any',
    remote_modes: [],
    include_locations: '',
    exclude_locations: '',
    must_have_keywords: '',
    avoid_keywords: '',
  })

  const filterIsActive = useMemo(
    () => inboxFilter !== 'active' || inboxSearch.trim().length > 0,
    [inboxFilter, inboxSearch]
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
        if (!cancelled) setStatus({ enabled: false, configured: false, provider: 'none', reason: 'Failed to load discovery status.' })
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
          setForm((prev) => ({
            ...prev,
            ...formFromCriteria(prefsPayload.defaults),
          }))
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

  const loadInbox = async (state = inboxFilter, search = inboxSearch) => {
    setLoadingInbox(true)
    try {
      const params = new URLSearchParams({
        inbox_state: state,
        limit: '50',
      })
      if (search.trim()) params.set('search', search.trim())
      const response = await fetch(`/api/discover/roles?${params.toString()}`, { credentials: 'include' })
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}))
        throw new Error(payload.detail || 'Failed to load discover inbox')
      }
      const payload = await response.json()
      setRoles(payload.roles || [])
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
    setForm({
      search_intent: '',
      role_families: [],
      seniority: 'any',
      remote_modes: [],
      include_locations: '',
      exclude_locations: '',
      must_have_keywords: '',
      avoid_keywords: '',
    })
    setError('')
  }

  const handleSearch = async () => {
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
          page_size: 20,
          refresh: false,
        }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || 'Discover search failed')
      }
      setRoles(payload.roles || [])
      setResultMeta({
        count: (payload.roles || []).length,
        source: payload.result_source,
        warnings: payload.warnings || [],
      })
      setInboxFilter('active')
      setInboxSearch('')
    } catch (err) {
      setError(err.message)
    } finally {
      setSearching(false)
    }
  }

  const refreshInbox = async (state = inboxFilter, search = inboxSearch) => {
    await loadInbox(state, search)
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
        body: JSON.stringify({
          name: savedSearchName.trim(),
          criteria: currentCriteria(),
          is_default: asDefault,
        }),
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

  const handleShortlist = async (roleId) => {
    const response = await fetch(`/api/discover/roles/${roleId}/shortlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({}),
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || 'Failed to shortlist role')
    }
    await refreshInbox()
    await refreshDiscoveryMeta()
  }

  const handleDismiss = async (roleId) => {
    const response = await fetch(`/api/discover/roles/${roleId}/dismiss`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        reasons: dismissReasons,
        comment: dismissComment || null,
      }),
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || 'Failed to save feedback')
    }
    setDismissingRoleId(null)
    setDismissReasons([])
    setDismissComment('')
    await refreshInbox()
    await refreshDiscoveryMeta()
  }

  const handleRestore = async (roleId) => {
    const response = await fetch(`/api/discover/roles/${roleId}/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({}),
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || 'Failed to restore role')
    }
    await refreshInbox()
    await refreshDiscoveryMeta()
  }

  const handleOpenInTailor = async (roleId) => {
    const response = await fetch(`/api/discover/roles/${roleId}/open-in-tailor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({}),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail || 'Failed to open role in Tailor')
    }
    if (typeof onOpenInTailor === 'function') {
      onOpenInTailor(payload.discover_seed)
    }
  }

  if (!isAuthenticated) {
    return (
      <div className="discover-page">
        <div className="discover-hero">
          <div>
            <h2>Discover Roles</h2>
            <p>Search broadly, review quickly, and only run strategy evaluation on roles you choose.</p>
          </div>
        </div>
        <div className="discover-empty-card">
          <strong>Sign in required</strong>
          <p>Discover requires an authenticated local user. Sign in with Google to search and save a role inbox.</p>
        </div>
      </div>
    )
  }

  if (loadingStatus) {
    return <div className="discover-loading">Loading discovery status...</div>
  }

  if (!status.configured) {
    return (
      <div className="discover-page">
        <div className="discover-hero">
          <div>
            <h2>Discover Roles</h2>
            <p>Search broadly, review quickly, and only run strategy evaluation on roles you choose.</p>
          </div>
        </div>
        <div className="discover-empty-card">
          <strong>Discover is unavailable</strong>
          <p>{status.reason || 'Discover search is not configured on this instance.'}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="discover-page">
      <div className="discover-hero">
        <div>
          <h2>Discover Roles</h2>
          <p>Search broadly, review quickly, and only run strategy evaluation on roles you choose.</p>
        </div>
      </div>

      {suggestions.length > 0 && (
        <div className="discover-suggestions-panel">
          <strong>Preference suggestions</strong>
          <div className="discover-suggestions-list">
            {suggestions.map((suggestion) => (
              <div key={suggestion.suggestion_key} className="discover-suggestion-card">
                <span>{suggestion.title}</span>
                <small>Observed {suggestion.evidence_count} times in the last 90 days.</small>
                <div className="discover-actions">
                  <button type="button" className="discover-primary-button" onClick={() => actOnSuggestion(suggestion, 'accepted')}>
                    Apply
                  </button>
                  <button type="button" className="discover-secondary-button" onClick={() => actOnSuggestion(suggestion, 'dismissed')}>
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="discover-dashboard-grid">
        <div className="discover-form-card">
          <strong>Saved searches</strong>
          <div className="discover-saved-search-create">
            <input
              type="text"
              value={savedSearchName}
              onChange={(event) => setSavedSearchName(event.target.value)}
              placeholder="Applied AI remote"
            />
            <button type="button" className="discover-secondary-button" onClick={() => handleSaveSearch()} disabled={savingSearch}>
              {savingSearch ? 'Saving...' : 'Save search'}
            </button>
            <button type="button" className="discover-secondary-button" onClick={saveCurrentAsDefaults}>
              Save as defaults
            </button>
          </div>
          <div className="discover-defaults-copy">
            Defaults: {Object.keys(defaultPreferences || {}).length > 0 ? 'Saved' : 'Not set yet'}
          </div>
          {savedSearches.length === 0 ? (
            <p className="discover-muted-copy">No saved searches yet.</p>
          ) : (
            <div className="discover-saved-search-list">
              {savedSearches.map((saved) => (
                <div key={saved.id} className="discover-saved-search-card">
                  <div>
                    <strong>{saved.name}</strong>
                    {saved.is_default ? <span className="discover-default-badge">Default</span> : null}
                  </div>
                  <div className="discover-actions">
                    <button type="button" className="discover-secondary-button" onClick={() => applySavedSearch(saved.id)}>
                      Apply
                    </button>
                    <button type="button" className="discover-secondary-button" onClick={() => deleteSavedSearch(saved.id)}>
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="discover-form-card">
          <strong>Discovery analytics</strong>
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
                <small>Opened in Tailor</small>
              </div>
              <div className="discover-analytics-stat">
                <span>{analytics.funnel?.strategy_linked_roles || 0}</span>
                <small>Strategy linked</small>
              </div>
              <div className="discover-analytics-stat">
                <span>{analytics.funnel?.application_linked_roles || 0}</span>
                <small>Applications linked</small>
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
              <strong>Top dismiss reasons</strong>
              {analytics.reason_counts.slice(0, 5).map((item) => (
                <div key={item.reason} className="discover-analytics-row">
                  <span>{item.reason}</span>
                  <span>{item.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="discover-form-card">
        <div className="discover-form-grid">
          <label className="discover-field discover-field-wide">
            <span>Search intent</span>
            <input
              type="text"
              value={form.search_intent}
              onChange={(event) => setForm((prev) => ({ ...prev, search_intent: event.target.value }))}
              placeholder="applied AI backend roles at product startups"
            />
          </label>

          <div className="discover-field discover-field-wide">
            <span>Role families</span>
            <div className="discover-chip-row">
              {ARCHETYPE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={form.role_families.includes(option.value) ? 'discover-chip active' : 'discover-chip'}
                  onClick={() => updateArrayField('role_families', option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <label className="discover-field">
            <span>Seniority</span>
            <select value={form.seniority} onChange={(event) => setForm((prev) => ({ ...prev, seniority: event.target.value }))}>
              {SENIORITY_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <div className="discover-field">
            <span>Remote modes</span>
            <div className="discover-chip-row">
              {REMOTE_MODE_OPTIONS.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={form.remote_modes.includes(option) ? 'discover-chip active' : 'discover-chip'}
                  onClick={() => updateArrayField('remote_modes', option)}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          <label className="discover-field">
            <span>Include locations</span>
            <input
              type="text"
              value={form.include_locations}
              onChange={(event) => setForm((prev) => ({ ...prev, include_locations: event.target.value }))}
              placeholder="new york, boston"
            />
          </label>

          <label className="discover-field">
            <span>Exclude locations</span>
            <input
              type="text"
              value={form.exclude_locations}
              onChange={(event) => setForm((prev) => ({ ...prev, exclude_locations: event.target.value }))}
              placeholder="san francisco"
            />
          </label>

          <label className="discover-field">
            <span>Must-have keywords</span>
            <input
              type="text"
              value={form.must_have_keywords}
              onChange={(event) => setForm((prev) => ({ ...prev, must_have_keywords: event.target.value }))}
              placeholder="python, llm, backend"
            />
          </label>

          <label className="discover-field">
            <span>Avoid keywords</span>
            <input
              type="text"
              value={form.avoid_keywords}
              onChange={(event) => setForm((prev) => ({ ...prev, avoid_keywords: event.target.value }))}
              placeholder="frontend, onsite"
            />
          </label>
        </div>

        {error && <div className="discover-inline-error">{error}</div>}

        <div className="discover-actions">
          <button type="button" className="discover-primary-button" onClick={handleSearch} disabled={searching}>
            {searching ? 'Searching...' : 'Search'}
          </button>
          <button type="button" className="discover-secondary-button" onClick={clearForm} disabled={searching}>
            Clear
          </button>
        </div>
      </div>

      {resultMeta && (
        <div className="discover-meta-bar">
          <span>{resultMeta.count} results</span>
          <span>{resultMeta.source === 'cache' ? 'Cached' : resultMeta.source === 'stale_cache_fallback' ? 'Cached fallback' : 'Fresh search'}</span>
          {resultMeta.warnings?.length > 0 && <span>{resultMeta.warnings.join(' ')}</span>}
        </div>
      )}

      <div className="discover-toolbar">
        <div className="discover-filter-tabs">
          {['active', 'shortlisted', 'dismissed', 'all'].map((value) => (
            <button
              key={value}
              type="button"
              className={inboxFilter === value ? 'discover-filter-tab active' : 'discover-filter-tab'}
              onClick={async () => {
                setInboxFilter(value)
                await loadInbox(value, inboxSearch)
              }}
            >
              {value[0].toUpperCase() + value.slice(1)}
            </button>
          ))}
        </div>
        <input
          className="discover-search-input"
          type="text"
          value={inboxSearch}
          onChange={(event) => setInboxSearch(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') loadInbox(inboxFilter, inboxSearch)
          }}
          placeholder="Search inbox"
        />
      </div>

      {filterIsActive && (
        <div className="discover-filter-banner">
          Showing a filtered inbox view.
        </div>
      )}

      {loadingInbox ? (
        <div className="discover-loading">Loading role inbox...</div>
      ) : roles.length === 0 ? (
        <div className="discover-empty-card">
          <strong>No roles in this view yet.</strong>
          <p>Run a search to populate the inbox, or adjust the filters above.</p>
        </div>
      ) : (
        <div className="discover-results-grid">
          {roles.map((role) => (
            <div key={role.id} className="discover-role-card">
              <div className="discover-role-header">
                <div>
                  <div className="discover-role-company">{role.company}</div>
                  <div className="discover-role-title">{role.job_title}</div>
                </div>
                <div className="discover-role-domain">{role.source_domain}</div>
              </div>

              <div className="discover-role-meta">
                <span>{role.location || 'Location unavailable'}</span>
                <span>{role.remote_mode || 'unknown'}</span>
                <span>{role.posted_label || 'Date unavailable'}</span>
                <span>{archetypeLabel(role.archetype)}</span>
                {Number(role.extraction_confidence || 0) < 0.6 && (
                  <span className="discover-confidence-badge">Low confidence</span>
                )}
              </div>

              <p className="discover-role-summary">{role.short_tldr || 'No summary available yet.'}</p>

              <div className="discover-pill-groups">
                {(role.matched_filters || []).map((item) => (
                  <span key={`${role.id}-match-${item}`} className="discover-pill match">
                    {item}
                  </span>
                ))}
                {(role.possible_blockers || []).map((item) => (
                  <span key={`${role.id}-block-${item}`} className="discover-pill blocker">
                    {item}
                  </span>
                ))}
              </div>

              <div className="discover-card-actions">
                <a href={role.apply_url || role.canonical_url} target="_blank" rel="noreferrer" className="discover-link-button">
                  Open posting
                </a>
                <button type="button" className="discover-secondary-button" onClick={() => handleOpenInTailor(role.id)}>
                  Open in Tailor
                </button>
                {role.inbox_state !== 'shortlisted' ? (
                  <button type="button" className="discover-secondary-button" onClick={() => handleShortlist(role.id)}>
                    Shortlist
                  </button>
                ) : (
                  <button type="button" className="discover-secondary-button" onClick={() => handleRestore(role.id)}>
                    Restore
                  </button>
                )}
                {role.inbox_state !== 'dismissed' ? (
                  <button
                    type="button"
                    className="discover-secondary-button"
                    onClick={() => {
                      setDismissingRoleId(role.id)
                      setDismissReasons([])
                      setDismissComment('')
                    }}
                  >
                    Not relevant
                  </button>
                ) : (
                  <button type="button" className="discover-secondary-button" onClick={() => handleRestore(role.id)}>
                    Restore
                  </button>
                )}
              </div>

              {dismissingRoleId === role.id && (
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
                    <button type="button" className="discover-primary-button" onClick={() => handleDismiss(role.id)}>
                      Save feedback
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
