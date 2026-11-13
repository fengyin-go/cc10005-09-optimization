import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/projection'

type Unknown = unknown

type Writer = {
  id: string
  kind: string
  source: string
  target: string
  label: string
  required: boolean
}

type Resolution = {
  conflict_id: string
  target: string
  winner_id: string
  loser_action: string
  rename_to: string
  decided_at: string
  decided_by: string
}

type SupersededResolution = Resolution & { superseded_at: string; reason: string }

type Conflict = {
  id: string
  target: string
  kind: string
  kind_label: string
  participants: Writer[]
  resolution: Resolution | null
  resolved: boolean
  resolution_problem: string
}

type Step = {
  key: string
  label: string
  status: string
  value: Unknown
  note: string
  value_type?: string
  from_type?: string
  to_type?: string
  operation?: string
  before?: Unknown
  target?: string
  failure_reason?: string
}

type Outcome = {
  writer_id: string
  kind: string
  source: string
  declared_target: string
  effective_target: string
  status: string
  cause: string
  value: Unknown
  reason: string
  steps: Step[]
}

type PreviewRecord = {
  record_id: string
  record_label: string
  accepted: boolean
  reject_reasons: string[]
  values: Record<string, Unknown>
  outcomes: Outcome[]
}

type Validation = {
  validator_id: string
  target: string
  type: string
  blocking: boolean
  checked: number
  failures: { record_id: string; record_label: string; value: Unknown; reason: string }[]
  status: string
  note?: string
}

type FieldIssue = {
  record_id: string
  record_label: string
  channel: string
  writer_id: string
  source: string
  target: string
  declared_target: string
  status: string
  cause: string
  stage: string
  reason: string
  value: Unknown
}

type Issue = { kind: string; label: string; reason: string }

type Preview = {
  version_id: string
  version: number
  revision: number
  sample_id: string
  sample_name: string
  generated_at: string
  read_only?: boolean
  conflicts: Conflict[]
  records: PreviewRecord[]
  validations: Validation[]
  field_issues: FieldIssue[]
  disabled_mappings: { id: string; source: string; target: string; reason: string }[]
  disabled_cleanse_rules: { id: string; source: string; target: string; reason: string }[]
  blocking_issues: Issue[]
  warnings: Issue[]
  summary: Record<string, number>
  publishable: boolean
  verdict: string
  verdict_note: string
}

type Mapping = {
  id: string
  source: string
  target: string
  convert: string
  default: string
  cleanse: string
  required: boolean
  enabled: boolean
}

type CleanseRule = {
  id: string
  source: string
  output: string
  op: string
  enabled: boolean
}

type Validator = { id: string; target: string; type: string; blocking: boolean }

type Version = {
  id: string
  rule_id: number
  rule_code: string
  name: string
  version: number
  status: string
  created_at: string
  updated_at: string
  updated_by: string
  published_at: string | null
  revision: number
  note: string
  sample_id: string
  mappings: Mapping[]
  cleanse_rules: CleanseRule[]
  validators: Validator[]
  resolutions: Resolution[]
  superseded_resolutions?: SupersededResolution[]
  preview: { revision: number; generated_at: string; verdict: string; publishable: boolean } | null
}

type VersionSummary = Omit<Version, 'mappings' | 'cleanse_rules' | 'validators' | 'resolutions'> & {
  mappings: number
  enabled_mappings: number
  cleanse_rules: number
  validators: number
  unresolved_conflicts: number
  has_preview: boolean
  preview_revision: number | null
}

type Binding = {
  task_code: string
  task_name: string
  rule_id: number
  rule_code: string
  version_id: string
  version: number
  bound_at: string
  semantics: string
}

type Meta = {
  sample: { id: string; name: string; description: string; records: { record_id: string; label: string; payload: Unknown }[] }
  options: {
    conversions: string[]
    cleanse_operations: string[]
    validator_types: string[]
    resolution_strategies: { value: string; label: string }[]
  }
  bindings: Binding[]
}

type ActionEnvelope = { ok: boolean; message: string; entry?: Unknown }

async function postAction(path: string, body?: Unknown): Promise<ActionEnvelope> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
  const payload = (await response.json().catch(() => null)) as ActionEnvelope | null
  if (!response.ok) {
    return { ok: false, message: payload?.message ?? `接口返回 ${response.status}，操作未生效` }
  }
  return payload ?? { ok: false, message: '接口返回为空，操作未生效' }
}

async function sendJson(method: string, path: string, body: Unknown): Promise<ActionEnvelope> {
  const response = await request(path, { method, body: JSON.stringify(body) })
  const payload = (await response.json().catch(() => null)) as ActionEnvelope | null
  if (!response.ok) {
    return { ok: false, message: payload?.message ?? `接口返回 ${response.status}，操作未生效` }
  }
  return payload ?? { ok: false, message: '接口返回为空，操作未生效' }
}

const STATUS_TEXT: Record<string, string> = {
  written: '写入',
  dropped: '丢弃',
  rejected: '拒收',
  empty: '空值未写入',
  held: '冲突挂起',
}

const STEP_STATUS_TEXT: Record<string, string> = {
  ok: '正常',
  applied: '已应用',
  none: '无表达式',
  skipped: '跳过',
  missing: '未取到',
  blank: '空值',
  failed: '失败',
  no_default: '无默认',
  not_needed: '不需要',
  written: '已写入',
  dropped: '已丢弃',
  rejected: '记录拒收',
  empty: '空值未写入',
  held: '冲突挂起',
}

const FIELD_FILTERS = [
  { key: 'all', label: '全部字段' },
  { key: 'written', label: '写入' },
  { key: 'dropped', label: '丢弃' },
  { key: 'rejected', label: '拒收' },
  { key: 'empty', label: '空值' },
  { key: 'held', label: '冲突挂起' },
]

