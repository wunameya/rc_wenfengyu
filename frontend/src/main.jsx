import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Activity, AlertTriangle, Bell, Check, ChevronRight, FlaskConical, RefreshCw, RotateCcw, Search, Send, Settings, X } from 'lucide-react'
import './styles.css'

const STATUS = {
  PENDING: { label: '等待投递', tone: 'neutral' },
  PROCESSING: { label: '投递中', tone: 'info' },
  RETRY_WAIT: { label: '等待重试', tone: 'warning' },
  SUCCEEDED: { label: '投递成功', tone: 'success' },
  DEAD: { label: '投递失败', tone: 'danger' },
}

const FILTERS = [
  { value: 'failed', label: '全部异常', statuses: ['RETRY_WAIT', 'DEAD'] },
  { value: 'dead', label: '投递失败', statuses: ['DEAD'] },
  { value: 'retry', label: '等待重试', statuses: ['RETRY_WAIT'] },
  { value: 'processing', label: '投递中', statuses: ['PROCESSING'] },
  { value: 'success', label: '投递成功', statuses: ['SUCCEEDED'] },
]

function Icon({ name }) {
  const components = { delivery: Send, test: FlaskConical, settings: Settings, refresh: RefreshCw, alert: AlertTriangle, retry: RotateCcw, activity: Activity, check: Check, search: Search, close: X, bell: Bell, chevron: ChevronRight }
  const Component = components[name]
  return <Component aria-hidden="true" />
}

function Sidebar({ view, onChange }) {
  const items = [
    ['failures', 'delivery', '投递记录'],
    ['test', 'test', '通知测试'],
    ['settings', 'settings', '运行设置'],
  ]
  return <aside className="sidebar">
    <div className="sidebar-brand"><div className="brand-logo">N</div><div><strong>NotifyOps</strong><span>消息投递平台</span></div></div>
    <div className="nav-label">工作台</div>
    <nav className="side-nav">{items.map(([key, icon, label]) => <button key={key} className={view === key ? 'active' : ''} onClick={() => onChange(key)}><Icon name={icon} /><span>{label}</span>{key === 'failures' && <em>!</em>}</button>)}</nav>
    <div className="sidebar-status"><span><i />服务运行中</span><small>API · Worker · MySQL</small></div>
    <div className="sidebar-version">NotifyOps <span>v1.0.0</span></div>
  </aside>
}

function Topbar({ view }) {
  const title = view === 'test' ? '通知测试' : view === 'settings' ? '运行设置' : '通知投递管理'
  return <header className="topbar"><div><span>工作台</span><b>/</b><strong>{title}</strong></div><div className="topbar-actions"><button aria-label="通知"><Icon name="bell" /></button><span className="avatar">WF</span><div><strong>管理员</strong><small>系统运维</small></div></div></header>
}

function PageHeading({ eyebrow, title, description, action }) {
  return <section className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{action}</section>
}

const formatTime = (value) => {
  if (!value) return '—'
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  }).format(new Date(normalized))
}

const formatJson = (value) => JSON.stringify(value, null, 2)

async function api(path, options) {
  const response = await fetch(path, options)
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      message = body.detail || message
    } catch { /* 保留默认错误 */ }
    throw new Error(message)
  }
  return response.json()
}

function StatusBadge({ value }) {
  const status = STATUS[value] || { label: value, tone: 'neutral' }
  return <span className={`status status--${status.tone}`}><i />{status.label}</span>
}

function SummaryCard({ label, value, hint, tone, icon }) {
  return (
    <article className="summary-card">
      <div className={`metric-icon metric-icon--${tone}`}><Icon name={icon} /></div>
      <div><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>
    </article>
  )
}

function PanelTitle({ index, title, hint }) {
  return <div className="panel-title"><div><span>{index}</span><h2>{title}</h2></div>{hint && <small>{hint}</small>}</div>
}

function JsonBlock({ title, value }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      <pre>{formatJson(value)}</pre>
    </section>
  )
}

