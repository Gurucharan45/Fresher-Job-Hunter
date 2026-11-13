import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders the dashboard shell and job data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const request = String(input)
        if (request === '/api/preferences') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              location: 'Hyderabad/Telangana',
              include_remote: true,
              remote_only: false,
              skills: ['Python'],
              status: 'all',
              source: 'all',
            }),
          })
        }

        if (request === '/api/diagnostics') {
          return Promise.resolve({
            ok: true,
            json: async () => ({ fetched: 2, normalized: 2, pipeline: { final_matches: 1, after_location: 2 } }),
          })
        }

        if (request.startsWith('/api/jobs')) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [{
                job_key: 'job-1',
                title: 'Junior Python Developer',
                company: 'Example Co',
                location: 'Hyderabad, Telangana',
                source: 'Example',
                url: 'https://example.com/jobs/1',
                match_score: 90,
                match_reason: 'Mentions Python and Django',
                experience_level: '0-1 years',
                required_skills: ['Python', 'Django'],
                status: 'new',
              }],
              total: 1,
            }),
          })
        }

        return Promise.resolve({
          ok: true,
          json: async () => ({ ok: true }),
        })
      }),
    )

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Opportunity dashboard')).toBeInTheDocument()
    })
    expect(screen.getAllByText('Junior Python Developer').length).toBeGreaterThan(0)
  })
})