function renderValue(value: Unknown, emptyText = '‹空›'): string {
  if (value === null || value === undefined) return emptyText
  if (typeof value === 'string') return value === '' ? '‹空字符串›' : value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function StepChain({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(false)
  const failedIndex = steps.findIndex((step) => step.status === 'failed')
  return (
    <div className="step-chain">
      <button className="link" type="button" onClick={() => setOpen((prev) => !prev)}>
        {open ? '收起步骤' : `逐步查看（${steps.length} 步）`}
      </button>
      {open && (
        <div className="step-list">
          {steps.map((step, index) => (
            <div key={`${step.key}-${index}`} className={`step-item step-${step.status}`}>
              <span className="step-index">{index + 1}</span>
              <div className="step-body">
                <div className="step-head">
                  <strong>{step.label}</strong>
                  <span className="step-status">{STEP_STATUS_TEXT[step.status] ?? step.status}</span>
                  {step.from_type && step.to_type && <span className="step-meta">{step.from_type} → {step.to_type}</span>}
                  {step.value_type && <span className="step-meta">类型：{step.value_type}</span>}
                  {step.operation && <span className="step-meta">表达式：{step.operation}</span>}
                  {step.target && <span className="step-meta">目标：{step.target}</span>}
                </div>
                <div className="step-value">
                  {step.before !== undefined && <span className="step-before">清洗前 {renderValue(step.before)} → </span>}
                  <span>{renderValue(step.value, '‹无值，不改写为空字符串›')}</span>
                </div>
                {step.note && <div className={step.status === 'failed' ? 'step-note error-text' : 'step-note'}>{step.note}</div>}
                {failedIndex === index && <div className="step-flag">失败点：后续不会用空字符串顶替</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ProjectionView() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [versions, setVersions] = useState<VersionSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [version, setVersion] = useState<Version | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [conflictRevision, setConflictRevision] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [activeTab, setActiveTab] = useState<'mappings' | 'cleanse' | 'validators'>('mappings')
  const [selectedRecord, setSelectedRecord] = useState('')
  const [fieldFilter, setFieldFilter] = useState('all')
  const [sampleText, setSampleText] = useState('')
  const [sampleOpen, setSampleOpen] = useState(false)

  const readable = version?.status === '编辑中'

  const loadVersions = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/versions`)
      const payload = await response.json()
      setVersions(payload.items ?? [])
      return (payload.items ?? []) as VersionSummary[]
    } catch (err) {
      setError(err instanceof Error ? err.message : '投影版本读取失败')
      return []
    }
  }, [])

  const loadConflicts = useCallback(async (versionId: string) => {
    if (!versionId) return
    try {
      const response = await request(`${ENDPOINT}/versions/${versionId}/conflicts`)
      if (!response.ok) throw new Error('冲突列表读取失败')
      const payload = (await response.json()) as { revision: number; items: Conflict[] }
      setConflicts(payload.items ?? [])
      setConflictRevision(payload.revision ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '冲突列表读取失败')
    }
  }, [])

  const loadVersion = useCallback(async (versionId: string) => {
    if (!versionId) return null
    try {
      const response = await request(`${ENDPOINT}/versions/${versionId}`)
      if (!response.ok) throw new Error('投影版本读取失败')
      const payload = (await response.json()) as Version
      setVersion(payload)
      return payload
    } catch (err) {
      setError(err instanceof Error ? err.message : '投影版本读取失败')
      return null
    }
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        const metaResponse = await request(`${ENDPOINT}/meta`)
        const metaPayload = (await metaResponse.json()) as Meta
        setMeta(metaPayload)
      } catch (err) {
        setError(err instanceof Error ? err.message : '投影元数据读取失败')
      }
      const items = await loadVersions()
      const draft = items.find((item) => item.status === '编辑中')
      const active = items.find((item) => item.status === '已启用')
      const first = draft ?? active ?? items[0]
      if (first) {
        setSelectedId(first.id)
        await loadVersion(first.id)
        await loadConflicts(first.id)
      }
    })()
  }, [loadVersions, loadVersion, loadConflicts])

  async function selectVersion(versionId: string) {
    setSelectedId(versionId)
    setPreview(null)
    setMessage('')
    setError('')
    setSelectedRecord('')
    setFieldFilter('all')
    await loadVersion(versionId)
    await loadConflicts(versionId)
    await loadVersions()
  }

  function refreshAfter(entry: Unknown) {
    if (entry && typeof entry === 'object' && 'id' in entry) {
      setVersion(entry as Version)
    } else {
      void loadVersion(selectedId)
    }
    void loadConflicts(selectedId)
    void loadVersions()
  }

  async function runPreview(overrideId?: string) {
    setBusy(true)
    setError('')
    setMessage('')
    let body: Unknown = {}
    if (sampleText.trim()) {
      try {
        body = { sample: JSON.parse(sampleText) }
      } catch (err) {
        setBusy(false)
        setError(`自定义样本不是合法 JSON：${err instanceof Error ? err.message : '解析失败'}`)
        return
      }
    }
    setFieldFilter('all')
    setSelectedRecord('')
    const targetId = overrideId ?? selectedId
    const result = await postAction(`${ENDPOINT}/versions/${targetId}/preview`, body)
    setBusy(false)
    if (result.entry && typeof result.entry === 'object' && 'records' in result.entry) {
      const payload = result.entry as Preview
      setPreview(payload)
      setConflicts(payload.conflicts)
      setConflictRevision(payload.revision)
      setMessage(result.message)
      if (!payload.read_only) {
        await loadVersion(targetId)
        await loadVersions()
      }
    } else {
      setError(result.message)
    }
  }

  async function publishVersion() {
    setBusy(true)
    setError('')
    let result = await postAction(`${ENDPOINT}/versions/${selectedId}/publish`, { confirm_warnings: false })
    if (!result.ok && !result.message.includes('confirm_warnings')) {
      setBusy(false)
      setError(result.message)
      return
    }
    if (!result.ok && result.message.includes('confirm_warnings')) {
      const accept = window.confirm(`${result.message}\n\n确认接受这些警告并把该版本设为现用版本吗？`)
      if (!accept) {
        setBusy(false)
        setError('已取消发布：预演有警告，未获确认。现用版本未被替换。')
        return
      }
      result = await postAction(`${ENDPOINT}/versions/${selectedId}/publish`, { confirm_warnings: true })
    }
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    const detail = await loadVersions()
    const enabled = detail.find((item) => item.status === '已启用')
    if (enabled) {
      setSelectedId(enabled.id)
      await loadVersion(enabled.id)
      await loadConflicts(enabled.id)
      await loadMeta()
      setPreview(null)
    }
  }

  async function forkDraft() {
    setBusy(true)
    setError('')
    const result = await postAction(`${ENDPOINT}/versions/${selectedId}/fork`)
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    const draft = result.entry as Version
    await loadVersions()
    setSelectedId(draft.id)
    await loadVersion(draft.id)
    setPreview(null)
  }

  async function discardDraft() {
    if (!window.confirm('确认丢弃当前编辑中版本？已启用的现用版本不受影响。')) return
    setBusy(true)
    const result = await postAction(`${ENDPOINT}/versions/${selectedId}/discard`)
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    const items = await loadVersions()
    const enabled = items.find((item) => item.status === '已启用')
    if (enabled) {
      setSelectedId(enabled.id)
      await loadVersion(enabled.id)
      setPreview(null)
    }
  }

  async function saveMapping(mapping: Mapping, patch: Partial<Mapping>) {
    setBusy(true)
    setError('')
    const merged = { ...mapping, ...patch }
    const result = await sendJson('PUT', `${ENDPOINT}/versions/${selectedId}/mappings/${mapping.id}`, merged)
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
  }

  async function removeMapping(mappingId: string) {
    if (!window.confirm(`确认从编辑草稿移除映射 ${mappingId}？`)) return
    setBusy(true)
    const result = await sendJson('DELETE', `${ENDPOINT}/versions/${selectedId}/mappings/${mappingId}`, {})
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
  }

  async function addMapping(form: Mapping) {
    setBusy(true)
    setError('')
    const result = await sendJson('POST', `${ENDPOINT}/versions/${selectedId}/mappings`, form)
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return false
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
    return true
  }

  async function addCleanseRule(rule: CleanseRule) {
    setBusy(true)
    setError('')
    const result = await sendJson('POST', `${ENDPOINT}/versions/${selectedId}/cleanse-rules`, rule)
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return false
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
    return true
  }

  async function saveCleanseRule(rule: CleanseRule, patch: Partial<CleanseRule>) {
    setBusy(true)
    setError('')
    const result = await sendJson(
      'PUT',
      `${ENDPOINT}/versions/${selectedId}/cleanse-rules/${rule.id}`,
      { ...rule, ...patch },
    )
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
  }

  async function removeCleanseRule(ruleId: string) {
    if (!window.confirm(`确认移除清洗规则 ${ruleId}？`)) return
    setBusy(true)
    const result = await sendJson('DELETE', `${ENDPOINT}/versions/${selectedId}/cleanse-rules/${ruleId}`, {})
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
  }

  async function saveValidators(next: Validator[]) {
    setBusy(true)
    setError('')
    const result = await sendJson('PUT', `${ENDPOINT}/versions/${selectedId}/validators`, { validators: next })
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
  }

  async function resolveConflict(conflict: Conflict, winnerId: string, loserAction: string, renameTo: string) {
    setBusy(true)
    setError('')
    const result = await postAction(
      `${ENDPOINT}/versions/${selectedId}/conflicts/${encodeURIComponent(conflict.id)}/resolve`,
      { winner_id: winnerId, loser_action: loserAction, rename_to: loserAction === 'rename' ? renameTo : '' },
    )
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    setPreview(null)
    await loadVersion(selectedId)
    await loadConflicts(selectedId)
    await loadVersions()
  }

  async function loadMeta() {
    try {
      const response = await request(`${ENDPOINT}/meta`)
      setMeta((await response.json()) as Meta)
    } catch (err) {
      setError(err instanceof Error ? err.message : '投影元数据读取失败')
    }
  }

  async function rebindTask(versionId: string) {
    const bindings = meta?.bindings ?? []
    const targets = bindings.filter((binding) => binding.version_id !== versionId && binding.rule_id === version?.rule_id)
    if (targets.length === 0) return
    const confirmed = window.confirm(
      `确认把这些任务显式改绑到 ${versionId}：${targets.map((item) => item.task_code).join('、')}？改绑是单独动作，不会自动发生。`,
    )
    if (!confirmed) return
    setBusy(true)
    let allOk = true
    const notes: string[] = []
    for (const binding of targets) {
      const result = await postAction(`${ENDPOINT}/bindings/rebind`, { task_code: binding.task_code, version_id: versionId })
      allOk = allOk && result.ok
      notes.push(result.ok ? result.message : `${binding.task_code}：${result.message}`)
    }
    setBusy(false)
    if (!allOk) {
      setError(notes.join('；'))
    } else {
      setMessage(notes.join('；'))
    }
    await loadMeta()
  }

  async function clearResolution(conflictId: string) {
    setBusy(true)
    const result = await sendJson(
      'DELETE',
      `${ENDPOINT}/versions/${selectedId}/conflicts/${encodeURIComponent(conflictId)}/resolve`,
      {},
    )
    setBusy(false)
    if (!result.ok) {
      setError(result.message)
      return
    }
    setMessage(result.message)
    setPreview(null)
    refreshAfter(result.entry)
  }

  const currentRecord = useMemo(() => {
    if (!preview) return null
    if (!selectedRecord) return preview.records[0] ?? null
    return preview.records.find((record) => record.record_id === selectedRecord) ?? preview.records[0] ?? null
  }, [preview, selectedRecord])

  const visibleOutcomes = useMemo(() => {
    if (!currentRecord) return []
    if (fieldFilter === 'all') return currentRecord.outcomes
    return currentRecord.outcomes.filter((outcome) => outcome.status === fieldFilter)
  }, [currentRecord, fieldFilter])

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    if (currentRecord) {
      for (const outcome of currentRecord.outcomes) {
        counts[outcome.status] = (counts[outcome.status] ?? 0) + 1
      }
    }
    return counts
  }, [currentRecord])

  const sampleJson = useMemo(() => {
    if (!meta) return ''
    return JSON.stringify(meta.sample, null, 2)
  }, [meta])

  return (
    <section className="page projection-page" data-module="projection">
      <header className="page-head">
        <div>
          <h2>字段投影编辑器</h2>
          <p className="page-desc">
            在隔离的编辑草稿里调整投影，逐步预演源字段取值、类型转换、默认填充与清洗结果，再用必填和唯一性校验把关；
            未通过预演不能替换现用版本。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => void runPreview()} disabled={busy || !readable}>
            运行预演
          </button>
          {version && !readable && (
            <button className="btn" type="button" onClick={() => void runPreview()} disabled={busy}>
              只读查看口径
            </button>
          )}
          {version?.status === '编辑中' && (
            <button className="btn primary" type="button" onClick={() => void publishVersion()} disabled={busy}>
              发布为新现用版本
            </button>
          )}
          {version?.status === '已启用' && (
            <button className="btn primary" type="button" onClick={() => void forkDraft()} disabled={busy}>
              派生编辑草稿
            </button>
          )}
        </div>
      </header>

      <div className="version-strip">
        {versions.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`version-chip ${item.id === selectedId ? 'selected' : ''} version-${item.status}`}
            onClick={() => void selectVersion(item.id)}
          >
            <span className="version-id">{item.id}</span>
            <span className="version-meta">v{item.version} · {item.status}</span>
            {item.unresolved_conflicts > 0 && <span className="version-alert">{item.unresolved_conflicts} 个未裁决冲突</span>}
          </button>
        ))}
      </div>

      <div className="isolation-note">
        {version?.status === '已启用' && (
          <span>
            已启用版本 <strong>{version.id}</strong> 只读。修改请先派生编辑草稿，现用版本不会被编辑；发布后既有任务绑定仍按原版本口径执行。
          </span>
        )}
        {version?.status === '已停用' && <span>已停用版本 <strong>{version.id}</strong> 仅作历史查看，不能编辑或发布。</span>}
        {version?.status === '编辑中' && (
          <span>
            编辑中版本 <strong>{version.id}</strong> 与现用版本隔离；revision {version.revision}。任何修改都会让旧预演失效，必须重新预演才能发布。
          </span>
        )}
        {meta && meta.bindings.length > 0 && (
          <span className="binding-note">
            既有任务绑定：
            {meta.bindings.map((binding) => (
              `${binding.task_code} → ${binding.version_id}(v${binding.version})`
            )).join('；')}
          </span>
        )}
      </div>

      <div className="panel binding-panel">
        <div className="panel-head">
          <div>
            <h3>任务绑定口径</h3>
            <p className="panel-desc">
              发布新版本只替换现用版本，不改写任务绑定；既有任务继续按绑定版本执行，需要切换时必须显式改绑。
            </p>
          </div>
          {version?.status === '已启用' && meta && meta.bindings.some((binding) => binding.rule_id === version.rule_id && binding.version_id !== version.id) && (
            <button className="btn" type="button" disabled={busy}
              onClick={() => void rebindTask(version.id)}>
              把该规则下的任务显式改绑到 {version.id}
            </button>
          )}
        </div>
        <table className="data-table">
          <thead>
            <tr><th>任务</th><th>规则</th><th>绑定版本</th><th>绑定时间</th><th>口径说明</th></tr>
          </thead>
          <tbody>
            {(meta?.bindings ?? []).map((binding) => (
              <tr key={binding.task_code}>
                <td>{binding.task_code} · {binding.task_name}</td>
                <td>{binding.rule_code}</td>
                <td>
                  <span className={binding.version_id === version?.id ? 'badge ok' : 'badge'}>
                    {binding.version_id}（v{binding.version}）
                  </span>
                </td>
                <td>{binding.bound_at}</td>
                <td>{binding.semantics}</td>
              </tr>
            ))}
            {(meta?.bindings ?? []).length === 0 && <tr><td className="empty-state" colSpan={5}>没有任务绑定记录</td></tr>}
          </tbody>
        </table>
      </div>

      {preview && (
        <div className="stat-row">
          <article className="stat-card">
            <span className="stat-label">样本记录</span>
            <strong className="stat-value">{preview.summary.records}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">通过 / 拒收</span>
            <strong className="stat-value">
              {preview.summary.accepted} / {preview.summary.rejected}
            </strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">写入字段</span>
            <strong className="stat-value">{preview.summary.written_fields}</strong>
          </article>
          <article className="stat-card">
            <span className="stat-label">丢弃 / 空值 / 挂起</span>
            <strong className="stat-value">
              {preview.summary.dropped_fields} / {preview.summary.empty_fields} / {preview.summary.held_fields}
            </strong>
          </article>
          <article className={`stat-card stat-verdict ${preview.publishable ? 'ok' : 'bad'}`}>
            <span className="stat-label">预演结论 · revision {preview.revision}</span>
            <strong className="stat-value">{preview.verdict}</strong>
            <span className="stat-label">{preview.generated_at}</span>
          </article>
        </div>
      )}

      {preview && (
        <div className={`verdict-bar ${preview.publishable ? 'ok' : 'bad'}`}>
          <strong>{preview.verdict_note}</strong>
          {preview.read_only && <span>（只读版本仅用于查看口径，不产生发布资格）</span>}
        </div>
      )}

      {version && (
        <>
          <div className="panel">
            <div className="panel-head">
              <div>
                <h3>映射与清洗</h3>
                <p className="panel-desc">
                  当前版本 {version.id} · 规则 {version.rule_code} · {version.mappings.length} 条映射 ·
                  {version.cleanse_rules.length} 条清洗规则 · {version.validators.length} 条校验规则
                </p>
              </div>
              <div className="tab-row">
                <button type="button" className={`tab ${activeTab === 'mappings' ? 'active' : ''}`} onClick={() => setActiveTab('mappings')}>
                  映射
                </button>
                <button type="button" className={`tab ${activeTab === 'cleanse' ? 'active' : ''}`} onClick={() => setActiveTab('cleanse')}>
                  清洗规则
                </button>
                <button type="button" className={`tab ${activeTab === 'validators' ? 'active' : ''}`} onClick={() => setActiveTab('validators')}>
                  校验规则
                </button>
              </div>
            </div>

            {activeTab === 'mappings' && (
              <MappingsPanel
                version={version}
                readable={Boolean(readable)}
                conversions={meta?.options.conversions ?? []}
                cleansers={meta?.options.cleanse_operations ?? []}
                busy={busy}
                onSave={saveMapping}
                onRemove={removeMapping}
                onAdd={addMapping}
              />
            )}

            {activeTab === 'cleanse' && (
              <CleansePanel
                version={version}
                readable={Boolean(readable)}
                operations={meta?.options.cleanse_operations ?? []}
                busy={busy}
                onSave={saveCleanseRule}
                onRemove={removeCleanseRule}
                onAdd={addCleanseRule}
              />
            )}

            {activeTab === 'validators' && (
              <ValidatorsPanel
                version={version}
                readable={Boolean(readable)}
                types={meta?.options.validator_types ?? []}
                busy={busy}
                onSave={saveValidators}
              />
            )}
          </div>

          <div className="panel">
            <div className="panel-head">
              <div>
                <h3>冲突裁决</h3>
                <p className="panel-desc">
                  多个源字段写入同一目标、或目标名与清洗输出重名时，系统不会按顺序静默覆盖；必须明确保留谁、落选字段是丢弃还是重命名。
                </p>
              </div>
            </div>
            {conflictRevision !== null && (
              <p className="panel-desc">当前 revision {conflictRevision}；
                {conflicts.length === 0 ? '没有写入冲突。' : conflicts.filter((c) => !c.resolved).length + ' 个冲突尚未裁决。'}
              </p>
            )}
            {(version?.superseded_resolutions ?? []).length > 0 && (
              <div className="superseded-box">
                <h4>已失效的历史裁决</h4>
                {(version?.superseded_resolutions ?? []).map((item, index) => (
                  <div key={`${item.conflict_id}-${index}`} className="superseded-item">
                    {item.conflict_id}：曾保留 {item.winner_id}、落选字段
                    {item.loser_action === 'drop' ? '丢弃' : `重命名为「${item.rename_to}」`}；
                    {item.superseded_at} 失效（{item.reason}）。不作为当前裁决使用。
                  </div>
                ))}
              </div>
            )}
            {conflicts.map((conflict) => (
              <ConflictCard
                key={conflict.id}
                conflict={conflict}
                readable={Boolean(readable)}
                busy={busy}
                onResolve={resolveConflict}
                onClear={clearResolution}
              />
            ))}
          </div>

          <div className="panel preview-panel">
            <div className="panel-head">
              <div>
                <h3>预演工作台</h3>
                <p className="panel-desc">
                  样本 {preview?.sample_name ?? meta?.sample.name ?? '图书嵌套样本'}：
                  {meta?.sample.description}
                </p>
              </div>
              <div className="page-actions">
                <button className="btn ghost" type="button" onClick={() => setSampleOpen((prev) => !prev)}>
                  {sampleOpen ? '收起自定义样本' : '自定义样本 JSON'}
                </button>
                <button className="btn" type="button" onClick={() => void runPreview()} disabled={busy}>
                  {busy ? '预演中…' : readable ? '运行预演' : '只读查看口径'}
                </button>
              </div>
            </div>

            {sampleOpen && (
              <div className="sample-editor">
                <p className="panel-desc">默认样本可以直接预演；如需改样本，编辑下面的 JSON 后再次运行预演。样本只影响预演，不改变已保存的映射。</p>
                <div className="page-actions">
                  <button className="btn ghost" type="button" onClick={() => setSampleText(sampleJson)}>
                    载入默认样本
                  </button>
                  <button className="btn ghost" type="button" onClick={() => setSampleText('')}>
                    清空使用默认
                  </button>
                </div>
                <textarea
                  className="sample-textarea"
                  value={sampleText}
                  placeholder={sampleJson}
                  onChange={(event) => setSampleText(event.target.value)}
                  rows={12}
                />
              </div>
            )}

            {!preview && <p className="empty-state">尚未预演。运行后可以逐条记录、逐字段查看取值、类型转换、默认填充、清洗与写入结果。</p>}

            {preview && (
              <>
                <div className="preview-toolbar">
                  <div className="record-tabs">
                    {preview.records.map((record) => (
                      <button
                        key={record.record_id}
                        type="button"
                        className={`record-chip ${currentRecord?.record_id === record.record_id ? 'selected' : ''} ${record.accepted ? 'accepted' : 'rejected'}`}
                        onClick={() => setSelectedRecord(record.record_id)}
                      >
                        {record.record_id} · {record.record_label}
                        <span className="record-flag">{record.accepted ? '通过' : '拒收'}</span>
                      </button>
                    ))}
                  </div>
                  <div className="filter-row">
                    {FIELD_FILTERS.map((filter) => (
                      <button
                        key={filter.key}
                        type="button"
                        className={`pill ${fieldFilter === filter.key ? 'active' : ''}`}
                        onClick={() => setFieldFilter(filter.key)}
                      >
                        {filter.label}
                        {filter.key !== 'all' && ` ${statusCounts[filter.key] ?? 0}`}
                      </button>
                    ))}
                  </div>
                </div>

                {currentRecord && (
                  <div className="record-card">
                    <div className="record-head">
                      <strong>{currentRecord.record_id} · {currentRecord.record_label}</strong>
                      <span className={currentRecord.accepted ? 'badge ok' : 'badge bad'}>
                        {currentRecord.accepted ? '记录通过' : '记录拒收'}
                      </span>
                    </div>
                    {currentRecord.reject_reasons.length > 0 && (
                      <ul className="reason-list">
                        {currentRecord.reject_reasons.map((reason, index) => (
                          <li key={index} className="error-text">{reason}</li>
                        ))}
                      </ul>
                    )}
                    <div className="target-values">
                      <span className="panel-desc">清洗后的目标值：</span>
                      {Object.keys(currentRecord.values).length === 0
                        ? <span>没有写入任何目标字段</span>
                        : Object.entries(currentRecord.values).map(([key, value]) => (
                          <span key={key} className="kv-chip">
                            <strong>{key}</strong>=<code>{renderValue(value)}</code>
                          </span>
                        ))}
                    </div>

                    <table className="data-table field-table">
                      <thead>
                        <tr>
                          <th>源字段</th>
                          <th>声明目标</th>
                          <th>实际目标</th>
                          <th>结果</th>
                          <th>说明</th>
                          <th>步骤</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visibleOutcomes.map((outcome) => (
                          <tr key={`${outcome.writer_id}-${outcome.declared_target}`} className={`row-${outcome.status}`}>
                            <td><code>{outcome.source}</code></td>
                            <td>{outcome.declared_target}</td>
                            <td>{outcome.effective_target}</td>
                            <td><span className={`badge status-${outcome.status}`}>{STATUS_TEXT[outcome.status] ?? outcome.status}</span></td>
                            <td className={outcome.status === 'written' || outcome.status === 'held' ? '' : 'reason-cell'}>
                              {outcome.status === 'written' ? renderValue(outcome.value) : outcome.reason}
                              {outcome.status === 'held' && (
                                <button
                                  className="link"
                                  type="button"
                                  onClick={() => document.getElementById(`conflict-${outcome.declared_target}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}
                                >
                                  去裁决
                                </button>
                              )}
                            </td>
                            <td><StepChain steps={outcome.steps} /></td>
                          </tr>
                        ))}
                        {visibleOutcomes.length === 0 && (
                          <tr><td className="empty-state" colSpan={6}>当前筛选下没有字段</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}

                <div className="explain-grid">
                  <IssueList title="阻断问题（不解决不能发布）" items={preview.blocking_issues} tone="bad" />
                  <IssueList title="警告与丢弃明细（可确认后发布）" items={preview.warnings} tone="warn" />
                </div>

                {(preview.disabled_mappings.length > 0 || preview.disabled_cleanse_rules.length > 0) && (
                  <div className="disabled-box">
                    <h4>未参与投影的写入方</h4>
                    {[...preview.disabled_mappings, ...preview.disabled_cleanse_rules].map((item) => (
                      <div key={`${item.id}-${item.target}`} className="disabled-item">
                        <strong>{item.id}</strong> {item.source} → {item.target}：{item.reason}
                      </div>
                    ))}
                  </div>
                )}

                <div className="validation-box">
                  <h4>校验结果（必填与唯一性）</h4>
                  <table className="data-table">
                    <thead>
                      <tr><th>规则</th><th>目标</th><th>类型</th><th>级别</th><th>检查记录数</th><th>结论</th><th>失败明细</th></tr>
                    </thead>
                    <tbody>
                      {preview.validations.map((validation) => (
                        <tr key={validation.validator_id}>
                          <td>{validation.validator_id}</td>
                          <td>{validation.target}</td>
                          <td>{validation.type}</td>
                          <td>{validation.blocking ? '阻断' : '仅告警'}</td>
                          <td>{validation.checked}</td>
                          <td>
                            <span className={`badge ${validation.status === 'passed' ? 'ok' : 'bad'}`}>
                              {validation.status === 'passed' ? '通过' : '未通过'}
                            </span>
                          </td>
                          <td>
                            {validation.note && <div>{validation.note}</div>}
                            {validation.failures.map((failure) => (
                              <div key={`${failure.record_id}-${failure.reason}`} className="validation-failure">
                                {failure.record_id}：{failure.reason}（值：{renderValue(failure.value, '‹空›')}）
                              </div>
                            ))}
                            {validation.failures.length === 0 && !validation.note && '—'}
                          </td>
                        </tr>
                      ))}
                      {preview.validations.length === 0 && (
                        <tr><td className="empty-state" colSpan={7}>当前版本没有校验规则</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </>
      )}

      <footer className="page-foot">
        <span className="foot-actions">
          {version?.status === '编辑中' && (
            <button className="btn ghost" type="button" onClick={() => void discardDraft()} disabled={busy}>
              丢弃编辑中版本
            </button>
          )}
          {message && <span className="ok-text">{message}</span>}
          {error && <span className="error-text">{error}</span>}
        </span>
        <span>
          {version && `${version.id} · ${version.status} · revision ${version.revision} · 更新 ${version.updated_at}`}
        </span>
      </footer>
    </section>
  )
}

function IssueList({ title, items, tone }: { title: string; items: Issue[]; tone: 'bad' | 'warn' }) {
  return (
    <div className={`issue-box ${tone}`}>
      <h4>
        {title} <span className="issue-count">{items.length}</span>
      </h4>
      {items.length === 0 && <p className="panel-desc">无</p>}
      {items.map((item, index) => (
        <div key={`${item.kind}-${index}`} className="issue-item">
          <strong>{item.label}</strong>
          <span>{item.reason}</span>
        </div>
      ))}
    </div>
  )
}

function MappingsPanel(props: {
  version: Version
  readable: boolean
  conversions: string[]
  cleansers: string[]
  busy: boolean
  onSave: (mapping: Mapping, patch: Partial<Mapping>) => Promise<void>
  onRemove: (mappingId: string) => Promise<void>
  onAdd: (form: Mapping) => Promise<boolean>
}) {
  const { version, readable, conversions, cleansers, busy, onSave, onRemove, onAdd } = props
  const [drafts, setDrafts] = useState<Record<string, Mapping>>({})
  const [form, setForm] = useState<Mapping>({
    id: '', source: '', target: '', convert: '文本', default: '', cleanse: '无', required: false, enabled: true,
  })

  function draftFor(mapping: Mapping): Mapping {
    return drafts[mapping.id] ?? mapping
  }

  function patchDraft(mapping: Mapping, patch: Partial<Mapping>) {
    setDrafts((prev) => ({ ...prev, [mapping.id]: { ...draftFor(mapping), ...patch } }))
  }

  return (
    <div className="mapping-panel">
      <table className="data-table">
        <thead>
          <tr>
            <th>映射</th><th>源字段</th><th>目标字段</th><th>类型转换</th><th>默认填充</th><th>清洗表达式</th><th>必填</th><th>参与</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {version.mappings.map((mapping) => {
            const draft = draftFor(mapping)
            return (
              <tr key={mapping.id}>
                <td><code>{mapping.id}</code></td>
                <td>
                  <input className="cell-input" value={draft.source} disabled={!readable || busy}
                    onChange={(event) => patchDraft(mapping, { source: event.target.value })} />
                </td>
                <td>
                  <input className="cell-input" value={draft.target} disabled={!readable || busy}
                    onChange={(event) => patchDraft(mapping, { target: event.target.value })} />
                </td>
                <td>
                  <select className="cell-input" value={draft.convert} disabled={!readable || busy}
                    onChange={(event) => patchDraft(mapping, { convert: event.target.value })}>
                    {conversions.map((option) => <option key={option}>{option}</option>)}
                  </select>
                </td>
                <td>
                  <input className="cell-input" value={draft.default} disabled={!readable || busy}
                    placeholder="留空表示不填充" onChange={(event) => patchDraft(mapping, { default: event.target.value })} />
                </td>
                <td>
                  <select className="cell-input" value={draft.cleanse} disabled={!readable || busy}
                    onChange={(event) => patchDraft(mapping, { cleanse: event.target.value })}>
                    {cleansers.map((option) => <option key={option}>{option}</option>)}
                  </select>
                </td>
                <td>
                  <label className="check">
                    <input type="checkbox" checked={draft.required} disabled={!readable || busy}
                      onChange={(event) => patchDraft(mapping, { required: event.target.checked })} />
                    必填
                  </label>
                </td>
                <td>
                  <label className="check">
                    <input type="checkbox" checked={draft.enabled} disabled={!readable || busy}
                      onChange={(event) => patchDraft(mapping, { enabled: event.target.checked })} />
                    启用
                  </label>
                </td>
                <td className="row-actions">
                  <button className="link" type="button" disabled={!readable || busy}
                    onClick={() => {
                      void onSave(mapping, draft).then(() => {
                        setDrafts((prev) => {
                          const next = { ...prev }
                          delete next[mapping.id]
                          return next
                        })
                      })
                    }}>
                    保存
                  </button>
                  <button className="link danger" type="button" disabled={!readable || busy} onClick={() => void onRemove(mapping.id)}>
                    移除
                  </button>
                </td>
              </tr>
            )
          })}
          {version.mappings.length === 0 && <tr><td className="empty-state" colSpan={9}>还没有映射</td></tr>}
        </tbody>
      </table>

      {readable && (
        <div className="inline-add">
          <span className="panel-desc">新增映射：</span>
          <input className="cell-input" placeholder="源字段，如 book.title" value={form.source}
            onChange={(event) => setForm((prev) => ({ ...prev, source: event.target.value }))} />
          <input className="cell-input" placeholder="目标字段" value={form.target}
            onChange={(event) => setForm((prev) => ({ ...prev, target: event.target.value }))} />
          <select className="cell-input" value={form.convert} onChange={(event) => setForm((prev) => ({ ...prev, convert: event.target.value }))}>
            {conversions.map((option) => <option key={option}>{option}</option>)}
          </select>
          <input className="cell-input" placeholder="默认填充（可空）" value={form.default}
            onChange={(event) => setForm((prev) => ({ ...prev, default: event.target.value }))} />
          <select className="cell-input" value={form.cleanse} onChange={(event) => setForm((prev) => ({ ...prev, cleanse: event.target.value }))}>
            {cleansers.map((option) => <option key={option}>{option}</option>)}
          </select>
          <label className="check"><input type="checkbox" checked={form.required}
            onChange={(event) => setForm((prev) => ({ ...prev, required: event.target.checked }))} />必填</label>
          <button className="btn" type="button" disabled={busy}
            onClick={() => {
              void onAdd({ ...form, id: '' }).then((ok) => {
                if (ok) setForm({ id: '', source: '', target: '', convert: '文本', default: '', cleanse: '无', required: false, enabled: true })
              })
            }}>
            加入草稿
          </button>
        </div>
      )}
    </div>
  )
}

function CleansePanel(props: {
  version: Version
  readable: boolean
  operations: string[]
  busy: boolean
  onSave: (rule: CleanseRule, patch: Partial<CleanseRule>) => Promise<void>
  onRemove: (ruleId: string) => Promise<void>
  onAdd: (rule: CleanseRule) => Promise<boolean>
}) {
  const { version, readable, operations, busy, onSave, onRemove, onAdd } = props
  const [drafts, setDrafts] = useState<Record<string, CleanseRule>>({})
  const [form, setForm] = useState<CleanseRule>({ id: '', source: '', output: '', op: '标签去空项', enabled: true })

  function draftFor(rule: CleanseRule): CleanseRule {
    return drafts[rule.id] ?? rule
  }

  function patchDraft(rule: CleanseRule, patch: Partial<CleanseRule>) {
    setDrafts((prev) => ({ ...prev, [rule.id]: { ...draftFor(rule), ...patch } }))
  }

  return (
    <div className="mapping-panel">
      <p className="panel-desc">
        独立清洗规则的输出字段与投影目标同名时会进入冲突裁决，不会自动覆盖。
      </p>
      <table className="data-table">
        <thead>
          <tr><th>规则</th><th>源字段</th><th>输出字段</th><th>清洗表达式</th><th>参与</th><th>操作</th></tr>
        </thead>
        <tbody>
          {version.cleanse_rules.map((rule) => {
            const draft = draftFor(rule)
            return (
              <tr key={rule.id}>
                <td><code>{rule.id}</code></td>
                <td><input className="cell-input" value={draft.source} disabled={!readable || busy}
                  onChange={(event) => patchDraft(rule, { source: event.target.value })} /></td>
                <td><input className="cell-input" value={draft.output} disabled={!readable || busy}
                  onChange={(event) => patchDraft(rule, { output: event.target.value })} /></td>
                <td>
                  <select className="cell-input" value={draft.op} disabled={!readable || busy}
                    onChange={(event) => patchDraft(rule, { op: event.target.value })}>
                    {operations.filter((option) => option !== '无').map((option) => <option key={option}>{option}</option>)}
                  </select>
                </td>
                <td><label className="check"><input type="checkbox" checked={draft.enabled} disabled={!readable || busy}
                  onChange={(event) => patchDraft(rule, { enabled: event.target.checked })} />启用</label></td>
                <td className="row-actions">
                  <button className="link" type="button" disabled={!readable || busy}
                    onClick={() => {
                      void onSave(rule, draft).then(() => {
                        setDrafts((prev) => {
                          const next = { ...prev }
                          delete next[rule.id]
                          return next
                        })
                      })
                    }}>保存</button>
                  <button className="link danger" type="button" disabled={!readable || busy} onClick={() => void onRemove(rule.id)}>移除</button>
                </td>
              </tr>
            )
          })}
          {version.cleanse_rules.length === 0 && <tr><td className="empty-state" colSpan={6}>没有独立清洗规则</td></tr>}
        </tbody>
      </table>

      {readable && (
        <div className="inline-add">
          <span className="panel-desc">新增清洗规则：</span>
          <input className="cell-input" placeholder="源字段" value={form.source}
            onChange={(event) => setForm((prev) => ({ ...prev, source: event.target.value }))} />
          <input className="cell-input" placeholder="输出字段" value={form.output}
            onChange={(event) => setForm((prev) => ({ ...prev, output: event.target.value }))} />
          <select className="cell-input" value={form.op} onChange={(event) => setForm((prev) => ({ ...prev, op: event.target.value }))}>
            {operations.filter((option) => option !== '无').map((option) => <option key={option}>{option}</option>)}
          </select>
          <button className="btn" type="button" disabled={busy}
            onClick={() => {
              void onAdd({ ...form, id: '' }).then((ok) => {
                if (ok) setForm({ id: '', source: '', output: '', op: '标签去空项', enabled: true })
              })
            }}>加入草稿</button>
        </div>
      )}
    </div>
  )
}

function ValidatorsPanel(props: {
  version: Version
  readable: boolean
  types: string[]
  busy: boolean
  onSave: (next: Validator[]) => Promise<void>
}) {
  const { version, readable, types, busy, onSave } = props
  const [rows, setRows] = useState<Validator[]>(version.validators)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    setRows(version.validators)
    setDirty(false)
  }, [version.id, version.revision, version.validators])

  function patch(index: number, next: Partial<Validator>) {
    setRows((prev) => prev.map((row, rowIndex) => (rowIndex === index ? { ...row, ...next } : row)))
    setDirty(true)
  }

  return (
    <div className="mapping-panel">
      <p className="panel-desc">
        必填校验检查通过记录的目标字段是否为空；唯一性校验按清洗后的值归并。阻断级失败会进入预演阻断清单，不允许发布。
      </p>
      <table className="data-table">
        <thead>
          <tr><th>规则</th><th>校验目标</th><th>校验类型</th><th>阻断级</th><th>操作</th></tr>
        </thead>
        <tbody>
          {rows.map((validator, index) => (
            <tr key={validator.id || index}>
              <td><code>{validator.id || '新增'}</code></td>
              <td><input className="cell-input" value={validator.target} disabled={!readable || busy}
                onChange={(event) => patch(index, { target: event.target.value })} /></td>
              <td>
                <select className="cell-input" value={validator.type} disabled={!readable || busy}
                  onChange={(event) => patch(index, { type: event.target.value })}>
                  {types.map((option) => <option key={option}>{option}</option>)}
                </select>
              </td>
              <td><label className="check"><input type="checkbox" checked={validator.blocking} disabled={!readable || busy}
                onChange={(event) => patch(index, { blocking: event.target.checked })} />阻断</label></td>
              <td className="row-actions">
                <button className="link danger" type="button" disabled={!readable || busy}
                  onClick={() => { setRows((prev) => prev.filter((_, rowIndex) => rowIndex !== index)); setDirty(true) }}>
                  移除
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td className="empty-state" colSpan={5}>没有校验规则</td></tr>}
        </tbody>
      </table>
      {readable && (
        <div className="inline-add">
          <button className="btn ghost" type="button" disabled={busy}
            onClick={() => {
              setRows((prev) => [...prev, { id: '', target: '', type: types[0] ?? '必填', blocking: true }])
              setDirty(true)
            }}>新增校验规则</button>
          <button className="btn primary" type="button" disabled={busy || !dirty}
            onClick={() => void onSave(rows.filter((row) => row.target.trim()))}>
            保存校验规则
          </button>
        </div>
      )}
    </div>
  )
}

function ConflictCard(props: {
  conflict: Conflict
  readable: boolean
  busy: boolean
  onResolve: (conflict: Conflict, winnerId: string, loserAction: string, renameTo: string) => Promise<void>
  onClear: (conflictId: string) => Promise<void>
}) {
  const { conflict, readable, busy, onResolve, onClear } = props
  const resolution = conflict.resolution
  const [winner, setWinner] = useState(resolution?.winner_id ?? conflict.participants[0]?.id ?? '')
  const [loserAction, setLoserAction] = useState(resolution?.loser_action ?? 'drop')
  const [renameTo, setRenameTo] = useState(resolution?.rename_to ?? '')

  useEffect(() => {
    setWinner(resolution?.winner_id ?? conflict.participants[0]?.id ?? '')
    setLoserAction(resolution?.loser_action ?? 'drop')
    setRenameTo(resolution?.rename_to ?? '')
  }, [conflict.id, resolution?.winner_id, resolution?.loser_action, resolution?.rename_to, conflict.participants])

  return (
    <article id={`conflict-${conflict.target}`} className={`conflict-card ${conflict.resolved ? 'resolved' : 'unresolved'}`}>
      <div className="conflict-head">
        <div>
          <strong>{conflict.kind_label}：{conflict.target}</strong>
          <span className="conflict-id">{conflict.id}</span>
        </div>
        <span className={`badge ${conflict.resolved ? 'ok' : 'bad'}`}>{conflict.resolved ? '已裁决' : '未裁决'}</span>
      </div>
      <div className="conflict-participants">
        {conflict.participants.map((participant) => (
          <label key={participant.id} className={`participant ${winner === participant.id ? 'selected' : ''}`}>
            <input
              type="radio"
              name={`conflict-${conflict.id}`}
              checked={winner === participant.id}
              disabled={!readable || busy}
              onChange={() => setWinner(participant.id)}
            />
            <span>
              <strong>{participant.id}</strong>
              <em>{participant.kind === 'cleanse' ? '清洗规则' : '映射'}</em>
              {participant.source} → {participant.target}
              {participant.required && <span className="badge warn">必填</span>}
            </span>
          </label>
        ))}
      </div>
      <div className="conflict-actions">
        <span className="panel-desc">保留谁：从上面选择一个写入方。落选字段：</span>
        <select className="cell-input" value={loserAction} disabled={!readable || busy}
          onChange={(event) => setLoserAction(event.target.value)}>
          <option value="drop">丢弃（推荐：明确告诉下游该字段被舍弃）</option>
          <option value="rename">重命名为新目标</option>
        </select>
        {loserAction === 'rename' && (
          <input className="cell-input" placeholder="新的目标字段名" value={renameTo} disabled={!readable || busy}
            onChange={(event) => setRenameTo(event.target.value)} />
        )}
        <button className="btn primary" type="button" disabled={!readable || busy || !winner}
          onClick={() => void onResolve(conflict, winner, loserAction, renameTo)}>
          {resolution ? '更新裁决' : '提交裁决'}
        </button>
        {resolution && (
          <button className="btn ghost" type="button" disabled={!readable || busy}
            onClick={() => void onClear(conflict.id)}>撤销裁决</button>
        )}
      </div>
      {resolution && (
        <div className={`resolution-note ${conflict.resolved ? '' : 'error-text'}`}>
          当前裁决：保留 <strong>{resolution.winner_id}</strong>，落选字段
          {resolution.loser_action === 'drop' ? '丢弃' : `重命名为「${resolution.rename_to}」`}
          （{resolution.decided_by} {resolution.decided_at}）
          {!conflict.resolved && `；但仍不通过：${conflict.resolution_problem}`}
        </div>
      )}
      {!resolution && <div className="resolution-note">{conflict.resolution_problem}</div>}
    </article>
  )
}
