import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'

afterEach(() => vi.unstubAllGlobals())
describe('API errors', () => {
  it('preserves the backend failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:'Insufficient disk space: need 10 GB'}), {status: 502})))
    await expect(api('/api/models/download')).rejects.toThrow('Insufficient disk space: need 10 GB')
  })
  it('formats validation details instead of object Object', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:[{loc:['body','repoId'],msg:'Field required'}]}), {status:422})))
    await expect(api('/api/models/download')).rejects.toThrow('Field required')
  })
})