function TaskDetailPanel({ taskId, onClose, onRetried }) {
  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retrying, setRetrying] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    api(`/api/v1/tasks/${taskId}`)
      .then((data) => active && setTask(data))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => { active = false }
  }, [taskId])

  const retry = async () => {
    setRetrying(true)
    setError('')
    try {
      await api(`/api/v1/tasks/${taskId}/retry`, { method: 'POST' })
      onRetried()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal" aria-label="任务详情">
        <header className="modal-header">
          <div><span className="eyebrow">DELIVERY DETAIL</span><h2>投递详情</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭"><Icon name="close" /></button>
        </header>
        {loading && <div className="modal-state">正在读取投递记录…</div>}
        {error && <div className="alert">{error}</div>}
        {task && (
          <div className="modal-content">
            <div className="detail-lead">
              <StatusBadge value={task.status} />
              <code>{task.id}</code>
              {(task.status === 'DEAD' || task.status === 'RETRY_WAIT') && (
                <button className="primary-button" onClick={retry} disabled={retrying}>
                  <Icon name="retry" />{retrying ? '提交中…' : '立即重试'}
                </button>
              )}
            </div>
            <dl className="facts">
              <div><dt>渠道</dt><dd>{task.channel}</dd></div>
              <div><dt>幂等键</dt><dd>{task.idempotency_key}</dd></div>
              <div className="span-2"><dt>目标地址</dt><dd className="breakable">{task.request_method} {task.target_url}</dd></div>
              <div><dt>投递次数</dt><dd>{task.total_attempts} 次（本轮已重试 {Math.max(0, task.current_attempt - 1)}/{Math.max(0, task.max_attempts - 1)}）</dd></div>
              <div><dt>创建时间</dt><dd>{formatTime(task.created_at)}</dd></div>
              <div><dt>下次重试</dt><dd>{task.status === 'RETRY_WAIT' ? formatTime(task.next_retry_at) : '—'}</dd></div>
              <div><dt>最近状态码</dt><dd>{task.last_http_status ?? '—'}</dd></div>
              <div className="span-2"><dt>最近错误</dt><dd className="error-text">{task.last_error || '—'}</dd></div>
            </dl>
            <div className="detail-columns"><JsonBlock title="请求 Body" value={task.request_body} /><JsonBlock title="原始业务变量" value={task.variables} /></div>
            <section className="detail-section">
              <h3>投递历史 <span>{task.attempts.length}</span></h3>
              {task.attempts.length === 0 ? <div className="empty-inline">尚未执行投递</div> : (
                <div className="timeline">
                  {[...task.attempts].reverse().map((attempt) => (
                    <article className="attempt" key={attempt.id}>
                      <div className={`attempt-dot attempt-dot--${attempt.outcome.toLowerCase()}`} />
                      <div className="attempt-main">
                        <div className="attempt-head">
                          <strong>第 {attempt.attempt_number} 次 · {STATUS[attempt.outcome]?.label || attempt.outcome}</strong>
                          <span>{formatTime(attempt.finished_at)} · {attempt.duration_ms} ms</span>
                        </div>
                        <div className="attempt-meta">HTTP {attempt.http_status ?? '—'} · {attempt.error || '请求成功'}</div>
                        {attempt.response_excerpt && <pre>{attempt.response_excerpt}</pre>}
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </section>
    </div>
  )
}

function TestDeliveryPage() {
  const [method, setMethod] = useState('POST')
  const [url, setUrl] = useState('http://127.0.0.1:9000/webhook/test')
  const [body, setBody] = useState('{\n  "event_type": "order-paid",\n  "event_id": "evt-local-test",\n  "data": {\n    "order_id": "100"\n  }\n}')
  const [timeout, setTimeoutValue] = useState(5)
  const [maxRetries, setMaxRetries] = useState(10)
  const [task, setTask] = useState(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)

  const send = async (event) => {
    event.preventDefault()
    setError('')
    setTask(null)
    let parsedBody
    try {
      parsedBody = JSON.parse(body)
    } catch (err) {
      setError(`JSON 格式错误：${err.message}`)
      return
    }
    setSending(true)
    try {
      const data = await api('/api/v1/test-deliveries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          method,
          url: url.trim(),
          headers: {},
          body: parsedBody,
          timeout_seconds: Number(timeout),
          max_retries: Number(maxRetries),
        }),
      })
      setTask({ id: data.id, status: data.status, attempts: [] })
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  useEffect(() => {
    if (!task?.id || ['SUCCEEDED', 'DEAD'].includes(task.status)) return undefined
    let active = true
    const poll = async () => {
      try {
        const latest = await api(`/api/v1/tasks/${task.id}`)
        if (active) setTask(latest)
      } catch (err) {
        if (active) setError(err.message)
      }
    }
    poll()
    const timer = window.setInterval(poll, 1000)
    return () => { active = false; window.clearInterval(timer) }
  }, [task?.id, task?.status])

  const latestAttempt = task?.attempts?.length ? task.attempts[task.attempts.length - 1] : null

  return (
    <>
      <PageHeading eyebrow="TOOLS / REQUEST LAB" title="通知测试" description="构造测试请求并放入真实投递队列，验证 Worker 消费与下游响应。" />
      <div className="test-grid">
        <form className="panel request-card" onSubmit={send}>
          <PanelTitle index="01" title="构造请求" hint="仅允许白名单内的目标主机" />
          <label className="field-label">请求地址</label>
          <div className="request-line">
            <select value={method} onChange={(event) => setMethod(event.target.value)}>
              <option>POST</option><option>PUT</option><option>PATCH</option>
            </select>
            <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="http://127.0.0.1:9000/webhook/test" required />
          </div>
          <div className="editor-heading"><label htmlFor="body-editor">Body · JSON</label><span>支持对象、数组和基础类型</span></div>
          <textarea id="body-editor" className="body-editor" spellCheck="false" value={body} onChange={(event) => setBody(event.target.value)} />
          <div className="send-row">
            <div className="test-options">
              <label>超时 <input type="number" min="0.1" max="30" step="0.1" value={timeout} onChange={(event) => setTimeoutValue(event.target.value)} /> 秒</label>
              <label>最大重试 <input type="number" min="0" max="10" value={maxRetries} onChange={(event) => setMaxRetries(event.target.value)} /> 次</label>
            </div>
            <button className="primary-button send-button" disabled={sending}>{sending ? '入队中…' : '加入投递队列'}</button>
          </div>
          {error && <div className="alert">{error}</div>}
        </form>

        <section className="panel response-card">
          <PanelTitle index="02" title="队列执行结果" hint={latestAttempt ? `${latestAttempt.duration_ms} ms` : '每秒自动刷新'} />
          {!task ? (
            <div className="response-placeholder"><div><Icon name="delivery" /></div><h3>等待测试任务</h3><p>提交后请求会先进入任务表，再由多 Worker 抢占、投递并回写结果。</p></div>
          ) : (
            <div className="response-result">
              <div className={`result-banner result-banner--${task.status === 'SUCCEEDED' ? 'success' : task.status === 'DEAD' ? 'danger' : 'waiting'}`}>
                <strong><StatusBadge value={task.status} /></strong>
                <span>{latestAttempt?.http_status ? `HTTP ${latestAttempt.http_status}` : `任务 ${task.id.slice(0, 8)}`}</span>
              </div>
              {!latestAttempt && <div className="queue-hint"><i /><div><strong>已进入投递队列</strong><span>等待 Worker 领取任务，页面每秒自动刷新。</span></div></div>}
              {latestAttempt?.error && <div className="alert response-alert">{latestAttempt.error}</div>}
              {latestAttempt && <section className="detail-section"><h3>响应 Body</h3><pre>{latestAttempt.response_excerpt || '（空响应）'}</pre></section>}
              {task.attempts?.length > 1 && <section className="detail-section"><h3>尝试次数</h3><p className="attempt-count">已完成 {task.attempts.length} 次投递，本轮最多重试 {Math.max(0, task.max_attempts - 1)} 次。</p></section>}
            </div>
          )}
        </section>
      </div>
    </>
  )
}

function SettingsPage() {
  const [settings, setSettings] = useState(null)
  const [workerProcesses, setWorkerProcesses] = useState(2)
  const [maxRetries, setMaxRetries] = useState(10)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      const data = await api('/api/v1/settings/workers')
      setSettings(data)
      setWorkerProcesses(data.worker_processes)
      setMaxRetries(data.max_delivery_retries)
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const data = await api('/api/v1/settings/workers', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          worker_processes: Number(workerProcesses),
          max_delivery_retries: Number(maxRetries),
        }),
      })
      setSettings(data)
      setWorkerProcesses(data.worker_processes)
      setMaxRetries(data.max_delivery_retries)
      setMessage('设置已保存，Worker 管理进程将在数秒内完成调整。')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <PageHeading eyebrow="SYSTEM / CAPACITY" title="运行设置" description="按当前流量与供应商容量动态调整消费者规模。" />
      <section className="panel settings-card">
        <PanelTitle index="01" title="Worker 消费配置" hint="约 2 秒内生效" />
        {error && <div className="alert settings-alert">{error}</div>}
        <form onSubmit={save}>
          <div className="setting-row">
            <div><label htmlFor="worker-processes">目标 Worker 进程数</label><p>每个进程都是独立消费者，共享 MySQL 并通过行锁避免重复领取。</p></div>
            <div className="number-control">
              <button type="button" onClick={() => setWorkerProcesses((value) => Math.max(1, Number(value) - 1))}>−</button>
              <input id="worker-processes" type="number" min="1" max={settings?.max_worker_processes || 10} value={workerProcesses} onChange={(event) => setWorkerProcesses(event.target.value)} />
              <button type="button" onClick={() => setWorkerProcesses((value) => Math.min(settings?.max_worker_processes || 10, Number(value) + 1))}>+</button>
            </div>
          </div>
          <div className="setting-row">
            <div><label htmlFor="max-retries">最大重试次数</label><p>用于限制新提交任务的自动重试次数，渠道自身配置更低时采用较小值。</p></div>
            <div className="number-control">
              <button type="button" onClick={() => setMaxRetries((value) => Math.max(0, Number(value) - 1))}>−</button>
              <input id="max-retries" type="number" min="0" max="10" value={maxRetries} onChange={(event) => setMaxRetries(event.target.value)} />
              <button type="button" onClick={() => setMaxRetries((value) => Math.min(10, Number(value) + 1))}>+</button>
            </div>
          </div>
          <div className="capacity-grid">
            <div><span>单进程并发</span><strong>{settings?.per_process_concurrency ?? '—'}</strong></div>
            <div><span>理论总并发</span><strong>{settings ? Number(workerProcesses || 0) * settings.per_process_concurrency : '—'}</strong></div>
            <div><span>当前重试上限</span><strong>{maxRetries}</strong></div>
          </div>
          <div className="settings-footer">
            <p>减少进程数时，正在执行的任务会先完成当前批次；异常中断的任务会在租约到期后重新领取。</p>
            <button className="primary-button" disabled={saving}>{saving ? '保存中…' : '保存设置'}</button>
          </div>
          {message && <div className="success-message"><Icon name="check" />{message}</div>}
        </form>
      </section>
    </>
  )
}

function DeliveryPage({ onSelect }) {
  const [tasks, setTasks] = useState([])
  const [summary, setSummary] = useState(null)
  const [channels, setChannels] = useState([])
  const [filter, setFilter] = useState('failed')
  const [channel, setChannel] = useState('')
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const params = useMemo(() => {
    const search = new URLSearchParams()
    FILTERS.find((item) => item.value === filter).statuses.forEach((value) => search.append('status', value))
    if (channel) search.set('channel', channel)
    if (submittedQuery) search.set('q', submittedQuery)
    search.set('page_size', '50')
    return search.toString()
  }, [filter, channel, submittedQuery])

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      const [taskData, summaryData] = await Promise.all([api(`/api/v1/tasks?${params}`), api('/api/v1/dashboard/summary')])
      setTasks(taskData.items)
      setTotal(taskData.total)
      setSummary(summaryData)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [params])

  useEffect(() => { api('/api/v1/channels').then((data) => setChannels(data.items)).catch(() => {}) }, [])
  useEffect(() => { refresh(); const timer = window.setInterval(() => refresh(true), 15000); return () => window.clearInterval(timer) }, [refresh])

  const abnormal = (summary?.retry_wait || 0) + (summary?.dead || 0)
  return <>
    <PageHeading eyebrow="DELIVERY MANAGEMENT" title="失败投递" description="监控外部 HTTP 通知状态，快速定位失败请求并进行恢复处理。" action={<button className="secondary-button" onClick={() => refresh()} disabled={loading}><Icon name="refresh" />{loading ? '刷新中' : '刷新数据'}</button>} />
    <section className="summary-grid">
      <SummaryCard label="当前异常" value={summary ? abnormal : '—'} hint="需要关注的任务" tone="red" icon="alert" />
      <SummaryCard label="等待重试" value={summary?.retry_wait ?? '—'} hint="系统将自动处理" tone="amber" icon="retry" />
      <SummaryCard label="正在投递" value={summary?.processing ?? '—'} hint="Worker 正在消费" tone="blue" icon="activity" />
      <SummaryCard label="24h 成功率" value={summary?.success_rate_24h == null ? '—' : `${summary.success_rate_24h}%`} hint={`${summary?.failed_attempts_24h ?? 0} 次失败尝试`} tone="green" icon="check" />
    </section>
    <section className="panel records-panel">
      <div className="records-toolbar">
        <div className="filter-tabs">{FILTERS.map((item) => <button key={item.value} className={filter === item.value ? 'active' : ''} onClick={() => setFilter(item.value)}>{item.label}</button>)}</div>
        <div className="toolbar-actions"><select value={channel} onChange={(event) => setChannel(event.target.value)}><option value="">全部渠道</option>{channels.map((item) => <option value={item} key={item}>{item}</option>)}</select><form className="search" onSubmit={(event) => { event.preventDefault(); setSubmittedQuery(query.trim()) }}><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索任务 ID / URL / 错误" /></form></div>
      </div>
      {error && <div className="alert">无法加载数据：{error}</div>}
      <div className="table-wrap"><table><thead><tr><th>状态</th><th>目标 URL</th><th>请求方法</th><th>失败原因</th><th>重试次数</th><th>最后更新时间</th><th>操作</th></tr></thead><tbody>{tasks.map((task) => <tr key={task.id}><td><StatusBadge value={task.status} /></td><td><div className="url-cell"><strong className="clamp-2" title={task.target_url}>{task.target_url}</strong><span>{task.channel}{task.is_test && <em>测试</em>}</span></div></td><td><span className={`method-tag method-tag--${task.request_method.toLowerCase()}`}>{task.request_method}</span></td><td><span className="failure-reason clamp-2" title={task.last_error || ''}>{task.last_error || '—'}</span>{task.last_http_status && <small>HTTP {task.last_http_status}</small>}</td><td><strong className="attempt-number">{Math.max(0, task.total_attempts - 1)}</strong><span className="attempt-limit"> / {Math.max(0, task.max_attempts - 1)}</span></td><td><span className="time-cell">{formatTime(task.updated_at)}</span>{task.status === 'RETRY_WAIT' && <small>下次 {formatTime(task.next_retry_at)}</small>}</td><td><button className="text-button" onClick={() => onSelect(task.id)}>查看详情 <Icon name="chevron" /></button></td></tr>)}</tbody></table>{!loading && tasks.length === 0 && <div className="empty-state"><div><Icon name="check" /></div><h3>当前筛选下没有投递记录</h3><p>新的异常请求会自动出现在这里。</p></div>}{loading && tasks.length === 0 && <div className="empty-state"><div className="spinner" /><h3>正在加载投递记录</h3></div>}</div>
      <footer className="table-footer">共 {total} 条记录</footer>
    </section>
  </>
}

function App() {
  const [view, setView] = useState(() => { const requested = new URLSearchParams(window.location.search).get('view'); return ['test', 'settings'].includes(requested) ? requested : 'failures' })
  const [selectedId, setSelectedId] = useState(() => new URLSearchParams(window.location.search).get('task'))
  const changeView = (next) => { setView(next); setSelectedId(null) }

  return (
    <div className="app-shell">
      <Sidebar view={view} onChange={changeView} />
      <div className="main-shell"><Topbar view={view} /><main>{view === 'test' ? <TestDeliveryPage /> : view === 'settings' ? <SettingsPage /> : <DeliveryPage onSelect={setSelectedId} />}</main></div>
      {selectedId && <TaskDetailPanel taskId={selectedId} onClose={() => setSelectedId(null)} onRetried={() => setSelectedId(null)} />}
    </div>
  )
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)

