import { type ChangeEvent, useEffect, useState } from 'react'
import './App.css'

type Job = {
  job_key: string
  title: string
  company: string
  location: string
  url: string
  source: string
  match_score: number
  match_reason: string
  experience_level: string
  required_skills: string[]
  status: string
  email_subject?: string
  email_draft?: string
  posted_at?: string
}

type Preferences = {
  location: string
  include_remote: boolean
  remote_only: boolean
  skills: string[]
  status: string
  source: string
}

const defaultPreferences: Preferences = {
  location: 'Hyderabad/Telangana',
  include_remote: true,
  remote_only: false,
  skills: ['Python', 'Django', 'SQL'],
  status: 'all',
  source: 'all',
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || 'Request failed')
  }

  return response.json() as Promise<T>
}

function App() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [diagnostics, setDiagnostics] = useState<Record<string, unknown> | null>(null)
  const [preferences, setPreferences] = useState<Preferences>(defaultPreferences)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [uploadStatus, setUploadStatus] = useState('')

  const loadJobs = async (nextSearch = search, nextStatus = statusFilter) => {
    setLoading(true)
    setError('')
    try {
      const response = await api<{ items: Job[]; total: number }>(
        `/api/jobs?search=${encodeURIComponent(nextSearch)}&status=${encodeURIComponent(nextStatus)}&limit=50`,
      )
      setJobs(response.items)
      if (response.items.length > 0 && !selectedJob) {
        setSelectedJob(response.items[0])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load jobs')
    } finally {
      setLoading(false)
    }
  }

  const loadDiagnostics = async () => {
    try {
      const report = await api<Record<string, unknown>>('/api/diagnostics')
      setDiagnostics(report)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load diagnostics')
    }
  }

  const loadPreferences = async () => {
    try {
      const response = await api<Preferences>('/api/preferences')
      setPreferences(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load preferences')
    }
  }

  useEffect(() => {
    void loadJobs()
    void loadDiagnostics()
    void loadPreferences()
  }, [])

  const savePreferences = async () => {
    try {
      const response = await api<Preferences>('/api/preferences', {
        method: 'POST',
        body: JSON.stringify(preferences),
      })
      setPreferences(response)
      setUploadStatus('Preferences saved.')
    } catch (err) {
      setUploadStatus(err instanceof Error ? err.message : 'Could not save preferences.')
    }
  }

  const runSearch = async () => {
    await loadJobs(search, statusFilter)
  }

  const runSourceSearch = async () => {
    setError('')
    try {
      const response = await api<{ matches: number; items: Job[] }>('/api/jobs/run', { method: 'POST' })
      setUploadStatus(`${response.matches} matches fetched and saved.`)
      await loadJobs(search, statusFilter)
      await loadDiagnostics()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Live search failed')
    }
  }

  const updateStatus = async (jobKey: string, status: string) => {
    try {
      await api(`/api/jobs/${encodeURIComponent(jobKey)}`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      })
      setJobs((current) =>
        current.map((job) =>
          job.job_key === jobKey ? { ...job, status } : job,
        ),
      )
      setSelectedJob((current) =>
        current && current.job_key === jobKey ? { ...current, status } : current,
      )
      setUploadStatus(`Job marked as ${status}.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update tracker status')
    }
  }

  const handleCsvUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }
    const formData = new FormData()
    formData.append('file', file)
    formData.append('save', 'false')

    try {
      const response = await fetch('/api/jobs/import-csv', {
        method: 'POST',
        body: formData,
      })
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const payload = await response.json()
      setUploadStatus(`CSV preview loaded: ${payload.report.accepted.length} accepted, ${payload.report.rejected.length} rejected.`)
      setDiagnostics(payload.report)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'CSV import failed.')
    }
  }

  const activeJobs = jobs.filter((job) => job.status !== 'rejected')
  const savedCount = jobs.filter((job) => job.status && job.status !== 'new').length
  const hyderabadCount = jobs.filter((job) => /hyderabad|telangana/i.test(job.location)).length

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Fresher Job Hunter</p>
          <h1>Opportunity dashboard</h1>
        </div>
        <div className="topbar-actions">
          <button type="button" onClick={runSourceSearch}>Refresh live jobs</button>
          <a className="button-link" href="/api/jobs/export/csv" target="_blank" rel="noreferrer">
            Export CSV
          </a>
        </div>
      </header>

      {error ? <div className="banner banner-error">{error}</div> : null}
      {uploadStatus ? <div className="banner banner-success">{uploadStatus}</div> : null}

      <section className="summary-grid">
        <article className="stat-card">
          <span>Total matches</span>
          <strong>{jobs.length}</strong>
        </article>
        <article className="stat-card">
          <span>Saved jobs</span>
          <strong>{savedCount}</strong>
        </article>
        <article className="stat-card">
          <span>Hyderabad/Telangana</span>
          <strong>{hyderabadCount}</strong>
        </article>
        <article className="stat-card">
          <span>Tracked</span>
          <strong>{activeJobs.length}</strong>
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2>Job explorer</h2>
          <div className="toolbar">
            <input
              aria-label="Search jobs"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by title, company, or location"
            />
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="all">All statuses</option>
              <option value="new">New</option>
              <option value="saved">Saved</option>
              <option value="contacted">Contacted</option>
              <option value="interview">Interview</option>
              <option value="rejected">Rejected</option>
            </select>
            <button type="button" onClick={runSearch} disabled={loading}>
              {loading ? 'Loading…' : 'Apply filters'}
            </button>
          </div>
        </div>

        <div className="job-layout">
          <div className="job-list">
            {jobs.length === 0 ? <p className="empty-state">No jobs match the current filters.</p> : null}
            {jobs.map((job) => (
              <button
                key={job.job_key}
                type="button"
                className={`job-card ${selectedJob?.job_key === job.job_key ? 'selected' : ''}`}
                onClick={() => setSelectedJob(job)}
              >
                <div className="job-card-topline">
                  <span className="score">{job.match_score ?? 0}</span>
                  <span className="status-pill">{job.status ?? 'new'}</span>
                </div>
                <h3>{job.title}</h3>
                <p>{job.company}</p>
                <small>{job.location}</small>
              </button>
            ))}
          </div>

          <article className="job-detail">
            {selectedJob ? (
              <>
                <div className="detail-header-row">
                  <div>
                    <p className="eyebrow">{selectedJob.source}</p>
                    <h2>{selectedJob.title}</h2>
                  </div>
                  <span className="score-badge">{selectedJob.match_score ?? 0}/100</span>
                </div>

                <div className="meta-grid">
                  <div><label>Company</label><p>{selectedJob.company}</p></div>
                  <div><label>Location</label><p>{selectedJob.location}</p></div>
                  <div><label>Experience</label><p>{selectedJob.experience_level || 'Not specified'}</p></div>
                  <div><label>Posted</label><p>{selectedJob.posted_at || 'Not specified'}</p></div>
                </div>

                <p className="description">{selectedJob.match_reason}</p>
                <ul className="tags">
                  {(selectedJob.required_skills || []).map((skill) => (
                    <li key={skill}>{skill}</li>
                  ))}
                </ul>

                <div className="actions-row">
                  <a href={selectedJob.url} target="_blank" rel="noreferrer">Open listing</a>
                  <button type="button" onClick={() => updateStatus(selectedJob.job_key, 'saved')}>Save</button>
                  <button type="button" onClick={() => updateStatus(selectedJob.job_key, 'contacted')}>Contacted</button>
                  <button type="button" onClick={() => updateStatus(selectedJob.job_key, 'interview')}>Interview</button>
                </div>

                {selectedJob.email_subject ? (
                  <div className="draft-box">
                    <h3>{selectedJob.email_subject}</h3>
                    <pre>{selectedJob.email_draft}</pre>
                  </div>
                ) : null}
              </>
            ) : (
              <p className="empty-state">Select a job to review details.</p>
            )}
          </article>
        </div>
      </section>

      <section className="panel two-column">
        <div>
          <h2>Application tracker</h2>
          <div className="tracker-list">
            {jobs.map((job) => (
              <div key={job.job_key} className="tracker-item">
                <div>
                  <strong>{job.title}</strong>
                  <p>{job.company}</p>
                </div>
                <select
                  value={job.status ?? 'new'}
                  onChange={(event) => updateStatus(job.job_key, event.target.value)}
                >
                  <option value="new">New</option>
                  <option value="saved">Saved</option>
                  <option value="contacted">Contacted</option>
                  <option value="interview">Interview</option>
                  <option value="rejected">Rejected</option>
                </select>
              </div>
            ))}
          </div>
        </div>

        <div>
          <h2>Diagnostics</h2>
          {diagnostics ? (
            <div className="diagnostics-block">
              <p><strong>Fetched:</strong> {String((diagnostics as any).fetched ?? 'n/a')}</p>
              <p><strong>Normalized:</strong> {String((diagnostics as any).normalized ?? 'n/a')}</p>
              <p><strong>Final matches:</strong> {String((diagnostics as any).pipeline?.final_matches ?? 'n/a')}</p>
              <p><strong>Location eligible:</strong> {String((diagnostics as any).pipeline?.after_location ?? 'n/a')}</p>
              <p><strong>Experience eligible:</strong> {String((diagnostics as any).pipeline?.after_experience ?? 'n/a')}</p>
            </div>
          ) : (
            <p className="empty-state">Diagnostics are loading.</p>
          )}
        </div>
      </section>

      <section className="panel two-column">
        <div>
          <h2>CSV import</h2>
          <label className="upload-box">
            <input type="file" accept=".csv" onChange={handleCsvUpload} />
            <span>Choose CSV file</span>
          </label>
        </div>

        <div>
          <h2>Preferences</h2>
          <div className="preferences-grid">
            <label>
              Preferred location
              <input
                value={preferences.location}
                onChange={(event) => setPreferences({ ...preferences, location: event.target.value })}
              />
            </label>
            <label>
              Default status
              <select value={preferences.status} onChange={(event) => setPreferences({ ...preferences, status: event.target.value })}>
                <option value="all">All</option>
                <option value="new">New</option>
                <option value="saved">Saved</option>
                <option value="contacted">Contacted</option>
                <option value="interview">Interview</option>
                <option value="rejected">Rejected</option>
              </select>
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={preferences.include_remote}
                onChange={(event) => setPreferences({ ...preferences, include_remote: event.target.checked })}
              />
              Include India-eligible remote roles
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={preferences.remote_only}
                onChange={(event) => setPreferences({ ...preferences, remote_only: event.target.checked })}
              />
              Remote only
            </label>
          </div>
          <button type="button" onClick={savePreferences}>Save preferences</button>
        </div>
      </section>
    </div>
  )
}

export default App
